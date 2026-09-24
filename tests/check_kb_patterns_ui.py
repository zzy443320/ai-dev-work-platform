# -*- coding: utf-8 -*-
"""验证知识库「模式 / 缺陷卡片」双层视图（2026-09-23 新增）。

覆盖：
1. 默认展示模式卡（同类缺陷共性），数量与「复发 N 次」徽标正确；
2. 打开模式卡 → 弹窗正文字段齐全（这类缺陷长什么样 / 共性根因 / 处理方式 /
   典型案例 / 复发提示），且关联缺陷可点；
3. 正文里的 markdown 相对链接（`分类/缺陷.md`）能下钻到缺陷卡——
   renderMarkdown 原来不支持链接，会原样显示 `[id](path)`；
4. 切到缺陷卡片视图 → 数量文案与卡片渲染正常，且刷新后视图保持（localStorage）。

用法：
    python tests/check_kb_patterns_ui.py [base_url]
默认 http://127.0.0.1:8765（可用另一个端口的临时实例做验证，不动正在跑的服务）。
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8765"


def main() -> int:
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.evaluate("() => switchTab('defect')")   # 知识库面板在缺陷修复页；默认页签是统计面板
        page.evaluate("() => localStorage.setItem('kb-view', 'pattern')")
        page.evaluate("() => loadKb()")
        page.wait_for_timeout(900)

        # ── 1. 默认模式视图 ──
        count_text = page.inner_text("#card-count")
        if "个模式" not in count_text:
            failures.append(f"模式视图数量文案异常：{count_text!r}")
        cards = page.query_selector_all("#cards .kb-card.pattern")
        if not cards:
            failures.append("模式卡未渲染")
            browser.close()
            print("FAIL: " + "；".join(failures))
            return 1
        first_text = cards[0].inner_text()
        if "复发" not in first_text:
            failures.append(f"模式卡缺少复发徽标：{first_text[:60]!r}")
        hot = page.query_selector_all("#cards .kb-card.pattern.hot")
        if not hot:
            failures.append("复发≥2 的模式卡没有 hot 标记（无法一眼看出系统性缺陷）")

        # ── 2. 打开模式卡 ──
        pid = page.evaluate(
            "async () => (await (await fetch('/api/patterns')).json())[0].id")
        page.evaluate("id => viewPattern(id)", pid)
        page.wait_for_timeout(900)
        if page.eval_on_selector("#modal", "el => el.classList.contains('hidden')"):
            failures.append("模式弹窗未打开")
        else:
            body = page.eval_on_selector("#modal-body", "el => el.innerHTML")
            for sec in ("这类缺陷长什么样", "共性根因", "处理方式", "典型案例", "复发提示"):
                if sec not in body:
                    failures.append(f"模式弹窗缺少「{sec}」章节")
            meta = page.eval_on_selector("#modal-meta", "el => el.innerText")
            if "复发" not in meta:
                failures.append(f"模式弹窗头部缺少复发次数：{meta[:60]!r}")
            if re.search(r"\[[^\]]+\]\([^)]+\)", body):
                failures.append("markdown 相对链接未被渲染（原样显示了 [id](path)）")
            # 关联缺陷必须可点（弹窗头部给的跳转）
            if 'a class="link"' not in page.eval_on_selector(
                    "#modal-meta", "el => el.innerHTML"):
                failures.append("模式弹窗缺少可点击的关联缺陷链接")

        # ── 3. 正文链接下钻 ──
        title_before = page.inner_text("#modal-title")
        opened = page.evaluate("""() => {
            const a = document.querySelector('#modal-body a.link');
            if (!a) return null;
            const href = a.getAttribute('href') || a.getAttribute('onclick') || '';
            a.click();
            return href;
        }""")
        page.wait_for_timeout(900)
        if not opened:
            failures.append("模式卡正文里没有可点的关联案例链接")
        else:
            title_after = page.inner_text("#modal-title")
            meta_after = page.eval_on_selector("#modal-meta", "el => el.innerText")
            if title_after == title_before:
                failures.append(f"点正文链接没有下钻到缺陷卡（标题仍是 {title_before!r}）")
            elif "ID:" not in meta_after:
                failures.append(f"下钻后不是缺陷卡：{meta_after[:60]!r}")

            # ── 4. 切回缺陷卡片视图 ──
            page.evaluate("() => setKbView('defect')")
            page.wait_for_timeout(900)
            page.keyboard.press("Escape")
            if "张卡片" not in page.inner_text("#card-count"):
                failures.append(f"缺陷视图数量文案异常：{page.inner_text('#card-count')!r}")
            if not page.query_selector_all("#cards .kb-card"):
                failures.append("缺陷卡片视图未渲染卡片")
            # 刷新后保持（localStorage）
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1200)
            if "张卡片" not in page.inner_text("#card-count"):
                failures.append("刷新后视图选择未保持（localStorage 失效）")
            page.evaluate("() => setKbView('pattern')")

        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: 模式视图默认展示 / 弹窗字段 / 正文链接下钻 / 视图切换与持久化 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
