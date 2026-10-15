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

# 2026-10-15 改版：run-panel（参数 + 控制台）并入 #defect-hero 驾驶舱，
# 检视器整列删除。DOM 源顺序意图不变：提案 → 统计 → 知识库。
ORDER = ["proposal-panel", "stats-panel", "kb-panel"]


def panel_dom_order(page):
    """返回这四个面板在 #pane-defect 里的先后下标。"""
    return page.evaluate("""ids => {
      const pane = document.querySelector('#pane-defect');
      if (!pane) return null;
      const kids = [...pane.children];
      return ids.map(id => kids.indexOf(document.getElementById(id)));
    }""", ORDER)


def cockpit_cols(page):
    """两列生效的判据：塔台（队列）在驾驶舱左边，中间留正间隙。"""
    return page.evaluate("""() => {
      const r = id => { const e = document.getElementById(id); return e ? e.getBoundingClientRect() : null; };
      const tower = r('proposal-panel'), hero = r('defect-hero');
      if (!tower || !hero) return null;
      return { tower: tower.left, hero: hero.left, gap: hero.left - tower.right,
               two: hero.left > tower.left + 4 && hero.left - tower.right >= 12 };
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

        # 2) 1440 视口两列驾驶舱必须生效（阈值回归护栏）
        cols = cockpit_cols(page)
        if not cols:
            failures.append("两列判据取不到面板位置")
        elif not cols["two"]:
            failures.append(
                "1440 视口下缺陷页没出「队列 | 驾驶舱」两列"
                f"（塔台 {cols['tower']:.0f} / hero {cols['hero']:.0f} / 间隙 {cols['gap']:.0f}）"
                "—— 检查 @container main 的阈值与 .col-main 容器宽")
        else:
            print(f"[cockpit] 1440 两列生效 tower={cols['tower']:.0f} hero={cols['hero']:.0f} gap={cols['gap']:.0f}")

        # 2b) 驾驶舱内容不许溢出面板：阶段列表、参数块、控制台都必须在 hero 边界内
        #     （参数与控制台并入 hero 后新增的护栏 —— 三块挤一行会互相顶出去）。
        fit = page.evaluate("""() => {
          const hero = document.getElementById('defect-hero').getBoundingClientRect();
          const box = sel => { const e = document.querySelector(sel); return e ? e.getBoundingClientRect() : null; };
          const over = (r) => r && (r.right > hero.right + 1 || r.left < hero.left - 1);
          const list = box('.dh-stagelist'), params = box('.dh-params'), cons = box('.dh-console');
          return { heroLeft: hero.left, heroRight: hero.right,
                   listRight: list && list.right, paramsRight: params && params.right,
                   consoleRight: cons && cons.right,
                   bad: [over(list) && 'stagelist', over(params) && 'params', over(cons) && 'console'].filter(Boolean) };
        }""")
        if fit["bad"]:
            failures.append(f"hero 内容溢出面板边界: {fit}")
        else:
            print(f"[hero-fit] 无溢出 hero={fit['heroLeft']:.0f}~{fit['heroRight']:.0f} "
                  f"params.right={fit['paramsRight']:.0f}")

        # 2c) 参数与运行按钮必须同处一块（改版的核心诉求）：
        #     #btn-run / #btn-probe 必须在 .dh-params 内，也就是在 hero 里。
        ops = page.evaluate("""() => {
          const p = document.querySelector('.dh-params');
          const run = document.getElementById('btn-run'), probe = document.getElementById('btn-probe');
          return { hasParams: !!p, inParams: !!(p && run && probe && p.contains(run) && p.contains(probe)),
                   inHero: !!(p && document.getElementById('defect-hero').contains(p)),
                   consoleInHero: !!(document.getElementById('defect-hero')
                     .contains(document.querySelector('.dh-console'))) };
        }""")
        if not (ops["hasParams"] and ops["inParams"] and ops["inHero"] and ops["consoleInHero"]):
            failures.append(f"参数/运行按钮/控制台未并入驾驶舱: {ops}")
        else:
            print("[cockpit] 参数 + 运行按钮 + 控制台都在 hero 内")

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
