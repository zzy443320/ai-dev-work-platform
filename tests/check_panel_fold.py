"""验证面板重排与折叠功能：

1. 顺序：pane-defect 内面板顺序为 运行流水线 → 待审批提案 → 分类统计 → 知识库；
2. 折叠：点击 fold-btn 后面板 body 隐藏（collapsed class），再点恢复；
3. 记忆：折叠状态写入 localStorage，页面刷新后保持。

用法：python tests/check_panel_fold.py（需 web 服务已在 8765 运行）
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

ORDER = ["run-panel", "proposal-panel", "stats-panel", "kb-panel"]


def panel_tops(page):
    return page.evaluate("""ids => ids.map(id => {
      const el = document.getElementById(id);
      return el ? el.getBoundingClientRect().top : null;
    })""", ORDER)


def main() -> int:
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://127.0.0.1:8765/", wait_until="networkidle")
        page.evaluate("() => localStorage.removeItem('panel-fold-state')")
        page.reload(wait_until="networkidle")
        # 页面默认落在第一个页签（统计面板），本用例断言的是缺陷修复页，显式切换。
        page.evaluate("() => switchTab('defect')")
        page.wait_for_timeout(200)

        # 1) 顺序
        tops = panel_tops(page)
        if any(t is None for t in tops):
            failures.append(f"面板缺失: {list(zip(ORDER, tops))}")
        elif tops != sorted(tops):
            failures.append(f"面板顺序错误 top={tops}（期望 {ORDER} 依次递增）")
        else:
            print(f"[order] {list(zip(ORDER, ['%.0f' % t for t in tops]))}")

        # 2) 折叠交互（以待审批提案为例）
        body_sel = "#proposal-panel > *:not(.panel-head)"
        h_before = page.eval_on_selector(
            "#proposal-panel", "el => el.offsetHeight")
        page.click("#proposal-panel .fold-btn")
        page.wait_for_timeout(150)
        collapsed = page.eval_on_selector(
            "#proposal-panel", "el => el.classList.contains('collapsed')")
        h_after = page.eval_on_selector(
            "#proposal-panel", "el => el.offsetHeight")
        if not collapsed or h_after >= h_before:
            failures.append(f"折叠未生效: collapsed={collapsed} h {h_before}->{h_after}")
        else:
            print(f"[fold] 待审批提案折叠成功 {h_before}->{h_after}px")

        saved = page.evaluate("() => localStorage.getItem('panel-fold-state')")
        if not saved or '"proposal-panel":true' not in saved.replace(" ", ""):
            failures.append(f"折叠状态未写入 localStorage: {saved}")

        # 3) 刷新后记忆保持
        page.reload(wait_until="networkidle")
        page.evaluate("() => switchTab('defect')")   # 刷新会回到默认页签，需切回来
        page.wait_for_timeout(200)
        still = page.eval_on_selector(
            "#proposal-panel", "el => el.classList.contains('collapsed')")
        if not still:
            failures.append("刷新后折叠状态丢失")
        else:
            print("[fold] 刷新后折叠状态保持")

        # 再点一次展开，并清掉测试写入的状态
        page.click("#proposal-panel .fold-btn")
        page.evaluate("() => localStorage.removeItem('panel-fold-state')")
        expanded = page.eval_on_selector(
            "#proposal-panel", "el => !el.classList.contains('collapsed')")
        if not expanded:
            failures.append("二次点击未展开")
        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: 面板顺序 + 折叠 + 状态记忆全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
