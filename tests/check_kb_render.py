"""验证知识库卡片的 Markdown 渲染。

覆盖两个历史 bug：
1. fenced code 内部空行把代码块腰斩成段落（旧实现先按 \\n\\n 切段再判类型）；
2. Markdown 表格不被支持，挤成一行文本。
另做端到端：真实打开一张知识卡片，断言弹窗里表格/代码块结构正确。

用法：python tests/check_kb_render.py（需 web 服务已在 8765 运行）
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

SAMPLE = """# 卡片标题

## 影响文件
| 文件 | 补丁块 | 状态 |
| --- | --- | --- |
| `a.vue` | 1 | ✅ 可应用 |
| `b.vue` | 2 | ✅ 可应用 |

## 补丁
```search-replace
<<<<<<< SEARCH a.vue
line1

line3（上面是空行）
=======
REPLACED
>>>>>>> REPLACE
```

## 建议 diff
```diff
--- a/x
+++ b/x
-old
+new
```

- 列表项一
- 列表项二

1. 步骤一
2. 步骤二
"""


def main() -> int:
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765/", wait_until="networkidle")

        html = page.evaluate("md => renderMarkdown(md)", SAMPLE)

        n_pre = html.count("<pre>")
        if n_pre != 2:
            failures.append(f"代码块数量 {n_pre}（期望 2）——空行可能腰斩了代码块")
        if "<table>" not in html:
            failures.append("表格未渲染成 <table>")
        if html.count("<th>") != 3:
            failures.append(f"表头单元格数 {html.count('<th>')}（期望 3）")
        if "&lt;&lt;&lt;&lt;&lt;&lt;&lt; SEARCH a.vue" not in html.split("</pre>")[0].replace(
                "<pre><code>", "", 1):
            failures.append("SEARCH 行不在第一个代码块内")
        if "<p>line3" in html:
            failures.append("代码块内容泄漏进段落（被内部空行腰斩）")
        if html.count("<li>") != 4:
            failures.append(f"列表项数 {html.count('<li>')}（期望 4）")
        if "<ol>" not in html:
            failures.append("有序列表未渲染成 <ol>")

        # 端到端：打开真实知识卡片（最新提案重刷过的那张）
        page.evaluate(
            "args => viewCard(args[0], args[1])",
            ["逻辑", "L4Zo7p9z4ehvrm44"],
        )
        page.wait_for_timeout(800)
        modal_hidden = page.eval_on_selector(
            "#modal", "el => el.classList.contains('hidden')")
        if modal_hidden:
            failures.append("卡片弹窗未打开")
        else:
            body = page.eval_on_selector("#modal-body", "el => el.innerHTML")
            if "<table>" not in body:
                failures.append("真实卡片「影响文件」表格未渲染")
            pres = page.eval_on_selector_all(
                "#modal-body pre", "els => els.map(e => e.textContent)")
            if not any("<<<<<<< SEARCH" in t for t in pres):
                failures.append("真实卡片 SEARCH/REPLACE 代码块缺失或被腰斩")
            if not any("diff" in t or "+new" in t or "+" in t for t in pres):
                failures.append("真实卡片 diff 代码块缺失")

        # 「现象」区分点回归：ONES 富文本常没有裸换行（段落结构只在标签里），
        # 旧 strip_html 把块级标签换成空格 → 整节挤成一坨。修复后同一节必须
        # 渲染成多个段落（工单 L4Zo7p9zIJ2gSfIU 是当时的坏例子）。
        for cat, card_id, min_blocks in (
                ("其他", "L4Zo7p9zIJ2gSfIU", 6),
                ("逻辑", "L4Zo7p9z4ehvrm44", 3)):
            card_file = PROJECT_ROOT / "knowledge_base" / cat / f"{card_id}.md"
            if not card_file.exists():
                failures.append(f"知识卡片缺失: {cat}/{card_id}")
                continue
            text = card_file.read_text(encoding="utf-8")
            if text.startswith("---"):
                text = text.split("---", 2)[-1]
            sec = text.split("## 现象", 1)[-1].split("## 根因", 1)[0]
            phen_html = page.evaluate("md => renderMarkdown(md)", sec)
            n_blocks = (phen_html.count("<p>") + phen_html.count("<ul>")
                        + phen_html.count("<ol>"))
            if n_blocks < min_blocks:
                failures.append(
                    f"{card_id}「现象」只渲染出 {n_blocks} 个段落/列表"
                    f"（期望 ≥{min_blocks}）——换行还原退化")
        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: renderMarkdown 单元 + 真实卡片弹窗渲染全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
