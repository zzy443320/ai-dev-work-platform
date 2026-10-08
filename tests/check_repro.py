# -*- coding: utf-8 -*-
"""复现环节（先跑红再修）的行为用例。

为什么单独一个文件：复现环节的可信度全在几个判定上——跑器探测的顺序、「红」与
「跑不起来」的区别、「现在就绿必须打回」、以及「禁止改用例凑绿」。这几条一旦退化，
`verified` 就会重新变成模型自说自话，而界面上完全看不出来。

用例不打真实模型：用一个可编排的假 AI 钉死每条分支（同 check_fix_agent.py 的理由）。
复现命令走配置里的 `repro_command`，指向 sys.executable 跑一个 python 断言脚本——
这样既不必依赖 node/vitest，也能顺便验到「带引号的绝对路径命令能过白名单」。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_fix_agent as F  # noqa: E402  复用临时仓库、假模型、补丁回复等夹具
from scripts import repro as R  # noqa: E402
from scripts.fix_agent import (CONCLUSION_STALLED, CONCLUSION_VERIFIED,  # noqa: E402
                               VIA_REPRO_ASSERT, VIA_REPRO_TEST, FixAgent)

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


PY = f'"{Path(sys.executable).as_posix()}"'

# 针对「现象」的断言：源码里还留着 BUG 就 exit 1（合格的红）
REPRO_GOOD = (
    "import pathlib, sys\n"
    "src = pathlib.Path('src/a.js').read_text(encoding='utf-8')\n"
    "assert \"label = 'OK'\" in src, '现象仍在：label 没变成 OK'\n"
)
# 什么都不测的用例（永远绿）——必须被硬闸门打回
REPRO_USELESS = "import sys\nsys.exit(0)\n"


def repro_agent(repo, replies, **cfg):
    conf = {"max_rounds": 6, "deadline_seconds": 120, "max_stall": 2,
            "per_command_timeout": 60,
            "repro": {"enabled": "on", "command": f"{PY} {{file}}", "strength": "test"}}
    conf.update(cfg)
    ai = F.FakeAI(replies)
    agent = FixAgent(str(repo), ai, agent_cfg=conf,
                     gate_cfg={"commands": [{"name": "check", "cmd": F.CHECK_CMD}]})
    return agent, ai


def add_repro_reply(content, path="spec/defect.spec.py"):
    return json.dumps({"action": "call", "tool": "repro_add",
                       "arguments": {"path": path, "content": content}},
                      ensure_ascii=False)


# ------------------------------------------------------------------ 探测与判定
def _fake_bin_repo(names):
    """造一个只有 node_modules/.bin/<name> 的空壳仓库（探测只看这些文件在不在）。"""
    repo = F._tempdir("repro-bin-")
    (repo / "node_modules" / ".bin").mkdir(parents=True)
    for n in names:
        (repo / "node_modules" / ".bin" / n).write_text("", encoding="utf-8")
    return repo


def t_detect():
    print("\n[1] 跑器探测顺序与强度标注")
    h = R.detect_harness(_fake_bin_repo([]), {"command": "npm run test:unit -- {file}",
                                             "strength": "test"})
    check("显式配置优先于一切探测", h.kind == "command"
          and h.strength == R.STRENGTH_TEST and h.usable, str(h.to_dict())[:150])
    check("render_cmd 替换 {file}", R.render_cmd(h, "a/b.spec.ts")
          == "npm run test:unit -- a/b.spec.ts")

    h2 = R.detect_harness(_fake_bin_repo(["vitest", "jest", "eslint"]), {})
    check("有 vitest 就用 vitest（单测级证据）", h2.kind == "vitest"
          and h2.strength == R.STRENGTH_TEST, h2.name)
    check("vitest 命令必须显式 run（否则可能进 watch 挂死）",
          "run" in h2.cmd_tpl, h2.cmd_tpl)
    check("render_cmd 没写 {file} 时自动追加",
          R.render_cmd(R.Harness(kind="x", name="x", cmd_tpl="npx vitest run"),
                       "a.spec.ts") == "npx vitest run a.spec.ts")

    # 只有 npm script 里提到 vitest（.bin 里没有）：走 npm run 并标出透传风险
    pkg = F._tempdir("repro-pkg-")
    (pkg / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"test:unit": "vitest run"}}), encoding="utf-8")
    h3 = R.detect_harness(pkg, {})
    check("从 package.json 脚本名认出跑器并走 npm run",
          "npm run test:unit" in h3.cmd_tpl and h3.strength == R.STRENGTH_TEST,
          f"{h3.kind} / {h3.cmd_tpl}")
    check("npm run 传文件参数有风险时明确说出来",
          any("agent.repro.command" in n for n in h3.notes), str(h3.notes)[:160])

    h4 = R.detect_harness(F._tempdir("repro-none-"), {})
    check("什么都没有时退回可执行断言（强度标 assert）",
          h4.strength == R.STRENGTH_ASSERT and h4.usable, f"{h4.kind} / {h4.name}")
    check("断言脚本退路里带「弱证据」提示", any("不能证明" in n for n in h4.notes),
          str(h4.notes)[:160])


def t_spec_rules():
    print("\n[2] 用例落点与三态判定")
    repo = F._tempdir("repro-spec-")
    (repo / "src").mkdir()
    (repo / "src" / "a.js").write_text("export const a = 1;\n", encoding="utf-8")
    h_test = R.Harness(kind="vitest", name="vitest", cmd_tpl="npx vitest run {file}",
                       strength=R.STRENGTH_TEST)
    ok, why = R.spec_ok(h_test, repo, "../outside.spec.ts")
    check("拒绝跳出仓库的路径", not ok and "仓库内" in why, why[:100])
    ok, why = R.spec_ok(h_test, repo, "src/a.js")
    check("拒绝覆盖仓库里已有的文件", not ok and "已经存在" in why, why[:100])
    ok, why = R.spec_ok(h_test, repo, "src/deep/new.spec.ts")
    check("新建目录里的 spec 允许", ok, why[:100])
    ok, why = R.spec_ok(h_test, repo, "src/deep/helper.ts")
    check("单测跑器下文件名不含 spec/test 会被拒（跑了个寂寞）",
          not ok and ".spec" in why, why[:120])
    h_assert = R.Harness(kind="assert-node", name="node 断言脚本",
                         cmd_tpl="node {file}", strength=R.STRENGTH_ASSERT)
    ok, _ = R.spec_ok(h_assert, repo, "spec/manual.cjs")
    check("断言脚本不强制 spec 命名", ok)

    check("非 0 + 业务断言失败 = red",
          R.classify_run(h_test, 1, "AssertionError: expected a to be b") == "red")
    check("非 0 + Cannot find module = broken（不算红）",
          R.classify_run(h_test, 1, "Error: Cannot find module './x'") == "broken")
    check("非 0 + No test files found = broken",
          R.classify_run(h_test, 1, "No test files found") == "broken")
    check("非 0 + 语法错误 = broken",
          R.classify_run(h_test, 2, "SyntaxError: Unexpected token") == "broken")
    check("返回码 0 = green", R.classify_run(h_test, 0, "1 passed") == "green")
    check("被白名单拒绝 = broken", R.classify_run(h_test, 0, "", denied=True) == "broken")
    check("broken 的修法提示按原因区分",
          "import" in R.broken_hint(h_test, "Cannot find module 'vue'")
          and "改名" in R.broken_hint(h_test, "No test files found"))


def t_red_first_gate():
    print("\n[3] 硬闸门：现在就绿的用例必须打回")
    repo = F.make_repo()
    before = F.snapshot(repo)
    agent, ai = repro_agent(repo, [
        add_repro_reply(REPRO_USELESS),      # 现在就绿 → 打回
        add_repro_reply(REPRO_GOOD),         # 跑红 → 判据成立
        F.patch_reply("OK"),                 # 修代码 → 用例转绿
    ])
    res = agent.run({"id": "R1", "title": "label 应为 OK"})
    gate1 = ai.prompts[1]["prompt"]
    check("第一次提交的打回理由说清了「未修复代码上就通过」",
          "未修复" in gate1 and "打回重写" in gate1, gate1[-500:][:180])
    check("打回同时给了怎么改的指引", "沿用同一个 path" in gate1 or "断言必须落在" in gate1)
    red_fb = ai.prompts[2]["prompt"]
    check("跑红后被明确承认为判据", "复现成功" in red_fb, red_fb[-600:][:180])
    check("一次无效提交被记在 rewrites 上", res["repro"]["rewrites"] == 1,
          str(res["repro"]["rewrites"]))
    check("最终结论 verified", res["conclusion"] == CONCLUSION_VERIFIED, res["conclusion"])
    check("证据来源标成 repro-test", res["verified_via"] == VIA_REPRO_TEST,
          res["verified_via"])
    check("复现用例进了每轮验证且转绿", res["repro"]["status"] == "green",
          res["repro"]["status"])
    check("用例没有被写进补丁（补丁只含业务改动）",
          "repro" not in (res["patch_text"] or "").lower()[:200]
          and "src/a.js" in " ".join(res["files"] or []), str(res["files"]))
    check("用户仓库零写入", F.snapshot(repo) == before)
    check("轮次预算为复现补了额度", res["max_rounds"] == 8, str(res["max_rounds"]))


def t_no_forever_green():
    print("\n[4] 一直复现不出来：重写次数用尽后如实标注，不假装做过")
    repo = F.make_repo()
    agent, ai = repro_agent(repo, [add_repro_reply(REPRO_USELESS)] * 5,
                            repro_max_rewrite=1)
    res = agent.run({"id": "R2", "title": "x"})
    check("复现状态 blocked", res["repro"]["status"] == "blocked", res["repro"]["status"])
    check("rewrite 次数被记录", res["repro"]["rewrites"] >= 2,
          str(res["repro"]["rewrites"]))
    check("模型被告知放弃复现环节", "复现环节到此为止" in ai.prompts[-1]["prompt"],
          ai.prompts[-1]["prompt"][-400:][:160])
    check("没建立判据时不会冒充 repro-verified",
          res.get("verified_via", "") != VIA_REPRO_TEST, str(res.get("verified_via")))


CHEAT_PATCH = (
    "把用例改掉就能永远绿了\n"
    "<<<<<<< SEARCH spec/defect.spec.py\n"
    "assert \"label = 'OK'\" in src, '现象仍在：label 没变成 OK'\n"
    "=======\n"
    "assert True\n"
    ">>>>>>> REPLACE\n"
)


def t_no_cheating():
    print("\n[5] 禁止改用例凑绿：补丁里含用例路径直接拒绝")
    repo = F.make_repo()
    agent, ai = repro_agent(repo, [
        add_repro_reply(REPRO_GOOD, path="spec/defect.spec.py"),
        CHEAT_PATCH, CHEAT_PATCH, CHEAT_PATCH, CHEAT_PATCH,
    ])
    res = agent.run({"id": "R3", "title": "x"})
    fb = ai.prompts[2]["prompt"] if len(ai.prompts) > 1 else ""
    check("改考卷的补丁被拒", "改它等于改考卷" in fb or "修改复现用例" in fb,
          fb[-600:][:200])
    check("判据用例本身没被改掉", "assert True" not in res["repro"]["content"],
          res["repro"]["content"][:120])
    check("结论不会因此变成 verified", res["conclusion"] != CONCLUSION_VERIFIED,
          res["conclusion"])
    check("同一判据被反复篡改会认输/止损，而不是空转到超时",
          res["conclusion"] in (CONCLUSION_STALLED, "budget_exhausted", "no_patch"),
          res["conclusion"])


def t_assert_strength_labelled():
    print("\n[6] 断言脚本级证据必须与单测级证据区分开")
    repo = F.make_repo()
    agent, _ai = repro_agent(
        repo, [add_repro_reply(REPRO_GOOD), F.patch_reply("OK")],
        repro={"enabled": "on", "command": f"{PY} {{file}}", "strength": "assert"})
    res = agent.run({"id": "R4", "title": "x"})
    check("结论 verified 但来源是 repro-assert",
          res["conclusion"] == CONCLUSION_VERIFIED
          and res["verified_via"] == VIA_REPRO_ASSERT,
          f"{res['conclusion']} / {res['verified_via']}")
    check("强度写进了 repro 元数据",
          res["repro"]["harness"]["strength"] == "assert", str(res["repro"]["harness"]))


def t_artifact_and_dedupe():
    print("\n[7] 复现用例挂成产出物，且重跑不堆重复件")
    repo = F.make_repo()
    import scripts.pipeline as _pl
    from fixture_defect import TEST_DEFECT
    _pl.SAMPLE_DEFECTS[:] = [TEST_DEFECT]
    cfg = {
        "ones": {"base_url": "", "token": "", "project_uuid": ""},
        "ai": {"model": "x", "api_key": ""},
        "repo": {"path": str(repo), "branch": "main"},
        "gate": {"commands": [{"name": "check", "cmd": F.CHECK_CMD}]},
        "agent": {"max_rounds": 6, "deadline_seconds": 120, "max_stall": 2,
                  "repro": {"enabled": "on", "command": f"{PY} {{file}}",
                            "strength": "test"}},
        "playwright": {"base_url": "http://127.0.0.1:1",
                       "screenshot_dir": str(F._tempdir("repro-shots-"))},
        "knowledge_base": {"output_dir": str(F._tempdir("repro-kb-"))},
        "proposals": {"output_dir": str(F._tempdir("repro-props-"))},
        "artifacts": {"output_dir": str(F._tempdir("repro-arts-"))},
    }
    pipe = _pl.AIDefectFixerPipeline(cfg)
    agent_res = {"repro": {"status": "green", "path": "spec/defect.spec.py",
                           "content": "assert 1 == 1\n",
                           "cmd": "npx vitest run spec/defect.spec.py",
                           "harness": {"name": "vitest", "strength_label": "单测跑器"}}}
    aid = pipe._repro_artifact({"id": "R5", "title": "t"}, agent_res)
    check("挂成了 repro 类型产出物", bool(aid), aid)
    a = pipe.artifacts.get(aid) or {}
    check("产出物是新建文件动作", (a.get("files") or [{}])[0].get("action") == "create",
          str(a.get("files"))[:150])
    check("标题带上工单号", "R5" in (a.get("title") or ""), a.get("title", ""))
    again = pipe._repro_artifact({"id": "R5", "title": "t"}, agent_res)
    check("同内容重跑不产生第二份", again == aid, f"{aid} vs {again}")
    changed = {"repro": dict(agent_res["repro"], content="assert 1 == 2\n")}
    third = pipe._repro_artifact({"id": "R5", "title": "t"}, changed)
    check("内容变了会挂新的那一份", third and third != aid, f"{aid} vs {third}")
    empty = pipe._repro_artifact({"id": "R6", "title": "t"},
                                 {"repro": {"status": "blocked"}})
    check("没能复现时不挂产出物（不污染列表）", empty == "", repr(empty))
    # 采纳路径：新建文件要能落盘，撤销要真把新建的文件收走
    r = pipe.artifacts.get(third)
    from scripts.artifact import ArtifactApplier

    ap = ArtifactApplier(str(repo), {})
    applied = ap.apply(r, force_gate=True)
    target = repo / r["files"][0]["path"]
    check("复现用例可被采纳进仓库", applied.get("ok") and target.is_file(),
          str(applied.get("error", ""))[:150])
    und = ap.undo({**r, "apply": applied})
    check("撤销后新建的用例文件不残留在仓库里",
          und.get("ok") and not target.exists(), str(und)[:150])


def main():
    print("== 复现环节（先跑红再修）行为用例 ==")
    t_detect()
    t_spec_rules()
    t_red_first_gate()
    t_no_forever_green()
    t_no_cheating()
    t_assert_strength_labelled()
    t_artifact_and_dedupe()
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
