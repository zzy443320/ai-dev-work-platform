"""验证面板重排与折叠功能：

1. 顺序：pane-defect 内面板的 **DOM 顺序** 为 运行流水线 → 待审批提案 → 分类统计 → 知识库；
2. 三列驾驶舱在 1440 视口生效（阈值 1240 是刻意定的：查询容器是 .col-main，
   1440 视口只有 1326px，旧阈值 1360 会让主角页在最常见的笔记本宽度上整屏降级）；
3. 折叠：点击 fold-btn 后面板 body 隐藏（collapsed class），再点恢复；
4. 记忆：折叠状态写入 localStorage，页面刷新后保持。

⚠️ 顺序为什么量 DOM 而不是量 getBoundingClientRect().top：
   三列重排后「待审批提案」是左塔（top 最小）、「运行流水线」在中列第二行，
   视觉 top 本来就不是递增的 —— 用 top 断言等于把版式钉死成单列，
   每次动驾驶舱都要来改这里。用例锁的是「面板没被搬乱」，那正是 DOM 顺序。

用法：python tests/check_panel_fold.py（需 web 服务已在 8765 运行）
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

ORDER = ["run-panel", "proposal-panel", "stats-panel", "kb-panel"]


def panel_dom_order(page):
    """返回这四个面板在 #pane-defect 里的先后下标。"""
    return page.evaluate("""ids => {
      const pane = document.querySelector('#pane-defect');
      if (!pane) return null;
      const kids = [...pane.children];
      return ids.map(id => kids.indexOf(document.getElementById(id)));
    }""", ORDER)


def cockpit_cols(page):
    """三列生效的判据：塔台在中列左边、审判台在中列右边。"""
    return page.evaluate("""() => {
      const x = id => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().left : null; };
      const hero = x('defect-hero'), tower = x('proposal-panel'), bench = x('inspector-panel');
      if (hero == null || tower == null || bench == null) return null;
      return { tower, hero, bench, three: tower < hero - 4 && hero < bench - 4 };
    }""")


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

        # 1) DOM 顺序
        idx = panel_dom_order(page)
        if idx is None or -1 in idx:
            failures.append(f"面板缺失: {list(zip(ORDER, idx))}")
        elif idx != sorted(idx):
            failures.append(f"DOM 顺序错误 {list(zip(ORDER, idx))}（期望下标递增）")
        else:
            print(f"[order] {list(zip(ORDER, idx))}")

        # 2) 1440 视口三列驾驶舱必须生效（阈值回归护栏）
        cols = cockpit_cols(page)
        if not cols:
            failures.append("三列判据取不到面板位置")
        elif not cols["three"]:
            failures.append(
                "1440 视口下缺陷页没出三列驾驶舱"
                f"（塔台 {cols['tower']:.0f} / hero {cols['hero']:.0f} / 审判台 {cols['bench']:.0f}）"
                "—— 检查 @container main 的阈值与 .col-main 容器宽")
        else:
            print(f"[cockpit] 1440 三列生效 tower={cols['tower']:.0f} hero={cols['hero']:.0f} bench={cols['bench']:.0f}")

        # 2b) 三列下 hero 不许溢出：阶段列表要么在环右侧、要么降到环下面，
        #     但不能骑到审判台身上（1440 实拍出过这个事）。
        fit = page.evaluate("""() => {
          const hero = document.getElementById('defect-hero').getBoundingClientRect();
          const bench = document.getElementById('inspector-panel').getBoundingClientRect();
          const list = document.querySelector('.dh-stagelist').getBoundingClientRect();
          const nums = document.querySelector('.dh-nums').getBoundingClientRect();
          return { listRight: list.right, heroRight: hero.right, benchLeft: bench.left,
                   numsLeft: nums.left, heroLeft: hero.left, overflowRight: list.right > bench.left + 1,
                   overflowLeft: nums.left < hero.left - 1 };
        }""")
        if fit["overflowRight"] or fit["overflowLeft"]:
            failures.append(f"hero 内容溢出面板边界: {fit}")
        else:
            print(f"[hero-fit] 无溢出 list.right={fit['listRight']:.0f} ≤ bench.left={fit['benchLeft']:.0f}")

        # 3) 折叠交互（以待审批提案为例）
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

        # 4) 刷新后记忆保持
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
