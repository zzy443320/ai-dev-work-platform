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


def t_page_reverify_hook():
    """页面复验的四种回法都必须按证据强度落到结论里，尤其不能把「没看成」当成通过。"""
    repo0 = F.make_repo()

    def run_with_hook(hook_reply, replies=None):
        repo = F.make_repo()
        before = F.snapshot(repo)
        conf = {"max_rounds": 6, "deadline_seconds": 120, "max_stall": 9,
                "per_command_timeout": 60, "repro": "off",
                "sandbox_server_command": "npm run dev -- --port {port}"}
        ai = F.FakeAI(replies or [F.patch_reply("OK"), F.patch_reply("OK")])
        agent = FixAgent(str(repo), ai, agent_cfg=conf,
                         gate_cfg={"commands": [{"name": "check", "cmd": F.CHECK_CMD}]},
                         page_after_hook=lambda sb, budget_left=0: dict(hook_reply))
        return agent.run({"id": "P1", "title": "x"}), repo, before, ai

    print("\n[8] 页面复验：判读如何影响结论")
    res, repo, before, _ai = run_with_hook(
        {"status": "ok", "verdict": "gone", "evidence": "列表已渲染出数据",
         "url": "http://127.0.0.1:1"})
    check("命令绿 + 判读 gone → verified", res["conclusion"] == CONCLUSION_VERIFIED,
          res["conclusion"])
    check("页面复验结果存进了结论", res["page_after"]["verdict"] == "gone")
    check("只复验一次（不起第二次服务）", res["rounds"] == 1, str(res["rounds"]))
    check("仓库零写入", F.snapshot(repo) == before)

    res2, repo2, before2, ai2 = run_with_hook(
        {"status": "ok", "verdict": "same", "evidence": "页面仍然白屏"})
    check("命令全绿但现象仍在 → symptom_persists",
          res2["conclusion"] == "symptom_persists", res2["conclusion"])
    check("symptom_persists 需要人工", res2["needs_human"] is True)
    check("降级后循环没有提前收口", res2["rounds"] >= 2, str(res2["rounds"]))
    check("判读原文回喂给了模型，并明确要求别拿「测试都过了」当理由",
          "页面仍然白屏" in ai2.prompts[-1]["prompt"]
          and "现象仍然存在" in ai2.prompts[-1]["prompt"],
          ai2.prompts[-1]["prompt"][-400:][:180])
    check("仓库零写入（复验也不写）", F.snapshot(repo2) == before2)

    res3, _r3, _b3, _ai3 = run_with_hook(
        {"status": "unusable", "reason": "服务在 90s 内没有监听 51234"})
    check("沙箱页面起不来 → 不算复验也不谎称看过",
          res3["conclusion"] in (CONCLUSION_VERIFIED, "checks_pass")
          and res3["page_after"]["status"] == "unusable",
          f"{res3['conclusion']} / {res3['page_after']}")
    check("unusable 不降级为需人工（只是少一路证据）", res3["needs_human"] is False)

    res4, _r4, _b4, _ai4 = run_with_hook({"status": "skipped", "reason": "剩余预算不足"})
    check("预算不足跳过复验时，结论仍是命令级", res4["conclusion"] == CONCLUSION_VERIFIED,
          res4["conclusion"])
    check("跳过被原样记进结论（不当作看过）",
          res4["page_after"]["status"] == "skipped", str(res4["page_after"])[:120])
    check("证据缺席要在 notes 里说明，别让人以为页面看过了",
          any("页面复验未完成" in n for n in res4["notes"]), str(res4["notes"])[:200])


def t_precedents():
    """先例检索：相关度、自我回声、被拒绝的也带上、注入必须封顶。"""
    print("\n[9] 同类历史缺陷先例检索与注入封顶")
    from scripts.precedents import DEFAULT_MAX_CHARS, load_precedents, render_precedents

    d = F._tempdir("prec-dir-")

    def write_proposal(pid, *, defect_id, title, files, keywords, status,
                       decision=None, conclusion="", via="", repro_path="",
                       patch_text=""):
        import datetime

        (d / f"{pid}.json").write_text(json.dumps({
            "id": pid, "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "defect": {"id": defect_id, "title": title},
            "analysis": {"category": "逻辑", "root_cause": "缺了可选链",
                         "keywords": keywords, "patch_canonical": patch_text},
            "patch": {"changes": [{"file_path": f, "stats": {"added": 1, "removed": 1}}
                                  for f in files], "files": files},
            "decision": decision or {},
            "agent": {"conclusion": conclusion, "verified_via": via,
                      "repro": {"path": repro_path, "status": "green"}},
        }), encoding="utf-8")

    write_proposal("p-applied", defect_id="100001", title="流程列表 processItem 报 TypeError",
                   files=["packages/app/src/views/process-list.vue"],
                   keywords=["processItem", "TypeError"], status="applied",
                   decision={"approved": True, "note": "改法可以"},
                   conclusion="verified", via="repro-test",
                   repro_path="src/process-list.spec.ts",
                   patch_text="<<<<<<< SEARCH a\nb\n=======\nc\n>>>>>>> REPLACE")
    write_proposal("p-rejected", defect_id="100002", title="列表 startTime 显示异常",
                   files=["packages/app/src/views/process-list.vue"],
                   keywords=["startTime"], status="rejected",
                   decision={"approved": False, "note": "应该在 formatter 里改"})
    write_proposal("p-unrelated", defect_id="100003", title="登录页图标错位",
                   files=["packages/app/src/login/icon.css"], keywords=["icon"],
                   status="applied", patch_text="icon.png -> icon.svg")
    write_proposal("p-self", defect_id="900001", title="processItem 相关新问题",
                   files=["packages/app/src/views/process-list.vue"],
                   keywords=["processItem"], status="pending")

    defect = {"id": "900001", "title": "processItem 为 undefined 时列表崩溃",
              "description": "打开列表页 TypeError: Cannot read properties of undefined "
                             "(reading 'processItem')，startTime 也不对"}
    got = load_precedents(str(d), defect, limit=3)
    ids = [g["proposal_id"] for g in got]
    check("相关先例被挑中，无关的不进", "p-applied" in ids and "p-unrelated" not in ids,
          str(ids))
    check("被人工拒绝过的改法也一起给（避免重演）", "p-rejected" in ids, str(ids))
    check("本工单自己那份未采纳的提案不算先例（回声）", "p-self" not in ids, str(ids))
    check("先例带上结局与证据来源",
          got[0]["outcome"] == "applied" and got[0]["verified_via"] == "repro-test"
          and got[0]["repro_path"], str(got[0])[:200])
    check("人工备注被带上", any("formatter" in (g.get("decision_note") or "")
                            for g in got), str([g.get("decision_note") for g in got]))

    text = render_precedents(got)
    check("渲染出的先例文本含补丁原文", "SEARCH" in text, text[:120])
    big = [dict(g, patch_text=("x" * 4000)) for g in got]
    capped = render_precedents(big, max_chars=2500)
    check("注入文本严格封顶", len(capped) <= 2500, f"{len(capped)} vs 2500")
    check("超预算时先砍补丁正文而不是整条先例",
          "补丁原文因长度上限已省略" in capped and "人工拒绝" in capped, capped[:200])
    check("默认上限是个明确常量", DEFAULT_MAX_CHARS == 6000)
    check("目录不存在时返回空而不是抛", load_precedents(str(d / "nope"), defect) == [])
    check("limit=0 视为关闭", load_precedents(str(d), defect, limit=0) == [])


def t_sandbox_server():
    """沙箱里起服务：真起真停、端口不抢、起不来时如实报告、内联代码仍然拦得住。"""
    print("\n[10] 沙箱 dev server 起停与失败上报")
    from scripts.sandbox import default_server_command, port_open

    repo = F.make_repo()
    (repo / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"dev": "vite"}}), encoding="utf-8")
    cmd, _cwd = default_server_command(repo)
    check("vite 项目能探出 dev 命令且带 {port}", "vite" not in cmd and "{port}" in cmd
          and cmd.startswith("npm run dev"), cmd)
    (repo / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"dev": "some-weird-runner"}}), encoding="utf-8")
    check("不认识的技术栈不瞎猜命令", default_server_command(repo)[0] == "")

    sb = F.Sandbox(repo, cfg={"mode": "copy"}).open()
    try:
        ok_cmd = f'"{Path(sys.executable).as_posix()}" -m http.server {{port}}'
        h = sb.spawn_server(ok_cmd, ready_timeout=45)
        check("服务起得来并监听在空闲端口", h.ready and h.port > 0, h.note[:120])
        if h.ready:
            check("端口能真的连上", port_open("127.0.0.1", h.port))
            check("URL 指向 127.0.0.1", h.url.startswith("http://127.0.0.1:"), h.url)
        h.stop()
        check("stop 之后端口被释放", not port_open("127.0.0.1", h.port, 0.6))
        dead = sb.spawn_server(
            f'"{Path(sys.executable).as_posix()}" definitely-missing-script.js',
            ready_timeout=12)
        check("进程秒退时 ready=False 且带上退出原因",
              not dead.ready and "退出码" in dead.note, dead.note[:160])
        inline = sb.spawn_server(f'"{Path(sys.executable).as_posix()}" -c "print(1)"')
        check("起服务也过白名单（内联代码照样拒）",
              not inline.ready and "内联" in inline.note, inline.note[:120])
    finally:
        sb.close()


def t_pipeline_end_to_end():
    """②③接到流水线上：先例进了 prompt、页面复验走的是真沙箱、卡片把结论沉淀下来。"""
    print("\n[11] pipeline 端到端：先例注入 + 页面复验 + 卡片沉淀")
    import scripts.pipeline as _pl
    from fixture_defect import TEST_DEFECT

    repo = F.make_repo()
    (repo / "package.json").write_text(json.dumps(
        {"name": "app", "scripts": {"dev": "vite", "check": "x"}}), encoding="utf-8")
    before = F.snapshot(repo)
    props = F._tempdir("prec-props-")
    kb_dir = F._tempdir("prec-kb-")
    # 先例库：一条同样改 process-list 的历史提案，已被人工采纳
    (props / "seed.json").write_text(json.dumps({
        "id": "seed", "created": "2026-09-01T10:00:00", "status": "applied",
        "defect": {"id": "700001", "title": "processList 里 processItem 未判空崩溃"},
        "analysis": {"category": "数据", "root_cause": "缺可选链",
                     "keywords": ["processItem", "processList"],
                     "patch_canonical": "<<<<<<< SEARCH src/a.js\nold\n=======\nnew\n>>>>>>> REPLACE"},
        "patch": {"changes": [{"file_path": "src/a.js"}], "files": ["src/a.js"]},
        "decision": {"approved": True, "note": "改法认可"},
        "agent": {"conclusion": "verified", "verified_via": "repro-test"},
    }), encoding="utf-8")

    cfg = {
        "ones": {"base_url": "", "token": "", "project_uuid": ""},
        "ai": {"model": "x", "api_key": ""},
        "repo": {"path": str(repo), "branch": "main"},
        "gate": {"commands": [{"name": "check", "cmd": F.CHECK_CMD}]},
        "agent": {"max_rounds": 5, "deadline_seconds": 150, "max_stall": 2,
                  "repro": "off", "precedents": 3,
                  "sandbox_server": "auto", "sandbox_server_command": "npm run dev",
                  "sandbox_server_ready_timeout": 20},
        "playwright": {"base_url": "http://127.0.0.1:1",
                       "screenshot_dir": str(F._tempdir("prec-shots-"))},
        "knowledge_base": {"output_dir": str(kb_dir)},
        "proposals": {"output_dir": str(props)},
        "artifacts": {"output_dir": str(F._tempdir("prec-arts-"))},
    }
    _pl.SAMPLE_DEFECTS[:] = [TEST_DEFECT]
    pipe = _pl.AIDefectFixerPipeline(cfg)
    ai = F.FakeAI([F.patch_reply("OK")])
    pipe.ai = ai
    pipe.analyzer = type("Stub", (), {"ai": ai, "analyze": lambda self, d, on_delta=None: {
        "keywords": ["processItem"], "suspect_files": ["src/a.js"], "read_files": [],
        "truncation": [], "root_cause": "桩", "category": "数据", "explanation": "",
        "prevention": "", "non_frontend": False, "patch_text": "", "patch_blocks": [],
        "patch_canonical": "", "parse_errors": [], "block_count": 0,
        "ai_mode": "fake", "ai_error": "", "locate_empty": False,
        "defect_id": d.get("id"), "title": d.get("title")}})()
    results = pipe.run(defects=[TEST_DEFECT], skip_verify=True)
    prop = results[0]["proposal"]
    agent = prop["agent"]

    check("先例进了 prompt", "同类历史缺陷的先例" in ai.prompts[0]["prompt"],
          ai.prompts[0]["prompt"][:120])
    check("先例带上了当时的人工备注与结局", "改法认可" in ai.prompts[0]["prompt"])
    check("注入的先例被记进提案（可追溯）",
          len(agent.get("precedents_used") or []) >= 1
          and agent["precedents_used"][0]["outcome"] == "applied",
          str(agent.get("precedents_used"))[:160])
    pa = agent.get("page_after") or {}
    check("页面复验真的试过并留下状态", pa.get("status") in
          ("unusable", "skipped", "failed", "ok"), str(pa)[:160])
    if pa.get("status") != "ok":
        check("复验没做成时明确说「只有命令级证据」",
              any("页面复验未完成" in n for n in agent.get("notes") or []),
              str(agent.get("notes"))[:200])
    check("复验失败不会把结论抬成 verified-by-page",
          agent["conclusion"] in (CONCLUSION_VERIFIED, "checks_pass", "symptom_persists"),
          agent["conclusion"])
    check("跑完仓库仍零写入", F.snapshot(repo) == before)

    cards = [p for p in kb_dir.rglob("*.md")
             if p.name != "INDEX.md" and "_patterns" not in p.parts]
    check("知识卡片已生成", bool(cards), str([c.name for c in cards])[:120])
    body = cards[0].read_text(encoding="utf-8")
    check("卡片新增「修复循环结论」分区", "## 修复循环结论" in body)
    check("卡片记录了人工结局", "human_decision:" in body, body[:300])
    check("卡片记录了证据来源", "evidence_via:" in body)
    check("卡片原有的现象/根因分区没被动过", "## 现象" in body and "## 根因" in body)
    check("卡片不搬模型散文，只记枚举值与计数",
          "未经任何真实验证" not in body.split("## 修复循环结论")[1][:600],
          body.split("## 修复循环结论")[1][:200])


def main():
    print("== 复现环节（先跑红再修）行为用例 ==")
    t_detect()
    t_spec_rules()
    t_red_first_gate()
    t_no_forever_green()
    t_no_cheating()
    t_assert_strength_labelled()
    t_artifact_and_dedupe()
    t_page_reverify_hook()
    t_precedents()
    t_sandbox_server()
    t_pipeline_end_to_end()
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
