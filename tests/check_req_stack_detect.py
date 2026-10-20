# -*- coding: utf-8 -*-
"""需求开发「去死板」三件套的离线回归（2026-10-20）。

钉住三件事，防止改回去：

1. 技术栈自动识别（scripts/stack_detect.py）：读 package.json 推断框架，
   识别不出必须**留空**而不是兜底成默认栈（猜错比不猜更害人）；
2. 影响范围（scope）真正生效：_candidate_files 的候选要被用户圈定的 glob 过滤，
   过滤后为空时保留未过滤结果（写错范围不该让整单跑空）；
3. 工具循环多动作：模型在一条回复的正文里夹带多个 action JSON 时，一轮要全部
   执行（上限 MAX_ACTIONS_PER_REPLY），不能像旧版那样只认「整条回复一个 JSON」
   把第二个动作起全部静默丢掉（fix_agent 215890 的同款坑）。

纯离线：临时目录 + 假 AI；scope 用例需要 git，没有 git 的机器自动 SKIP。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.stack_detect import detect_stack  # noqa: E402
from scripts.task_modules import (MAX_ACTIONS_PER_REPLY,  # noqa: E402
                                  _candidate_files, _complete_with_tools, _scope_globs)

FAIL: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}]  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAIL.append(name)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=15)
        return True
    except Exception:
        return False


# --------------------------------------------------------------- 1. 技术栈识别
def test_detect_stack():
    print("── 1. 技术栈自动识别")
    root = Path(tempfile.mkdtemp(prefix="stack-vue-"))
    _write(root, "package.json", json.dumps({
        "dependencies": {"vue": "^3.4.0", "element-plus": "^2.4.0"},
        "devDependencies": {"typescript": "^5.0.0", "vite": "^5.0.0"},
    }, ensure_ascii=False))
    _write(root, "pnpm-lock.yaml", "")
    det = detect_stack(str(root))
    check("Vue3+TS 识别正确", det["framework"] == "Vue 3 + TypeScript", str(det))
    check("识别依据可解释（构建/包管理/组件库）",
          all(k in det["summary"] for k in ("vite", "pnpm", "element-plus")), det["summary"])
    check("识别依据带来源文件", "package.json" in det["sources"], str(det["sources"]))

    root = Path(tempfile.mkdtemp(prefix="stack-react-"))
    _write(root, "package.json", json.dumps({
        "dependencies": {"react": "^18.2.0"},
    }, ensure_ascii=False))
    det = detect_stack(str(root))
    check("React 无 TS 判 JavaScript", det["framework"] == "React + JavaScript", str(det))

    root = Path(tempfile.mkdtemp(prefix="stack-uniapp-"))
    _write(root, "package.json", json.dumps({
        "dependencies": {"@dcloudio/uni-app": "3.0.0"},
    }, ensure_ascii=False))
    det = detect_stack(str(root))
    check("uni-app 识别正确", det["framework"] == "uni-app", str(det))

    root = Path(tempfile.mkdtemp(prefix="stack-empty-"))
    det = detect_stack(str(root))
    check("空仓库 framework 留空（绝不猜默认栈）", det["framework"] == "", str(det))
    check("不存在的路径也安全返回", detect_stack(str(root / "nope"))["framework"] == "")

    # monorepo：根 package.json 缺框架依赖时，packages/* 一层要能补上
    root = Path(tempfile.mkdtemp(prefix="stack-mono-"))
    _write(root, "package.json", json.dumps({"private": True}, ensure_ascii=False))
    _write(root, "packages/app/package.json", json.dumps({
        "dependencies": {"vue": "^3.4.0"},
        "devDependencies": {"typescript": "^5.0.0"},
    }, ensure_ascii=False))
    det = detect_stack(str(root))
    check("monorepo 子包依赖能识别", det["framework"] == "Vue 3 + TypeScript", str(det))


# --------------------------------------------------------------- 2. scope 过滤
def test_scope_filter():
    print("── 2. 影响范围真正生效")
    check("scope 解析：中英文逗号/换行/反斜杠",
          _scope_globs("src/a\\x/** ，src/b/**\ndocs") ==
          ["src/a/x/**", "src/b/**", "docs"],
          str(_scope_globs("src/a\\x/** ，src/b/**\ndocs")))

    if not _git_available():
        print("  [SKIP] 没有 git，跳过 _candidate_files 过滤断言")
        return
    root = Path(tempfile.mkdtemp(prefix="scope-repo-"))
    _write(root, "src/api/order.ts", "export const order = 1\n")
    _write(root, "src/views/order/list.ts", "export const list = order\n")
    _write(root, "docs/order.md", "order 文档\n")
    subprocess.run(["git", "-C", str(root), "init"], capture_output=True, timeout=30)
    subprocess.run(["git", "-C", str(root), "add", "-A"], capture_output=True, timeout=30)
    hits = _candidate_files(str(root), ["order"])
    check("不加 scope 时三个文件都命中", len(hits) == 3, str(hits))
    hits = _candidate_files(str(root), ["order"], scope="src/views/**")
    check("圈定范围后只剩范围内文件", hits == ["src/views/order/list.ts"], str(hits))
    hits = _candidate_files(str(root), ["order"], scope="nowhere/**")
    check("范围写错时保留未过滤结果（不跑空）", len(hits) == 3, str(hits))


# --------------------------------------------------------------- 3. 多动作循环
class ScriptedAI:
    """按脚本回话：第一轮「正文 + 夹带两个 action JSON」，第二轮给最终答案。

    complete() 必须支持 on_delta（真实现靠流式回调攒原文；夹带动作正是从原文捞的）。
    """

    mode = "fake"

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0

    def complete(self, prompt, system="", max_tokens=None, on_delta=None):
        r = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        raw, parsed = (r if isinstance(r, tuple) else (json.dumps(r, ensure_ascii=False), r))
        if on_delta and raw:
            on_delta("content", raw)
        return parsed


TOOL_SPECS = [{"name": "repo_read", "description": "读文件",
               "inputSchema": {"type": "object"}}]


def test_multi_action_loop():
    print("── 3. 工具循环多动作")
    prose = ("先读这两个文件。\n"
             '{"action":"call","tool":"repo_read","arguments":{"path":"src/a.ts"}}\n'
             '{"action":"call","tool":"repo_read","arguments":{"path":"src/b.ts"}}')
    first = json.loads(prose.split("\n", 1)[1].split("\n")[0])
    final = {"summary": "读完", "plan": "x"}
    ai = ScriptedAI([(prose, first), (json.dumps(final, ensure_ascii=False), final)])
    executed: list = []

    def executor(name, args):
        executed.append(args.get("path"))
        return "OK"

    res = _complete_with_tools(ai, "sys", "p", TOOL_SPECS, executor,
                               max_rounds=4, require_files=False)
    check("一条回复夹带的两个动作都被执行",
          executed == ["src/a.ts", "src/b.ts"], str(executed))
    check("随后仍能收到最终答案", res.get("summary") == "读完", str(res)[:120])

    # 未知工具不许执行、也不许中断循环
    prose_bad = ('{"action":"call","tool":"evil","arguments":{}}\n'
                 '{"action":"call","tool":"repo_read","arguments":{"path":"ok.ts"}}')
    bad = json.loads(prose_bad.split("\n")[0])
    ai = ScriptedAI([(prose_bad, bad),
                     (json.dumps(final, ensure_ascii=False), final)])
    executed.clear()
    _complete_with_tools(ai, "sys", "p", TOOL_SPECS, executor,
                         max_rounds=4, require_files=False)
    check("未知工具被拒、已知工具照常执行", executed == ["ok.ts"], str(executed))

    # 无原文（mock 降级）时退回单动作口径
    ai = ScriptedAI([(None, first), (json.dumps(final, ensure_ascii=False), final)])
    executed.clear()
    _complete_with_tools(ai, "sys", "p", TOOL_SPECS, executor,
                         max_rounds=4, require_files=False)
    check("拿不到原文时退回单动作", executed == ["src/a.ts"], str(executed))

    check("一轮动作上限与 fix_agent 同口径", MAX_ACTIONS_PER_REPLY == 3)


if __name__ == "__main__":
    test_detect_stack()
    test_scope_filter()
    test_multi_action_loop()
    print(f"\n合计 {len(FAIL)} 个失败")
    sys.exit(1 if FAIL else 0)
