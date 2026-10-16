r"""回归：路由反解 + 批量 grep（工单 215890 定位链路复盘）。

两条都直接决定「真凶能不能进读取窗口」，215890 缺一条都跑不通：

**一、路由里的单词段必须留下**
工单里唯一的路径线索是路由 `…/mobile#/agent-workspace/schedule`（正文只有
标题 + 测试环境地址 + 截图，没有任何文件路径）。真凶在
`…/runtime/lui/mobile/pages/schedule/schedule-editor.vue`。
旧 `_route_to_dir_tokens` 要求每个路由段至少 2 个 kebab 词，于是
`schedule` 这个**真目录名被整条丢掉**，只剩一个不存在的 `agent-workspace`：
目录判定空转，窗口里塞满别的 mobile 组件，模型只能拒出补丁（status=invalid）。
现在单词段（≥6 字符、非通用词、非模板噪音）也会留下，并且整条路由再补一份
**带斜杠**的形态 —— 下游 `_target_dirs` 的「末段+次末段」兜底只认含 `/` 的
token，不含斜杠的路由段永远进不了 tier-0。

**二、grep 必须批量，且要排除 vendor 产物**
目标仓里 vendor 进来的 `packages/admin/public/monaco-editor-*/` 有 9MB 的打包
文件，**每个**关键词的 grep 都要整份读一遍：单次 7~10s，20 个关键词就是两三
分钟；`schedule` 这种词直接撞 10s 超时被静默丢弃（= 真凶目录一分都拿不到）。
批量成「一次 grep 多个 -e」并排除 `**/public/**` 等产物后，实测 ≈160s → ≈7s，
且一个词都不丢；丢了的词现在也会写进 meta 明说，不再蒙混。

用法：python tests/check_route_tokens.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.analyzer as az  # noqa: E402
from scripts.analyzer import DefectAnalyzer, _route_to_dir_tokens, strip_html  # noqa: E402

_PASS = 0
_FAIL = 0

KW = "buildModelOptions"
KW2 = "scheduleTypeOption"
TARGET = "packages/app/runtime/lui/mobile/pages/schedule/schedule-editor.vue"
DESKTOP_TWIN = "packages/app/runtime/lui/pages/schedule/schedule-editor.vue"
VENDOR = "packages/admin/public/monaco-editor-0.52.2/vs/worker.js"


def ok(cond: bool, label: str, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [ok]   {label}" + (f" — {detail[:110]}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {detail[:220]}" if detail else ""))


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)


def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def make_repo(root: Path) -> None:
    body = ("<script>\n"
            "export function buildModelOptions() { return []; }\n"
            "const scheduleTypeOption = 'noRepeat';\n"
            "</script>\n")
    _write(root, TARGET, body)
    _write(root, DESKTOP_TWIN, body)
    # vendor 产物：内容命中关键词，但永远不是修复目标
    _write(root, VENDOR, "function buildModelOptions(){return []}\n")
    git(root, "init", "-b", "main")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")


def check_route_tokens() -> None:
    print("[1] 路由反解：单词段（真目录名）不许丢")
    toks = _route_to_dir_tokens(
        "【测试环境】https://test-develop-mysql.cloudpivot.cn/mobile#/agent-workspace/schedule")
    ok("schedule" in toks,
       "215890 的路由里解出真目录名 schedule", str(toks))
    ok("agent-workspace/schedule" in toks,
       "再补一份带斜杠的形态（下游 tier-0 兜底只认含 / 的 token）", str(toks))

    print("\n[2] 原有用例不许回退")
    toks2 = _route_to_dir_tokens("页面定位：admin/#/demo-skill-log，点击新增")
    ok("demo-skill-log" in toks2 and "skill-log" in toks2,
       "kebab 复合段的完整段与相邻词对都还在", str(toks2))
    ok(all("/" not in t for t in toks2), "单段路由不会凭空多出斜杠形态", str(toks2))
    ok("agent-workspace" in toks, "多词段本身仍然保留")

    print("\n[3] 泛段/短段不许混进来")
    for text, bad in (("首页 #/login", "login"),
                      ("列表 #/list", "list"),
                      ("详情 #/detail", "detail"),
                      ("分支 #/develop", "develop")):
        got = _route_to_dir_tokens(text)
        ok(bad not in got, f"#/{bad} 不作为目录线索", str(got))


def check_grep_batching(a: DefectAnalyzer) -> None:
    print("\n[4] 批量 grep：多个关键词只扫一遍仓库")
    calls = []
    orig = az.subprocess.run

    def spy(cmd, *args, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
        return orig(cmd, *args, **kwargs)

    az.subprocess.run = spy
    try:
        hits, dropped = a._grep_hits([KW, KW2, "nonexistentSymbolXyz"])
    finally:
        az.subprocess.run = orig
    greps = [c for c in calls if "grep" in c]
    ok(len(greps) == 1,
       "3 个关键词只发生 1 次 git grep（曾是一个词一次全树扫描）",
       f"calls={len(greps)}")
    ok(not dropped, "没有关键词被丢掉", str(dropped))
    ok(hits.get(KW) == {TARGET, DESKTOP_TWIN},
       "每个关键词各自命中哪些文件仍然分得清", str(sorted(hits.get(KW) or [])))
    ok(hits.get(KW2) == {TARGET, DESKTOP_TWIN},
       "第二个关键词也正确归属（-o 片段返查没串味）", str(sorted(hits.get(KW2) or [])))
    ok(not hits.get("nonexistentSymbolXyz"), "搜不到的词就是空集，不报错")

    print("\n[5] vendor 产物在扫描阶段就排除（否则每个词多花 7~10s）")
    ok(VENDOR not in (hits.get(KW) or set()),
       "public/monaco-editor 下的命中不返回", str(sorted(hits.get(KW) or [])))
    ok(any("exclude" in str(p) for cmd in greps for p in cmd),
       "命令里确实带了 :(exclude) 路径规格")


def check_dropped_is_reported(a: DefectAnalyzer) -> None:
    print("\n[6] grep 失败的关键词必须被记录下来（不许静默当「没命中」）")
    orig = az.subprocess.run

    def boom(cmd, *args, **kwargs):
        if "grep" in (list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]):
            raise subprocess.TimeoutExpired(cmd="git grep", timeout=1)
        return orig(cmd, *args, **kwargs)

    a._grep_dropped = []
    az.subprocess.run = boom
    try:
        a._scan_repo([KW, KW2], [])
        a._drop_high_freq_keywords([KW, KW2])
    finally:
        az.subprocess.run = orig
    ok(set(a._grep_dropped) >= {KW, KW2},
       "超时词进了 _grep_dropped（analyze 会据此在 meta 里明说）", str(a._grep_dropped))


def check_end_to_end_window(repo: Path) -> None:
    print("\n[7] 端到端：只有路由线索的工单，真凶目录必须进窗口")
    a = DefectAnalyzer(str(repo), None, max_files=2, precedent_dir="")
    text = ("【迭代106】移动端定时任务编辑时，选不了模型\n"
            "【测试环境】https://test.example.cn/mobile#/agent-workspace/schedule\n"
            "【测试分支】v2.1.x")
    a._side_hint = az._side_hint_of(text)
    a._wants_validation = False
    kws, ptoks = a._extract_keywords(text)
    ok("schedule" in ptoks, "路由里的 schedule 进了 path_tokens", str(ptoks))
    files = a._scan_repo(kws, ptoks)
    snippets, _meta = a._read_contexts(files, kws)
    ok(any("pages/schedule/" in f for f in snippets),
       "schedule 目录的文件进了读取窗口", str(list(snippets)))
    ok(TARGET in snippets,
       "移动端那一份排在最前（端侧加权同时生效）", str(list(snippets)[:2]))


def main() -> int:
    if not shutil.which("git"):
        print("SKIP：没有 git")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ones-route-tok-"))
    try:
        repo = tmp / "repo"
        repo.mkdir()
        make_repo(repo)
        a = DefectAnalyzer(str(repo), None, max_files=2, precedent_dir="")
        check_route_tokens()
        check_grep_batching(a)
        check_dropped_is_reported(a)
        check_end_to_end_window(repo)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
