# -*- coding: utf-8 -*-
"""合成工单跑道：把「线上撞见过的每一类失败」固化成一条可重复跑的假工单。

## 为什么要有这条跑道

跑新工单总在冒新问题，但复盘 41 份提案后的实情是：失败只有 9 类，而且**每一类都能用一条
固定的假工单复现**——假工单不烧 token、不依赖网络、几秒钟跑完。于是"每遇到一次就得人肉
排查一次"变成"跑一遍跑道，红一条就是这一类会再犯"。

跑道的三条硬规矩：

1. **不打真实模型**。用可编排的假 AI 把每条失败剧本钉死：脚本里写了什么，循环就该有什么
   动作。真实模型的质量问题归 check_locate_regression* 那类回放用例管，混在一起只会变成
   随机失败发生器。
2. **每条假工单都要断言"至少产出补丁或合法结论"**，不是断言"没报错"。215890 那类"零补丁
   零结论"的收场，就是跑道要拦住的东西。
3. **分类器里每个"需要动作的类"都必须被护栏或跑道兜住**（见 `check_taxonomy_closure`）
   ——否则那个类下次出现时，还是只能靠人肉。

用法：python tests/ticket_battery.py
"""
import atexit
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.fix_agent import (  # noqa: E402
    CONCLUSION_BACKEND, CONCLUSION_GREEN, CONCLUSION_STALLED, CONCLUSION_VERIFIED,
    FixAgent,
)
from scripts.gate import run_gate  # noqa: E402
from scripts.postmortem import CLASSES  # noqa: E402

FAIL = []
PASS = 0
_TEMPS: list = []
GREEN = (CONCLUSION_GREEN, CONCLUSION_VERIFIED)


@atexit.register
def _cleanup_temps():
    for d in _TEMPS:
        try:
            d.cleanup()
        except OSError:
            pass


def ok(cond, label, detail=""):
    """与 tests/ 下其它用例同签名：(条件, 名字, 细节)。"""
    global PASS
    print(("  [ok]   " if cond else "  [FAIL] ") + label
          + (f" — {str(detail)[:170]}" if detail else ""))
    if cond:
        PASS += 1
    else:
        FAIL.append(label)


def _tempdir(prefix):
    d = tempfile.TemporaryDirectory(prefix=prefix)
    _TEMPS.append(d)
    return Path(d.name)


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=str(repo), capture_output=True, text=True).stdout.strip()


# ------------------------------------------------------------------ 假目标仓库
REAL_EDITOR = "packages/ai/runtime/mobile/pages/schedule/editor.vue"
STALE_EDITOR = "packages/ai/mobile/pages/schedule/editor.vue"  # 工单里写的旧路径
STORE = "packages/ai/runtime/lui/store/workspace-store.ts"
BUG_LINE = "export const MODEL_LABEL = 'never';"
FIX_LINE = "export const MODEL_LABEL = 'always';"

CHECK_PY = """
import pathlib
import sys

src = pathlib.Path('packages/ai/runtime/mobile/pages/schedule/editor.vue').read_text(
    encoding='utf-8')
if 'never' in src:
    print('移动端模型标签仍然是 never', file=sys.stderr)
    sys.exit(1)
print('clean')
"""
CHECK2_PY = """
import pathlib
import sys

src = pathlib.Path('packages/ai/runtime/lui/store/workspace-store.ts').read_text(
    encoding='utf-8')
if 'STORE_VERSION = 1' not in src:
    print('store 版本被改动', file=sys.stderr)
    sys.exit(1)
print('clean')
"""
CHECK_CMD = f'"{Path(sys.executable).as_posix()}" check.py'
CHECK2_CMD = f'"{Path(sys.executable).as_posix()}" check2.py'


def make_repo(prefix="battery-repo-"):
    repo = _tempdir(prefix)
    for rel in ("packages/ai/runtime/mobile/pages/schedule", "packages/ai/api",
                "packages/ai/runtime/lui/store"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / REAL_EDITOR).write_text(
        "<!-- 移动端定时任务编辑弹窗 -->\n"
        "<template>\n  <span>{{ MODEL_LABEL }}</span>\n</template>\n"
        "<script>\n"
        f"{BUG_LINE}\n"
        "export const TITLE = 'schedule';   // 定时任务编辑\n"
        "</script>\n", encoding="utf-8")
    (repo / STORE).write_text(
        "export const STORE_VERSION = 1;\n"
        "export const availableModel: string[] = [];\n"
        "export function setSelectedModelCode(code?: string) { return code; }\n",
        encoding="utf-8")
    (repo / "packages" / "ai" / "api" / "ai.api.ts").write_text(
        "// 模型目录接口\n"
        "export function listModels(): Promise<string[]> { return Promise.resolve([]); }\n",
        encoding="utf-8")
    (repo / "check.py").write_text(CHECK_PY, encoding="utf-8")
    (repo / "check2.py").write_text(CHECK2_PY, encoding="utf-8")
    (repo / "package.json").write_text(json.dumps({"name": "battery"}), encoding="utf-8")
    git(repo, "init", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "init")
    return repo


class FakeAI:
    """按脚本回放回复；mode 不能是 mock，否则循环自己会短路。"""

    def __init__(self, replies):
        self.mode = "fake"
        self.model = "fake"
        self.replies = list(replies)
        self.prompts = []
        self.calls = 0

    def complete_text(self, prompt, system="", max_tokens=None, on_delta=None, images=None):
        self.calls += 1
        self.prompts.append(prompt)
        if on_delta:
            on_delta("content", "(fake)")
        if not self.replies:
            return {"text": "", "error": "FakeAI 脚本已用尽"}
        return {"text": self.replies.pop(0), "error": ""}


def agent_for(repo, ai, **cfg):
    conf = {"max_rounds": 6, "deadline_seconds": 180, "max_stall": 3,
            "per_command_timeout": 60, "repro": "off"}
    conf.update(cfg)
    return FixAgent(str(repo), ai, agent_cfg=conf,
                    gate_cfg={"commands": [{"name": "check", "cmd": CHECK_CMD},
                                           {"name": "store-check", "cmd": CHECK2_CMD}]})


def patch_reply(path=REAL_EDITOR, search=BUG_LINE, replace=FIX_LINE):
    return (f"根因：{path} 里模型标签写死。\n"
            f"<<<<<<< SEARCH {path}\n{search}\n=======\n{replace}\n>>>>>>> REPLACE")


def call_tool(name, **args):
    return json.dumps({"action": "call", "tool": name, "arguments": args})


# ------------------------------------------------------------------ 假工单
def ticket_stale_path():
    """215890 死点：工单里的路径过时，模型照抄 → 工具必须给出真实路径。"""
    repo = make_repo()
    ai = FakeAI([call_tool("repo_read", path=STALE_EDITOR), patch_reply()])
    res = agent_for(repo, ai).run({"id": "B1", "title": "移动端编辑页选不了模型"})
    saw = ai.prompts[1] if len(ai.prompts) > 1 else ""
    ok(REAL_EDITOR in saw and "不要照抄" in saw,
       "工具把真实路径回给了模型",
       [ln for ln in saw.splitlines() if "相近" in ln][:1])
    ok(res["rounds"] == 2, "两轮就收敛（不再拿轮次试路径）", f"rounds={res['rounds']}")
    ok(res["conclusion"] in GREEN, "产出补丁并转绿", res["conclusion"])
    ok(res["files"] == [REAL_EDITOR], "补丁打在真实路径上", str(res["files"]))


def ticket_embedded_action():
    """215890 第 4 轮：空补丁块 + 夹带 tool call → 那一轮必须去查代码，不能白烧。"""
    repo = make_repo()
    mixed = (f"<<<<<<< SEARCH {STALE_EDITOR}\n=======\n>>>>>>> REPLACE\n\n"
             "Wait — 我不能这样空提交。先查仓库。\n\n"
             + call_tool("repo_grep", pattern="MODEL_LABEL", glob="**/*.vue"))
    ai = FakeAI([mixed, patch_reply()])
    res = agent_for(repo, ai).run({"id": "B2", "title": "移动端编辑页选不了模型"})
    trace = res["trace"]
    tool_rows = [r for r in trace if r.get("kind") == "tool"]
    ok(any(r.get("round") == 1 and r.get("tool") == "repo_grep" for r in tool_rows),
       "空补丁块那轮执行了夹带的工具调用", str(tool_rows[:1]))
    ok(len(res["attempts"]) == 1, "那一轮没有被当成补丁交上去",
       f"attempts={len(res['attempts'])}")
    ok(res["conclusion"] in GREEN, "最终仍能产出补丁", res["conclusion"])


def ticket_brace_glob():
    """215722 死点：glob 用了 shell 花括号 → 必须仍能搜到内容。"""
    repo = make_repo()
    ai = FakeAI([call_tool("repo_grep", pattern="MODEL_LABEL", glob="**/*.{vue,ts}"),
                 patch_reply()])
    res = agent_for(repo, ai).run({"id": "B3", "title": "移动端编辑页选不了模型"})
    saw = ai.prompts[1] if len(ai.prompts) > 1 else ""
    ok(REAL_EDITOR in saw and "[无命中]" not in saw,
       "花括号 glob 真的圈到了文件",
       [ln for ln in saw.splitlines() if "命中" in ln][:1])
    ok(res["conclusion"] in GREEN, "最终产出补丁", res["conclusion"])


def ticket_wrong_path_patch():
    """补丁直接打到不存在的路径 → 反馈里必须带仓库里的真实候选路径。"""
    repo = make_repo()
    ai = FakeAI([patch_reply(path=STALE_EDITOR), patch_reply()])
    res = agent_for(repo, ai).run({"id": "B4", "title": "移动端编辑页选不了模型"})
    saw = ai.prompts[1] if len(ai.prompts) > 1 else ""
    ok("相近的真实路径" in saw and REAL_EDITOR in saw,
       "沙箱拒绝后回喂了真实候选路径")
    ok(res["conclusion"] in GREEN and res["files"] == [REAL_EDITOR],
       "第二轮改到真实路径后转绿", f"{res['conclusion']} / {res['files']}")


def ticket_search_miss():
    """SEARCH 与原文不逐字一致 → 反馈要给出可执行的修法，且第二轮能修好。"""
    repo = make_repo()
    ai = FakeAI([patch_reply(search="export const MODEL_LABEL='never';"), patch_reply()])
    res = agent_for(repo, ai).run({"id": "B5", "title": "移动端编辑页选不了模型"})
    saw = ai.prompts[1] if len(ai.prompts) > 1 else ""
    ok("逐字" in saw, "反馈要求逐字照抄原文")
    ok(res["conclusion"] in GREEN, "第二轮照抄后转绿", res["conclusion"])


def ticket_introduced_red():
    """补丁把基线本来是绿的检查弄红 → 必须拦住（基线摘除 ≠ 放水）。"""
    repo = make_repo()
    ai = FakeAI([patch_reply(path=STORE, search="export const STORE_VERSION = 1;",
                             replace="export const STORE_VERSION = 2;")])
    res = agent_for(repo, ai).run({"id": "B6", "title": "顺手改 store 版本"})
    v = res.get("verification") or {}
    ok(res["conclusion"] == "regression" or v.get("regressions"),
       "新引入的失败被识别为回归", f"{res['conclusion']} / {v.get('regressions')}")


def ticket_verdict_backend():
    """根因不在前端 → 必须交出带证据的归因结论，而不是零信息认输。"""
    repo = make_repo()
    ai = FakeAI([json.dumps({
        "action": "verdict", "kind": "backend_issue",
        "endpoint": "/api/ai/models",
        "expected": "模型目录返回非空列表",
        "actual": "移动端拿到的模型列表为空，下拉里没有可选项",
        "citations": ["packages/ai/api/ai.api.ts:2"],
        "handoff": "请后端确认模型目录接口在移动端租户下的返回",
        "cleanup": "后端修好后移除前端的空列表兜底",
    })])
    res = agent_for(repo, ai).run({"id": "B7", "title": "移动端选不了模型（接口无数据）"})
    v = res.get("verdict") or {}
    ok(res["conclusion"] == CONCLUSION_BACKEND and res["conclusion"] != CONCLUSION_STALLED,
       "结论是后端归因（不是认输）", res["conclusion"])
    ok(bool(v.get("verified")) and bool(v.get("citations_ok")),
       "结论带可核查引用", str(v.get("citations_ok")))
    ok(bool(v.get("handoff")) and bool(v.get("cleanup")),
       "给了交接与清理动作", str(v.get("handoff"))[:60])


def ticket_half_written_patch():
    """半截改动（replace 吐到一半）漂在哪、以及哪一刻会静默通过。

    实测出来的两种行为：
      * `.ts` → 硬拦（balance: 进 failed_checks，提案停在 gate_failed）；
      * `.vue` → **刻意不判失败**（模板插值/多行模板字符串会误报，见 _vue_check），
        闸门照样 ok。取舍保留，但必须**点名**该文件，不能让人以为"闸门过了"就等于"校验过了"。
    """
    repo = make_repo()
    broken_ts = "export const S = { a: 1;\n"
    g_ts = run_gate(str(repo), {}, [STORE], patched_contents={STORE: broken_ts})
    ok(not g_ts.ok and any(f.startswith("balance:") for f in g_ts.failed_checks),
       ".ts 半截改动被硬拦", str(g_ts.failed_checks))

    broken_vue = ("<template>\n  <span>{{ a }}</span>\n</template>\n"
                  "<script>\nexport const x = { a: 1;\n</script>\n")
    g_vue = run_gate(str(repo), {}, [REAL_EDITOR],
                     patched_contents={REAL_EDITOR: broken_vue})
    ok(any("不平衡" in (n or "") and "硬校验" in (n or "") for n in g_vue.notes),
       ".vue 半截改动虽不硬拦，但被点名（不再静默）", str(g_vue.notes[-1:]))
    ok(any(c.skipped and c.name.startswith("skip:") for c in g_vue.checks),
       "该文件被明确标成「未通过硬校验」", str([(c.name, c.skipped) for c in g_vue.checks]))


# 分类器里的类 → 已被哪条跑道/护栏兜住（闭环自检用）
COVERS = {
    "path_not_found": "ticket_stale_path / ticket_wrong_path_patch / check_path_suggest",
    "tool_misled": "ticket_brace_glob / check_grep_glob",
    "search_miss": "ticket_search_miss / check_fix_agent",
    "gate_red": "ticket_introduced_red",
    "gate_baseline_red": "check_gate_baseline",
    "patch_broken_syntax": "ticket_half_written_patch",
    "locate_miss": "check_locate_regression*",
    "stall_or_budget": "check_fix_agent（不收敛/预算）",
    "ai_unavailable": "check_fix_agent（推理截断签名）",
    "model_no_patch": "ticket_stale_path 等（不得零交付）",
    "patch_ready": "（健康类，无需护栏）",
    "shipped": "（健康类，无需护栏）",
    "human_rejected": "（人工决策，非失败类）",
}


def check_taxonomy_closure():
    """分类器里「需要动作的类」必须被护栏或跑道兜住，否则下次还是要人肉排查。"""
    missing = [k for k, v in CLASSES.items() if v["next"] and k not in COVERS]
    ok(not missing, "每个需要动作的类都有护栏或跑道", str(missing))
    noted = [k for k, v in CLASSES.items()
             if v["next"] and k in COVERS and "check_" not in COVERS[k]
             and "ticket_" not in COVERS[k]]
    print(f"     （只有说明、没有自动用例的类：{noted or '无'}）")


TICKETS = [
    ("路径过时（215890）", ticket_stale_path),
    ("夹带 tool call（215890 第4轮）", ticket_embedded_action),
    ("花括号 glob（215722）", ticket_brace_glob),
    ("补丁打偏路径", ticket_wrong_path_patch),
    ("SEARCH 不逐字", ticket_search_miss),
    ("补丁引入新红", ticket_introduced_red),
    ("后端归因交付", ticket_verdict_backend),
    ("半截改动的拦截边界", ticket_half_written_patch),
]


def main():
    print("合成工单跑道：每条假工单守住一类失败\n")
    for name, fn in TICKETS:
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:  # 用例自身炸了也要算这条跑道红，不能静默跳过
            ok(False, f"{name} 执行异常", repr(exc))
        print()
    print("[闭环自检]")
    check_taxonomy_closure()
    print(f"\n通过 {PASS} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  FAIL {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
