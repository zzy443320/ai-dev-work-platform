r"""回归：端侧（移动端/桌面端）加权 + 过时路径自愈（工单 215890 定位侧复盘）。

工单 215890（「【迭代106】移动端定时任务编辑时，选不了模型」）的复现地址是
`…/mobile#/agent-workspace/schedule`，真凶在**移动端**那一份实现里：

    packages/cloudpivot-ai/runtime/lui/mobile/pages/schedule/schedule-editor.vue

同仓还有一份文件名一模一样、内容高度相似的**桌面端**实现：

    packages/cloudpivot-ai/runtime/lui/pages/schedule/schedule-editor.vue

两份文件对关键词的命中几乎一样多，任何「按内容打分」的排序都区分不出来——
谁赢纯看运气。而工单标题里明明写着「移动端」。三个机制缺一不可：

**一、端侧加权** `_side_hint_of` 从工单正文判 mobile/desktop，`_boost` 给同一端
的实现加分；`_rank_key` 在「同名两份实现」的路径裁决里同样偏向同一端。
两边都提到（对比类描述）时返回空串——宁可不用信号也不能押错端。

**二、过时路径自愈** 工单、先例卡片、模型回复里的路径都会过时（上游把模块
挪进了 `runtime/`）。旧行为：`_read_full` 直接回「文件不存在」，`_resolve_file_hint`
的尾缀精确匹配也救不了（真实路径尾部多出一层 `runtime`）→ 模型拿着死路径反复
试、耗尽轮次认输（215890 烧掉 4 轮就是这么来的）。现在按「文件名 + 祖先目录
重叠 + 端侧」找回真身，并**在 note 里点名真实路径**（SEARCH 块必须写真路径）。

**三、先例路径同样会过时** `load_precedent_files` 读的是提案落盘当时的路径。
上游一挪目录，`f in precedent` 永远为假 —— 这套「最可信的修改点信号」（+30 分）
会**静默失效**：日志里先例文件数照样非零，却一笔加分都没生效。

⚠ 挑不准时不许猜：两端祖先重叠与端侧都平手时返回空串，交回上层如实报错。

用例自带临时 git 仓库，不依赖用户的目标仓库。
用法：python tests/check_side_stale.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyzer import DefectAnalyzer, _side_hint_of, _side_of_path  # noqa: E402

_PASS = 0
_FAIL = 0

KW = "buildModelOptions"
# 桌面端实现里**没有**任何端侧标记（真实仓库就是这样），其余三份各有标记
DESKTOP_A = "packages/app/runtime/lui/pages/schedule/schedule-editor.vue"
DESKTOP_B = "packages/app/runtime/lui/pc/pages/schedule/schedule-editor.vue"
MOBILE_A = "packages/app/runtime/lui/mobile/pages/schedule/schedule-editor.vue"
MOBILE_B = "packages/app/runtime/lui/mobile/kit/pages/schedule/schedule-editor.vue"
# 缺一层 runtime/ 的旧路径（工单/先例/模型回复里流传的形态）
STALE = "packages/app/lui/pages/schedule/schedule-editor.vue"
# 真正挑不准的一组：祖先重叠、端侧、长度全部相同
AMBIG_A = "packages/app/aaa/lui/pages/schedule/schedule-echo.vue"
AMBIG_B = "packages/app/bbb/lui/pages/schedule/schedule-echo.vue"
AMBIG_STALE = "packages/app/lui/pages/schedule/schedule-echo.vue"


def ok(cond: bool, label: str, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [ok]   {label}" + (f" — {detail[:110]}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {detail[:220]}" if detail else ""))
    return cond


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)


def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _editor(tag: str) -> str:
    return ("<script>\n"
            "export function buildModelOptions() {\n"
            f"  // 端侧标记：{tag}\n"
            "  return [];\n"
            "}\n"
            "</script>\n")


def make_repo(root: Path) -> None:
    """四份同名实现（关键词命中数完全相同，只有路径能区分）+ 一组真平手。"""
    _write(root, DESKTOP_A, _editor("desktop"))
    _write(root, DESKTOP_B, _editor("desktop"))
    _write(root, MOBILE_A, _editor("mobile"))
    _write(root, MOBILE_B, _editor("mobile"))
    # 不含量词：只为「真平手时不猜」这条断言存在，不许污染排序用例
    _write(root, AMBIG_A, "<script>\n// aaa\nexport default {};\n</script>\n")
    _write(root, AMBIG_B, "<script>\n// bbb\nexport default {};\n</script>\n")
    git(root, "init", "-b", "main")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")


class FakeAI:
    """只回一句「看不出根因」，用于把 analyze() 跑到定位与端侧判定那一步。"""

    def __init__(self):
        self.mode = "fake"
        self.model = "fake"
        self.calls = 0

    def complete(self, prompt, system="", max_tokens=None, on_delta=None, images=None):
        self.calls += 1
        return {"patch_blocks": "", "root_cause": "（用例假回复）", "error": ""}

    def complete_text(self, prompt, system="", max_tokens=None, on_delta=None, images=None):
        return self.complete(prompt, system, max_tokens, on_delta, images)


def check_side_hint() -> None:
    print("[1] 端侧判定：标题/URL 里的移动端信号要认得出来")
    ok(_side_hint_of("【迭代106】移动端定时任务编辑时，选不了模型") == "mobile",
       "标题写「移动端」→ mobile")
    ok(_side_hint_of("h5 页面白屏") == "mobile", "h5 → mobile")
    ok(_side_hint_of("打开 https://x.cn/mobile#/agent-workspace/schedule 复现") == "mobile",
       "只给了一个 /mobile 地址 → mobile")
    ok(_side_hint_of("桌面端列表分页错位") == "desktop", "「桌面端」→ desktop")
    ok(_side_hint_of("PC端导出按钮无响应") == "desktop", "PC端 → desktop")
    ok(_side_hint_of("移动端与桌面端表现不一致") == "",
       "两边都提到 → 不偏（宁可不用信号也不押错端）")
    ok(_side_hint_of("定时任务编辑时选不了模型") == "",
       "没有任何端侧信息 → 不偏")

    print("\n[2] 路径端侧标记按 -_. 分段整段比对")
    ok(_side_of_path(DESKTOP_A) == "", "桌面端实现没有标记（真实仓库就是这样）")
    ok(_side_of_path(MOBILE_A) == "mobile", "mobile 段 → mobile")
    ok(_side_of_path(DESKTOP_B) == "desktop", "pc 段 → desktop")
    ok(_side_of_path("packages/app/mobilesale/util.ts") == "",
       "mobilesale 不是 mobile（不许整段比对退化成子串）")


def check_ranking(a: DefectAnalyzer) -> None:
    print("\n[3] 端侧加权：两份同名实现里，排到工单说的那一端")
    a._side_hint = "mobile"
    got = a._scan_repo([KW], [])
    ok(len(got) >= 2 and all(_side_of_path(f) == "mobile" for f in got[:2]),
       "mobile 工单：两份移动端实现占住前两名", str(got[:3]))
    a._side_hint = "desktop"
    got = a._scan_repo([KW], [])
    ok(len(got) >= 2 and all(_side_of_path(f) != "mobile" for f in got[:2]),
       "desktop 工单：不含移动端实现", str(got[:3]))
    a._side_hint = ""
    got = a._scan_repo([KW], [])
    ok(got and got[0] == DESKTOP_A,
       "没端侧信号时不硬偏（回到原有的「路径更浅更前」）", str(got[:3]))


def check_stale_path(a: DefectAnalyzer) -> None:
    print("\n[4] 过时路径自愈：不许只回一句「文件不存在」")
    a._side_hint = ""
    cands = a._resolve_stale_file(STALE, limit=4)
    ok(MOBILE_A in cands and DESKTOP_A in cands,
       "缺 runtime/ 的旧路径能找回两端真实路径", str(cands))
    ok(cands[0] == DESKTOP_A,
       "旧路径不带端侧标记 → 先钉到同样不带标记的真身", str(cands[:2]))

    a._side_hint = "mobile"
    ok(a._resolve_stale_file(STALE, limit=2)[0] == MOBILE_A,
       "mobile 工单：同名候选里移动端排前", str(a._resolve_stale_file(STALE, 2)))
    a._side_hint = "desktop"

    text, note = a._read_one(STALE, [KW])
    ok(bool(text) and KW in text,
       "旧路径也能读到内容（不再白烧一轮）", f"len={len(text or '')}")
    ok(bool(note) and STALE in note and "已不存在" in note and "runtime/" in note,
       "note 必须点名真实路径，并说清旧路径已不存在", note[:170])


def check_resolve_file_hint(a: DefectAnalyzer) -> None:
    print("\n[5] 点名文件的解析：能定就定，定不了就说定不了")
    a._side_hint = ""
    ok(a._resolve_file_hint(STALE) == DESKTOP_A,
       "旧路径形如桌面端树（不带 mobile 段）→ 钉到桌面端真身",
       repr(a._resolve_file_hint(STALE)))
    ok(a._resolve_file_hint("schedule-editor.vue") == DESKTOP_A,
       "裸文件名：无端侧信号时取更短的那份", repr(a._resolve_file_hint("schedule-editor.vue")))
    a._side_hint = "mobile"
    ok(a._resolve_file_hint("schedule-editor.vue") == MOBILE_A,
       "裸文件名 + mobile 工单：取移动端那份", repr(a._resolve_file_hint("schedule-editor.vue")))
    ok(a._resolve_file_hint(DESKTOP_A) == DESKTOP_A, "真实路径原样返回")
    a._side_hint = ""
    ok(a._resolve_file_hint(AMBIG_STALE) == "",
       "祖先重叠/端侧/长度全平手 → 返回空，交回上层如实报错",
       repr(a._resolve_file_hint(AMBIG_STALE)))
    ok(set(a._resolve_stale_file(AMBIG_STALE, limit=2)) == {AMBIG_A, AMBIG_B},
       "两个候选都要给出来（供上层/日志判断），只是不替它选",
       str(a._resolve_stale_file(AMBIG_STALE, 2)))


def check_precedent_resolution(a: DefectAnalyzer) -> None:
    print("\n[6] 先例路径过时也要映射回真实路径（否则「最可信的信号」静默失效）")
    a._side_hint = ""
    got = a._resolve_precedent([STALE, DESKTOP_A])
    ok(got == [DESKTOP_A], "旧先例路径映射到真实路径，且与已有条目合并", str(got))

    print("\n[7] 先例加分必须真的生效（+30 曾因路径过时而完全没生效）")
    a._precedent_files = [STALE]     # 故意塞一条过时路径
    a._side_hint = ""
    ranked = a._scan_repo([KW], [])
    ok(ranked and ranked[0] == DESKTOP_A,
       "过时先例也能命中真身并排到最前", str(ranked[:3]))


def check_analyze_wiring(repo: Path) -> None:
    print("\n[8] analyze() 要把端侧语境传下去（不许只在单测里手工 setattr）")
    a = DefectAnalyzer(str(repo), FakeAI(), max_files=2)
    a.analyze({
        "title": "【迭代106】移动端定时任务编辑时，选不了模型",
        "description": "打开 https://test.example.cn/mobile#/agent-workspace/schedule，"
                       "点编辑，模型下拉里只有一个「自动模型」。",
    })
    ok(a._side_hint == "mobile", "analyze() 推导出 mobile", repr(a._side_hint))
    a2 = DefectAnalyzer(str(repo), FakeAI(), max_files=2)
    a2.analyze({"title": "桌面端列表分页错位", "description": "翻到第二页就空了。"})
    ok(a2._side_hint == "desktop", "analyze() 推导出 desktop", repr(a2._side_hint))


def main() -> int:
    if not shutil.which("git"):
        print("SKIP：没有 git")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ones-side-stale-"))
    try:
        repo = tmp / "repo"
        repo.mkdir()
        make_repo(repo)
        a = DefectAnalyzer(str(repo), None, max_files=2)
        check_side_hint()
        check_ranking(a)
        check_stale_path(a)
        check_resolve_file_hint(a)
        check_precedent_resolution(a)
        check_analyze_wiring(repo)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
