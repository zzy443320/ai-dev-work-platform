r"""回归：repo_grep 的 glob 与文案短语定位（工单 215722 复盘）。

2026-10-15 真实工单 215722（「提示词设定文案 是 角色设定」）跑完连补丁都没有，
status=invalid。复盘出的两条硬伤，本用例逐条守住：

**一、glob 花括号静默失效（工具在骗模型）**
模型连发 4 次 `repo_grep glob="**/*.{ts,vue,js,json}"`，而 `{a,b}` 是 **shell**
语法，git 的 wildmatch 与 Python 的 fnmatch 都不认：
  - pathspec `:(glob)**/*.{ts,vue,js,json}` 匹配到 0 个文件 → git grep 退出码 1
  - 代码把它当成「全仓无命中」回给模型 → 模型据此认定「文案在仓库里不存在」
    → give_up 交回人工。而真命中一直都在
    （cloudpivot-locale/locales/ai-platform/ai-platform.zh-CN.ts:442）。
修法：`_expand_braces` 展开成多条 pathspec；并给「无命中」加护栏——glob 一个
文件都没圈到时自动放宽重搜，绝不让「glob 失效」被读成「内容不存在」。

**二、定位只看路径，看不到 i18n 资源（能力缺口）**
语言包替换类缺陷的改动点在 `cloudpivot-locale/locales/*.ts`，路径与工单关键词
毫无关系：那次 18 个嫌疑文件里一个语言包都没有，6 个读取窗口全被路径里带
`ai-agent-detail` 的组件占满。
修法：从工单正文抽中文短语，按原串全仓搜，命中文件免竞争置顶。

用例自带临时 git 仓库，不依赖用户的目标仓库。
用法：python tests/check_grep_glob.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyzer import DefectAnalyzer  # noqa: E402
from scripts.chat import RepoReader  # noqa: E402

_PASS = 0
_FAIL = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [ok]   {label}" + (f" — {detail[:90]}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {detail[:200]}" if detail else ""))


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                   timeout=60)


def make_repo(root: Path) -> None:
    """造一个最小 monorepo：语言包 + 引用它的组件 + 一个同名干扰文档。"""
    (root / "packages" / "locale").mkdir(parents=True)
    (root / "packages" / "ai").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "packages" / "locale" / "ai-platform.zh-CN.ts").write_text(
        "export default {\n"
        "  roleSetting: '角色设定',\n"
        "  dialogSetting: '对话设置',\n"
        "};", encoding="utf-8")
    (root / "packages" / "ai" / "base-config.vue").write_text(
        "<template>\n"
        "  <div>{{ $t('languages.aiPlatform.roleSetting') }}</div>\n"
        "</template>", encoding="utf-8")
    # 干扰项：文档里也有「角色设定」，但它不是改动点，不该被代码搜索命中
    (root / "docs" / "note.md").write_text("角色设定 用于配置提示词", encoding="utf-8")
    git(root, "init")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t",
        "commit", "-m", "init")


def main() -> None:
    if not shutil.which("git"):
        print("SKIP：没有 git")
        return
    tmp = Path(tempfile.mkdtemp(prefix="ones-grep-glob-"))
    try:
        repo = tmp / "repo"
        repo.mkdir()
        make_repo(repo)
        r = RepoReader(str(repo))

        print("[1] glob 花括号展开")
        ok(r._expand_braces("**/*.{ts,vue}") == ["**/*.ts", "**/*.vue"],
           "单层花括号展开成多条", str(r._expand_braces("**/*.{ts,vue}")))
        ok(r._expand_braces("**/*.{a,{b,c}}") == ["**/*.a", "**/*.b", "**/*.c"],
           "嵌套花括号也能展开", str(r._expand_braces("**/*.{a,{b,c}}")))
        ok(r._expand_braces("*.ts") == ["*.ts"], "无花括号时原样返回")
        ps = r._glob_pathspecs("**/*.{ts,vue}")
        ok(len(ps) == 2 and all(p.startswith(":(glob)") for p in ps),
           "pathspec 按扩展名拆成两条", str(ps))

        print("\n[2] repo_grep 带花括号 glob（215722 的直接失败点）")
        out = r.grep("角色设定", "**/*.{ts,vue,js,json}")
        ok("ai-platform.zh-CN.ts" in out and "roleSetting" in out,
           "花括号 glob 命中语言包原文", out.splitlines()[0][:80])
        # 组件里写的是 key 名（$t('languages.aiPlatform.roleSetting')），不是中文串，
        # 所以「按文案搜」只该命中语言包；引用方要靠下一步的 key 二级跳转。
        out_k = r.grep("roleSetting", "**/*.{ts,vue}")
        ok("base-config.vue" in out_k, "按词条 key 能搜到引用它的组件",
           out_k.splitlines()[0][:80])
        ok("docs/note.md" not in out, "扩展名限定生效（文档不是改动点）")
        ok(r.grep("角色设定", "**/*.md").find("note.md") >= 0
           or "note.md" in r.grep("角色设定", "**/*.md"),
           "换成 *.md 就命中文档（说明过滤是真的按 glob 走的）")

        print("\n[3] glob 圈不到文件时的护栏")
        out2 = r.grep("角色设定", "**/*.{py,rb}")
        ok("⚠" in out2 and "roleSetting" in out2,
           "glob 无匹配文件时自动放宽重搜并标注", out2.splitlines()[0][:80])
        out3 = r.grep("压根不存在的串", "**/*.{py,rb}")
        ok(out3.startswith("[无命中]") and "去掉 glob" in out3,
           "放宽后仍无命中才说无命中", out3[:80])

        print("\n[4] 文案短语抽取")
        a = DefectAnalyzer(str(repo), None)
        phrases = a._extract_phrases(
            "【功能验收】自定义Agent中 提示词设定文案 是 角色设定"
            "（原自主规划对应的文案）\n【测试步骤】打开页面")
        ok("角色设定" in phrases, "界面文案被抽出", str(phrases))
        ok("提示词设定文案" in phrases, "更长的一条文案也在")
        ok("原自主规划对应的文案" not in phrases, "解说长句按虚词剔除")
        ok("功能验收" not in phrases, "表单模板词被停用词剔除")
        ok("自定义" not in phrases, "3 字泛词不参与（下限 4 字）")

        print("\n[5] 短语的内容级命中（改动点免竞争）")
        hits = a._content_scan(phrases)
        ok("packages/locale/ai-platform.zh-CN.ts" in hits,
           "命中语言包文件", str(list(hits)))
        info = hits.get("packages/locale/ai-platform.zh-CN.ts") or {}
        ok(2 in (info.get("lines") or []), "带准确行号", str(info.get("lines")))
        ok("roleSetting" in (info.get("samples") or [""])[0],
           "带回原文可直接写进 SEARCH 块", (info.get("samples") or [""])[0][:60])
        ok("docs/note.md" not in hits, "文档命中被扩展名过滤掉")

        print("\n[6] 词条 key 的二级跳转（改动点常在引用方）")
        refs = a._key_refs_scan(hits)
        ok("packages/ai/base-config.vue" in refs, "跳到引用该词条的组件",
           str(list(refs)))
        rinfo = refs.get("packages/ai/base-config.vue") or {}
        ok(rinfo.get("phrase") == "roleSetting", "解析出的 key 正确",
           str(rinfo.get("phrase")))
        ok("roleSetting" in (rinfo.get("samples") or [""])[0],
           "带回引用行原文", (rinfo.get("samples") or [""])[0][:70])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
