# -*- coding: utf-8 -*-
"""真浏览器验证：外部内容进 v-html 之前必须转义（防存储型 XSS）。

盯的是 2026-10-08 修掉的两处未转义插值（frontend/src/composables/useTasks.js）：
需求开发页把 Figma 返回的 file_name/note/tree、代码测试页把目标仓库文件的正文，
直接拼进 `.log-title` / `.log-html` 再交给 v-html。两者都是**外部可控**的内容：
仓库里放一个 `<img src=x onerror=...>`，预览一下就等于在界面里执行脚本。

同文件其他拼 HTML 的地方（useDefect / proposalRender / useSettings）一直有
escapeHtml，只有这两处漏 —— 所以这不是风格问题，是缺陷，且退化时没有任何其他信号。

跑法（不依赖 cwd）：
    PYTHONUTF8=1 .venv/Scripts/python.exe tests/check_render_escape.py
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

from temp_server import serve                    # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = TESTS_DIR.parent
BAD = []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        BAD.append(name)


# 载荷：src=x 必然触发 onerror；执行了就说明 HTML 被当真 HTML 解析了
PAYLOAD = '<img src=x onerror="window.__pwned = 1">'
MARK = "ESCAPE-MARKER-DO-NOT-EXECUTE"
EVIL_FILE = "EvilPreview.tsx"


def write_evil(repo_dir: Path) -> None:
    (repo_dir / EVIL_FILE).write_text(
        f"// {MARK}\nexport const evil = `{PAYLOAD}`;\n", encoding="utf-8")


def main() -> int:
    with serve() as srv:
        write_evil(srv.root / "repo")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(srv.base + "/", wait_until="networkidle")
            page.wait_for_timeout(400)

            # ---- 代码测试页：预览仓库里的文件（外部可控内容） ----
            page.evaluate("switchTab('codetest')")
            page.wait_for_timeout(300)
            page.fill("#test-file", EVIL_FILE)
            page.click("#btn-test-load")
            try:
                page.wait_for_selector(f"#test-preview:has-text('{MARK}')", timeout=15000)
            except Exception as e:
                check("文件预览渲染出来了", False, str(e)[:120])
                browser.close()
                return 1
            check("文件预览渲染出来了", True)

            preview = page.inner_text("#test-preview")
            # 逃逸判据 1：载荷被当成**文字**显示，而不是被解析成标签
            check("载荷以字面文本呈现（未生成元素）", PAYLOAD in preview,
                  preview[:100].replace("\n", " "))
            # 逃逸判据 2：预览容器里没有真正的 img 标签
            imgs = page.eval_on_selector_all("#test-preview img", "els => els.length")
            check("预览 DOM 里没有 img 元素", imgs == 0, f"img={imgs}")
            # 逃逸判据 3：终极判据——onerror 到底有没有跑
            pwned = page.evaluate("() => window.__pwned")
            check("脚本未被执行（window.__pwned 未定义）", pwned is None, f"pwned={pwned}")

            browser.close()

    # ---- 静态兜底：Figma 那处同样必须过 escapeHtml ----
    # 端到端要真拉 Figma（外网 + 令牌），不适合放进自检，所以按本项目惯例
    # （check_vue_migration.py 也是直接断言源码文本）盯住转义调用本身。
    src = (ROOT / "frontend" / "src" / "composables" / "useTasks.js").read_text(
        encoding="utf-8")
    check("Figma 树走 escapeHtml", "escapeHtml(data.tree || '')" in src)
    check("Figma 标题走 escapeHtml", "escapeHtml(head)" in src)
    check("Figma note 走 escapeHtml", "escapeHtml(data.note)" in src)
    check("仓库正文走 escapeHtml", "escapeHtml(data.content || '')" in src)
    check("确实 import 了 escapeHtml", "from '../utils/format.js'" in src
          and "escapeHtml" in src.split("from '../utils/format.js'")[0].splitlines()[-1])

    # ---- 问答页那个「未定义变量」的坑（ChatPane 生成提案后刷新产出物列表） ----
    chat = (ROOT / "frontend" / "src" / "views" / "ChatPane.vue").read_text(
        encoding="utf-8")
    binds = [l for l in chat.splitlines()
             if "useArtifacts()" in l or ("loadArtifacts" in l and "const {" in l)]
    check("ChatPane 从 useArtifacts 里解构了 loadArtifacts",
          any("loadArtifacts" in l for l in binds),
          " | ".join(l.strip()[:80] for l in binds) or "没找到解构行")
    check("调用的 loadArtifacts 已被解构（否则运行时 ReferenceError）",
          "loadArtifacts('chat')" in chat and any("loadArtifacts" in l for l in binds))

    print("\n" + ("ALL PASS" if not BAD else f"{len(BAD)} FAILED: {BAD}"))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
