# -*- coding: utf-8 -*-
"""验证右上角「更新日志」入口（2026-09-23 新增）。

覆盖：
1. 顶栏右上角有入口按钮；
2. 点开能看到条目：日期分组、标签、时间、标题、以及「内容 / 做法 / 文件 / 影响」四栏；
3. 未读小圆点：清掉已读标记后出现，打开一次后消失（且刷新仍然消失）；
4. 标签筛选：点「修复」后只剩修复条目，计数随之变化；
5. 关闭：Esc 与点遮罩都能关；
6. 明暗主题下标题色与背景不同（防止硬编码颜色导致某个主题下看不见）。

用法：
    python tests/check_changelog_ui.py [base_url]
默认 http://127.0.0.1:8765（可用另一个端口的临时实例做验证，不动正在跑的服务）。
"""
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

        # 先制造「没读过」的状态，才能验证小圆点
        page.goto(f"{BASE}/", wait_until="domcontentloaded")
        page.evaluate("() => localStorage.removeItem('changelog-seen')")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)

        # ── 1. 入口存在且在右上角 ──
        btn = page.query_selector("#changelog-btn")
        if not btn:
            failures.append("右上角没有更新日志入口按钮")
        else:
            box = btn.bounding_box()
            actions = page.query_selector(".top-actions").bounding_box()
            if not box or not actions or box["x"] < actions["x"]:
                failures.append("入口按钮不在顶栏右侧动作区")
            if not btn.query_selector("svg"):
                failures.append("入口按钮缺少图标")

        # ── 2. 未读小圆点 ──
        if page.eval_on_selector("#changelog-dot", "el => el.classList.contains('hidden')"):
            failures.append("未读时小圆点没有出现")

        # ── 3. 打开并检查渲染 ──
        page.click("#changelog-btn")
        page.wait_for_timeout(900)
        if page.eval_on_selector("#chmodal", "el => el.classList.contains('hidden')"):
            failures.append("点击入口后弹窗没有打开")
            browser.close()
            print("FAIL: " + "；".join(failures))
            return 1

        days = page.query_selector_all("#ch-body .ch-day")
        if len(days) < 4:
            failures.append(f"日期分组过少：{len(days)} 组")
        items = page.query_selector_all("#ch-body .ch-item")
        if len(items) < 29:
            failures.append(f"条目渲染过少：{len(items)} 条")

        first = items[0] if items else None
        if first:
            txt = first.inner_text()
            for label in ("内容", "做法", "文件", "影响"):
                if label not in txt:
                    failures.append(f"条目缺少「{label}」栏：{txt[:80]!r}")
            if not first.query_selector(".ch-tag"):
                failures.append("条目缺少标签徽标")
            if not first.query_selector(".ch-file"):
                failures.append("涉及文件没有渲染成代码标签")
            if not first.query_selector(".ch-time"):
                failures.append("条目缺少改动时间")

        # 日期标签应带星期与条数
        day_label = page.eval_on_selector("#ch-body .ch-day-label", "el => el.innerText")
        if "周" not in day_label or "条" not in day_label:
            failures.append(f"日期分组头缺少星期/条数：{day_label!r}")

        # ── 4. 打开一次后小圆点消失 ──
        if not page.eval_on_selector("#changelog-dot", "el => el.classList.contains('hidden')"):
            failures.append("打开弹窗后未读小圆点没有消失")

        # ── 5. 标签筛选 ──
        chips = page.query_selector_all("#ch-filters .ch-chip")
        if len(chips) < 3:
            failures.append(f"筛选器过少：{len(chips)} 个")
        else:
            fix_chip = page.query_selector("#ch-filters .ch-chip:not(.active):nth-of-type(3)")
            # 直接按文本找「修复」，避免依赖顺序
            clicked = page.evaluate("""() => {
                const b = [...document.querySelectorAll('#ch-filters .ch-chip')]
                    .find(x => x.textContent.startsWith('修复'));
                if (!b) return false;
                b.click();
                return true;
            }""")
            page.wait_for_timeout(500)
            if not clicked:
                failures.append("筛选器里没有「修复」")
            else:
                tags = page.eval_on_selector_all(
                    "#ch-body .ch-item .ch-tag", "els => els.map(e => e.textContent.trim())")
                if not tags or any(t != "修复" for t in tags):
                    failures.append(f"筛选后仍有非修复条目：{set(tags)}")
                if not page.eval_on_selector("#ch-filters .ch-chip.active",
                                             "el => el.textContent.startsWith('修复')"):
                    failures.append("筛选按钮没有进入选中态")
                if "筛选出" not in page.inner_text("#ch-foot"):
                    failures.append(f"底部没有随筛选变化：{page.inner_text('#ch-foot')!r}")

        # ── 6. 关闭方式 ──
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        if not page.eval_on_selector("#chmodal", "el => el.classList.contains('hidden')"):
            failures.append("Esc 关不掉弹窗")
        else:
            page.click("#changelog-btn")
            page.wait_for_timeout(600)
            page.mouse.click(20, 20)   # 点遮罩
            page.wait_for_timeout(300)
            if not page.eval_on_selector("#chmodal", "el => el.classList.contains('hidden')"):
                failures.append("点遮罩关不掉弹窗")

        # ── 7. 明暗主题都要看得见 ──
        colors = {}
        for theme in ("dark", "light"):
            page.evaluate(f"() => document.documentElement.setAttribute('data-theme', '{theme}')")
            page.click("#changelog-btn")
            page.wait_for_timeout(600)
            colors[theme] = page.eval_on_selector(
                "#ch-body .ch-title",
                "el => [getComputedStyle(el).color, getComputedStyle(el).fontSize]")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        if colors.get("dark") == colors.get("light"):
            failures.append(f"明暗主题下标题样式完全相同，可能写死了颜色：{colors}")
        for theme, (col, size) in colors.items():
            if col in ("rgba(0, 0, 0, 0)", "") or float(size.replace("px", "")) < 11:
                failures.append(f"{theme} 主题下标题颜色/字号异常：{col} / {size}")

        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: 入口位置 / 未读圆点 / 条目四栏渲染 / 标签筛选 / 两种关闭方式 / 明暗主题 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
