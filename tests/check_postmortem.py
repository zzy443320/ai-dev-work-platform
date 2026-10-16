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


def main():
    t_classes()
    t_classify()
    t_mixed()
    t_robust()
    t_ledger()
    t_real_records()
    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
