# -*- coding: utf-8 -*-
"""失败归类器的用例：每条判据都要能指回记录里的原始字段。

为什么单独测这个：归类器的价值全在「判据要准」——判错一次，人就会重新回去人肉复盘，
机制就废了。所以这里用**合成记录**逐类钉死判据（每条记录都只带一个类的特征），再用真实的
proposals/ 做一次全覆盖回归。

用法：python tests/check_postmortem.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.postmortem import CLASSES, classify, report_lines, summarize  # noqa: E402

_PASS = 0
_FAIL = 0


def ok(cond, label, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [ok]   {label}" + (f" — {str(detail)[:110]}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {str(detail)[:200]}" if detail else ""))


# 每条合成记录只带**一个类**的特征，判据互相不串门
SAMPLES = [
    ("shipped", {"status": "applied", "defect": {"number": 1, "title": "t"}}),
    ("human_rejected", {"status": "rejected", "decision": {"reason": "改法不对，应该是后端改"}}),
    ("ai_unavailable", {"status": "invalid", "analysis": {"ai_error": "Connection reset by peer"}}),
    ("patch_ready", {"status": "pending",
                     "patch": {"ok": True, "errors": [], "files": ["a.ts"],
                               "blocks": [{"file_path": "a.ts", "search": "x", "replace": "y"}]},
                     "gate": {"ok": True, "level": "sandbox"}}),
    ("patch_broken_syntax", {"status": "gate_failed",
                             "gate": {"ok": False, "failed_checks": ["balance:src/a.vue"]}}),
    ("gate_baseline_red", {"status": "gate_failed",
                           "gate": {"ok": False, "failed_checks": ["typecheck"]},
                           "agent": {"baseline": {"typecheck": {"ok": False}}}}),
    ("gate_red", {"status": "gate_failed",
                  "gate": {"ok": False, "failed_checks": ["typecheck"]},
                  "agent": {"baseline": {"typecheck": {"ok": True}}}}),
    ("path_not_found", {"status": "invalid",
                        "patch": {"ok": False, "blocks": [],
                                  "errors": ["文件不存在: packages/ai/lui/pages/x.vue"]},
                        "agent": {"conclusion": "not_converged", "trace": []}}),
    ("tool_misled", {"status": "invalid",
                     "patch": {"ok": False, "errors": ["glob **/*.{ts,vue} 没匹配到任何文件"]},
                     "analysis": {"parse_errors": []}}),
    ("locate_miss", {"status": "invalid",
                     "analysis": {"read_files": ["a.vue", "b.vue"],
                                  "parse_errors": ["第一轮窗口不足，已按模型提示补充定位"]},
                     "patch": {"ok": False, "errors": []}}),
    ("stall_or_budget", {"status": "invalid",
                         "agent": {"conclusion": "budget_exhausted", "rounds": 7,
                                   "max_rounds": 7, "stall_counts": {"unbuilt:x": 2}},
                         "patch": {"ok": False, "errors": []}}),
    ("search_miss", {"status": "invalid",
                     "patch": {"ok": False, "errors": ["SEARCH 段未能匹配到原文"]}}),
    ("replies_ignored", {"status": "invalid",
                         "patch": {"ok": False, "errors": []},
                         "agent": {"conclusion": "not_converged", "rounds": 7,
                                   "max_rounds": 7,
                                   "trace": [
                                       {"round": 1, "kind": "reply", "chars": 200,
                                        "preview": "先读关键嫌疑文件。\n"
                                                   '{"action":"call","tool":"repo_read",'
                                                   '"arguments":{"path":"a.vue"}}'},
                                       {"round": 1, "kind": "noop"}]}}),
    ("rounds_starved", {"status": "invalid",
                        "patch": {"ok": False, "errors": []},
                        "agent": {"conclusion": "not_converged", "rounds": 7,
                                  "max_rounds": 7, "attempts": [],
                                  "trace": [{"round": 1, "kind": "tool", "tool": "repo_grep"},
                                            {"round": 2, "kind": "tool", "tool": "repo_read"},
                                            {"round": 3, "kind": "tool", "tool": "repo_read"},
                                            {"round": 4, "kind": "tool", "tool": "repo_read"}]}}),
    ("model_no_patch", {"status": "invalid", "agent": {"conclusion": "no_patch"},
                        "analysis": {"parse_errors": ["模型返回了分析但未给出 SEARCH/REPLACE 补丁块"]},
                        "patch": {"ok": False, "errors": []}}),
]

# 同时命中两类时，主类要取「更该先处理的那个」，次要项不能丢
MIXED = {
    "status": "invalid",
    "patch": {"ok": False, "errors": ["文件不存在: packages/ai/lui/pages/x.vue"]},
    "analysis": {"read_files": ["a.vue"], "parse_errors": ["嫌疑窗口不足"]},
    "agent": {"conclusion": "not_converged", "rounds": 7, "max_rounds": 7},
}


def t_classes():
    print("[1] 每个类的元数据都可读（报告、下一步、护栏）")
    for key, info in CLASSES.items():
        ok(bool(info.get("label")) and bool(info.get("why")), f"{key} 有标签与成因")
        if key not in ("shipped", "patch_ready"):
            ok(bool(info.get("next")), f"{key} 给了下一步动作")
    bad = [k for k, v in CLASSES.items()
           if v.get("mechanism") and not v.get("case")]
    print(f"     （有机制但还没挂用例的类：{bad or '无'}）")


def t_classify():
    print("\n[2] 合成记录逐类判准")
    for expect, rec in SAMPLES:
        got = classify(rec)
        ok(got["class"] == expect, f"{expect} → {got['class']}",
           str(got.get("evidence", []))[:110])
        ok(bool(got.get("evidence")), f"{expect} 带了证据（不是空口分类）")


def t_mixed():
    print("\n[3] 同时命中多类：主类 + 次要信号")
    got = classify(MIXED)
    ok(got["class"] == "path_not_found", "主类取可立即行动的那个", got["class"])
    ok("locate_miss" in got["also"] or "stall_or_budget" in got["also"],
       "次要信号被保留", str(got["also"]))


def t_robust():
    print("\n[4] 烂记录不许炸")
    for rec in ({}, {"status": "invalid"}, {"patch": {"errors": None}, "analysis": None}):
        got = classify(rec)
        ok(got["class"] in CLASSES, f"空/残缺记录也能归类（{got['class']}）")


def t_ledger():
    print("\n[5] 账本：只有没见过的类才算「新类」")
    tmp = Path(tempfile.mkdtemp(prefix="postmortem-ledger-")) / "seen.json"
    recs = [{"status": "invalid", "analysis": {"ai_error": "timeout"}}]
    s1 = summarize(recs, ledger_path=tmp)
    ok("ai_unavailable" in s1["fresh"], "第一次出现记为新类", str(s1["fresh"]))
    ok(bool(s1.get("first_ever")), "并说明这是首次登记（避免误读成一堆新问题）")
    s2 = summarize(recs, ledger_path=tmp)
    ok(s2["fresh"] == [] and not s2.get("first_ever"),
       "第二次不再报（同一类不重复打扰人）", str(s2["fresh"]))
    ok(json.loads(tmp.read_text(encoding="utf-8"))["first_seen"].get("ai_unavailable"),
       "账本落盘了首次日期")
    lines = report_lines(s2)
    ok(any("已知类" in ln for ln in lines), "报告明确说「全是已知类」",
       [ln for ln in lines if "已知类" in ln][:1])
    s3 = summarize([{"status": "gate_failed",
                     "gate": {"failed_checks": ["balance:src/a.vue"]}}], ledger_path=tmp)
    ok("patch_broken_syntax" in s3["fresh"], "之后出现的新类照样报出来", str(s3["fresh"]))


def t_real_records():
    print("\n[6] 真实 proposals/ 全覆盖")
    root = Path(__file__).resolve().parents[1] / "proposals"
    files = sorted(root.glob("*.json")) if root.is_dir() else []
    if not files:
        print("  （没有 proposals/，跳过）")
        return
    recs = []
    for p in files:
        try:
            recs.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            ok(False, f"{p.name} 不是合法 JSON")
    summary = summarize(recs)
    unknown = [it for it in summary["items"] if it["class"] not in CLASSES]
    ok(not unknown, f"{len(recs)} 条全部归入已知类", str(unknown[:2]))
    no_ev = [it["id"] for it in summary["items"] if not it["evidence"]]
    ok(not no_ev, "每条都带证据", str(no_ev[:2]))
    print(f"     分布：{summary['counts']}")
    ok(sum(summary["counts"].values()) == len(recs), "计数与记录数一致")


def t_replies_ignored():
    """「动作没被接住」与「模型真的只写了散文」必须分开：前者是工具 bug，后者是模型行为。

    判据只认显式字段：trace 里有 `kind == "noop"`，**且**同一轮 `reply.preview` 里含
    `"action"`/`"tool"`。所以这里给一个只差一个字段的反例：同样是 noop，但回复里没有
    action JSON → 主类必须落回 stall_or_budget，不能被误报成解析问题。
    """
    print("\n[7] 空转轮次的判据：夹带动作 vs 真的只写了散文")
    got = classify({
        "status": "invalid", "patch": {"ok": False, "errors": []},
        "agent": {"conclusion": "not_converged", "rounds": 7, "max_rounds": 7,
                  "trace": [
                      {"round": 1, "kind": "reply", "preview": "查一下。\n"
                       '{"action":"call","tool":"repo_grep"}'},
                      {"round": 1, "kind": "tool", "tool": "repo_grep"},
                      # 第 2 轮：正文在前、动作在后 —— 就是这种被整轮漏掉
                      {"round": 2, "kind": "reply", "chars": 210,
                       "preview": "先读关键嫌疑文件。\n"
                                  '{"action":"call","tool":"repo_read",'
                                  '"arguments":{"path":"a.vue"}}'},
                      {"round": 2, "kind": "noop"}]}})
    ok(got["class"] == "replies_ignored", "夹带动作的空转轮 → 主类是「动作没被接住」",
       got["class"])
    ok(any("第 2 轮" in str(e) for e in got["evidence"]),
       "证据只点名真正被漏掉的那一轮（第 1 轮执行过了，不该报）",
       str(got["evidence"])[:160])
    ok("stall_or_budget" in got["also"], "同时保留「预算被烧」这个次要信号",
       str(got["also"]))
    plain = classify({
        "status": "invalid", "patch": {"ok": False, "errors": []},
        "agent": {"conclusion": "not_converged", "rounds": 7, "max_rounds": 7,
                  "trace": [{"round": 1, "kind": "reply", "preview": "我想想。"},
                            {"round": 1, "kind": "noop"}]}})
    ok(plain["class"] == "stall_or_budget",
       "纯散文的空转轮不许误报成解析问题（反例）", plain["class"])
    out = classify({"status": "invalid",
                    "patch": {"ok": True, "errors": [], "files": ["a.ts"],
                              "blocks": [{"file_path": "a.ts", "search": "x",
                                          "replace": "y"}]},
                    "gate": {"ok": True, "level": "sandbox"},
                    "agent": {"conclusion": "no_patch", "trace": [
                        {"round": 1, "kind": "reply", "preview": '{"action":"call"}'},
                        {"round": 1, "kind": "noop"}]}})
    ok(out["class"] != "replies_ignored", "已经有补丁时不报这个类（它只解释「没补丁」）",
       out["class"])


def t_rounds_starved():
    """「预算不够」要和「模型不会修 / 原地打转」分开：前者调个轮次预算就好。

    判据全用显式字段（conclusion / attempts / rounds vs max_rounds / trace 里的 tool
    条数 / stall_counts），不猜措辞。所以这里逐条给反例：只要有一个字段不满足，就必须
    退回 stall_or_budget —— 宁可少报，也不能把「模型认输」误诊成「加预算就能好」。
    """
    print("\n[8] 轮次预算耗尽：缺预算 vs 不收敛")
    base = {"status": "invalid", "patch": {"ok": False, "errors": []},
            "agent": {"conclusion": "not_converged", "rounds": 7, "max_rounds": 7,
                      "attempts": [],
                      "trace": [{"round": i, "kind": "tool", "tool": "repo_read"}
                                for i in range(1, 5)]}}
    got = classify(base)
    ok(got["class"] == "rounds_starved", "顶格用尽 + 只查代码 + 0 补丁 → rounds_starved",
       got["class"])
    ev = " ".join(str(e) for e in got["evidence"])
    ok("7/7" in ev, "证据里带上轮次实况（rounds/max_rounds）", ev[:120])
    ok("4 次工具执行" in ev, "证据里说明轮次都花在哪（工具执行次数）", ev[:120])
    ok("stall_or_budget" in got["also"], "次要信号仍保留", str(got["also"]))

    import copy
    def variant(**patch):
        r = copy.deepcopy(base)
        r["agent"].update(patch)
        return r

    ok(classify(variant(attempts=[{"conclusion": "x"}]))["class"] != "rounds_starved",
       "交过补丁的不算（那叫不收敛，不叫没轮到）")
    ok(classify(variant(rounds=3))["class"] != "rounds_starved",
       "没顶格就收工的不算（自己早退，不是预算不够）")
    ok(classify(variant(trace=[{"round": 1, "kind": "tool", "tool": "repo_read"}]))["class"]
       != "rounds_starved", "没在查代码的不算（给加预算也没用）")
    stall = variant(stall_counts={"unbuilt:x": 3})
    ok(classify(stall)["class"] == "stall_or_budget",
       "已在原地打转的归 stall_or_budget（有重复签名就让位）",
       classify(stall)["class"])
    withpatch = {"status": "invalid",
                 "patch": {"ok": True, "errors": [], "files": ["a.ts"],
                           "blocks": [{"file_path": "a.ts", "search": "x",
                                       "replace": "y"}]},
                 "gate": {"ok": True, "level": "sandbox"},
                 "agent": base["agent"]}
    ok(classify(withpatch)["class"] != "rounds_starved",
       "已经有补丁的不报这个类（它只解释「没产出补丁」）")


def main():
    t_classes()
    t_classify()
    t_mixed()
    t_robust()
    t_ledger()
    t_real_records()
    t_replies_ignored()
    t_rounds_starved()
    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
