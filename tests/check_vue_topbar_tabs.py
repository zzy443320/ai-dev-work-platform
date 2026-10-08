# -*- coding: utf-8 -*-
"""顶部导航布局：页签在顶栏里（品牌右侧），等宽均分整条空间，装不下才出箭头。

背景：切到「顶部导航」后，页签原来仍留在主列、单独占一整行 —— 顶栏右侧空着
一大片，而页签行白占 56px 高度。于是把页签搬进顶栏（`.topbar-tabs`）。第二轮把
形态改成「文字型顶部导航」：扁平无底、**等宽均分**、活动项文字宽度下划线且压在
顶栏底边上（页面右下角那三个图标按钮之间的 22px 大间距也在这一轮修掉了）。

这里锁的是几何与交互契约（不碰仓库、不调模型）：
  ① 页签条真的落在顶栏内、紧贴品牌右侧，且没把 76px 的顶栏撑高；
  ② 装得下：9 项等宽、正好铺满整条；活动项下划线落在顶栏底边上；
  ③ 装不下：等宽不减、每项都不小于 min-width、文字没被挤出按钮，并浮出箭头
     （箭头只在「还能往那个方向滚」时出现，切页签后活动项自己滚进可视区）；
  ④ 切到侧边工作台时顶栏恢复原样（页签条不占位、左导航接管），再切回来还得对。
     两种布局下主列页签头都是 display:none —— 三栏经典已删，不再有「页签头回来」这一态。
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

from temp_server import serve                     # noqa: E402
from playwright.sync_api import sync_playwright   # noqa: E402

OK, BAD = [], []
SHOTS = TESTS_DIR.parent / ".workbuddy" / "shots"

# 页签的 min-width（与 style.css 的 .tt-tab 对齐）：低于它文字会被挤出按钮，
# 所以这是「等宽」在窄屏下的下限，也是「装不下就滚动」的触发点。
TAB_MIN_W = 84
TAB_PAD_X = 6          # .tt-tab 的左右内边距

# 一次量齐所有几何：写成一个表达式，避免多次 evaluate 之间界面变化
GEO = """() => {
  const q = (s) => document.querySelector(s);
  const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return {left: Math.round(b.left), right: Math.round(b.right), top: Math.round(b.top),
            bottom: Math.round(b.bottom), w: Math.round(b.width), h: Math.round(b.height)}; };
  const bar = q('.topbar'), tabs = q('.topbar-tabs'), strip = q('.tt-strip');
  const prev = q('.tt-arrow.prev'), next = q('.tt-arrow.next'), active = q('.tt-tab.active');
  const vis = (el) => { if (!el) return false; const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden'
           && el.getBoundingClientRect().width > 0; };
  const head = q('#tabs > .el-tabs__header');
  const sb = strip ? strip.getBoundingClientRect() : null;
  const ab = active ? active.getBoundingClientRect() : null;
  const items = [...document.querySelectorAll('.tt-tab')].map((el) => {
    const lb = el.querySelector('.tt-label');
    return {label: el.textContent.trim(), w: Math.round(el.getBoundingClientRect().width),
            left: Math.round(el.getBoundingClientRect().left),
            right: Math.round(el.getBoundingClientRect().right),
            labelW: lb ? Math.round(lb.getBoundingClientRect().width) : null};
  });
  const actLabel = active ? active.querySelector('.tt-label') : null;
  // 顶栏右侧操作组里相邻控件的水平间距（曾因 .el-button + .el-button 的 12px 外边距
  // 叠上 10px gap 变成 22px，用户反馈「三个图标之间太开」）
  const acts = q('.top-actions');
  const kids = acts ? [...acts.children].map((el) => el.getBoundingClientRect()) : [];
  const gaps = [];
  for (let i = 1; i < kids.length; i++) gaps.push(Math.round(kids[i].left - kids[i - 1].right));
  let covered = null;
  if (ab) {
    covered = false;
    for (const a of [prev, next]) {
      if (!vis(a)) continue;
      const x = a.getBoundingClientRect();
      if (ab.left < x.right - 1 && ab.right > x.left + 1) covered = true;
    }
  }
  return {
    bar: r(bar), brand: r(q('.brand')), tabs: r(tabs), actions: r(q('.top-actions')), strip: r(strip),
    tabsHidden: tabs ? tabs.classList.contains('hidden') : null,
    scrollLeft: strip ? Math.round(strip.scrollLeft) : null,
    maxScroll: strip ? Math.round(strip.scrollWidth - strip.clientWidth) : null,
    tabCount: items.length,
    items,
    tabMinW: items.length ? Math.min(...items.map((i) => i.w)) : null,
    tabMaxW: items.length ? Math.max(...items.map((i) => i.w)) : null,
    prevVisible: vis(prev), nextVisible: vis(next),
    headDisplay: head ? getComputedStyle(head).display : null,
    headH: head ? Math.round(head.getBoundingClientRect().height) : null,
    activeLabel: active ? active.textContent.trim() : null,
    activeInView: (sb && ab) ? (ab.left >= sb.left - 1 && ab.right <= sb.right + 1) : null,
    activeCovered: covered,
    // 活动项下划线（.tt-label::after，bottom:0）的底边 = 标签盒的底边
    underlineBottom: actLabel ? Math.round(actLabel.getBoundingClientRect().bottom) : null,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    barH: bar ? Math.round(bar.getBoundingClientRect().height) : null,
    gaps,
  };
}"""


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


def main() -> int:
    errors = []
    SHOTS.mkdir(parents=True, exist_ok=True)

    with serve(kb="fixture") as srv:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                    if m.type == "error" else None)

            page.goto(srv.base + "/", wait_until="domcontentloaded")
            page.evaluate("() => localStorage.setItem('ui-layout', 'top')")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".tt-tab", timeout=10000)
            page.wait_for_timeout(500)
            g = page.evaluate(GEO)

            # ── ① 位置：页签条在顶栏内、品牌右侧，且没把顶栏撑高 ──
            check("顶部导航：页签条挂在顶栏里（未隐藏）", g["tabsHidden"] is False, str(g["tabsHidden"]))
            check("顶部导航：页签条紧贴品牌右侧",
                  g["tabs"] and g["brand"] and 0 <= g["tabs"]["left"] - g["brand"]["right"] <= 12,
                  f"tabsLeft={g['tabs'] and g['tabs']['left']} brandRight={g['brand'] and g['brand']['right']}")
            check("顶部导航：页签条竖直落在顶栏内、顶栏没被撑高",
                  g["tabs"]["top"] >= g["bar"]["top"] and g["tabs"]["bottom"] <= g["bar"]["bottom"] + 1
                  and g["barH"] == 76,
                  f"tabs={g['tabs']['top']}~{g['tabs']['bottom']} barH={g['barH']}")
            check("顶部导航：右侧操作组紧接页签条（中间无大空隙）",
                  g["actions"]["left"] - g["tabs"]["right"] <= 24,
                  f"gap={g['actions']['left'] - g['tabs']['right']}")
            check("顶部导航：主列里的页签头已让位（display:none）",
                  g["headDisplay"] == "none" and g["headH"] == 0,
                  f"display={g['headDisplay']} h={g['headH']}")
            check("页签条里 9 个页签齐全", g["tabCount"] == 9, str(g["tabCount"]))
            check("顶部导航下页面没有横向溢出", g["docOverflowX"] <= 0, str(g["docOverflowX"]))

            # ── ② 装得下：等宽 + 铺满整条 + 下划线压在顶栏底边上 ──
            check("1440 装得下（没有滚动余量）", g["maxScroll"] <= 0, str(g["maxScroll"]))
            check("1440 装得下时两端都不出箭头",
                  g["prevVisible"] is False and g["nextVisible"] is False,
                  f"prev={g['prevVisible']} next={g['nextVisible']}")
            check("9 项等宽（最宽−最窄 ≤ 2px）",
                  g["tabMaxW"] - g["tabMinW"] <= 2,
                  f"{[i['w'] for i in g['items']]}")
            check("等宽的 9 项正好铺满整条（首项贴左端、末项贴右端）",
                  abs(g["items"][0]["left"] - g["strip"]["left"]) <= 2
                  and abs(g["items"][-1]["right"] - g["strip"]["right"]) <= 2,
                  f"首 {g['items'][0]['left']} vs 条左 {g['strip']['left']}；"
                  f"末 {g['items'][-1]['right']} vs 条右 {g['strip']['right']}")
            check("活动项下划线落在顶栏底边上（文字宽度、不是整格宽）",
                  abs(g["underlineBottom"] - g["bar"]["bottom"]) <= 1
                  and g["items"][0]["labelW"] < g["items"][0]["w"],
                  f"下划线底 {g['underlineBottom']} vs 顶栏底 {g['bar']['bottom']}；"
                  f"标签 {g['items'][0]['labelW']} < 格宽 {g['items'][0]['w']}")
            check("右侧图标按钮之间不再是 22px（顶栏内统一 10px 间距）",
                  max(g["gaps"]) <= 12, str(g["gaps"]))
            page.screenshot(path=str(SHOTS / "topbar_tabs_1440.png"))

            # ── ③ 装不下：等宽不减、每项不小于 min-width、文字没被挤出按钮 ──
            page.set_viewport_size({"width": 1200, "height": 900})
            page.wait_for_timeout(700)
            g2 = page.evaluate(GEO)
            check("1200 视口装不下（maxScroll > 0）", g2["maxScroll"] > 0, str(g2["maxScroll"]))
            check("装不下时仍是等宽（最宽−最窄 ≤ 2px）",
                  g2["tabMaxW"] - g2["tabMinW"] <= 2,
                  f"{[i['w'] for i in g2['items']]}")
            check(f"装不下时每项都不小于 min-width（{TAB_MIN_W}px）",
                  g2["tabMinW"] >= TAB_MIN_W,
                  f"最窄={g2['tabMinW']}")
            check("装不下时文字没被挤出按钮（标签宽 ≤ 格宽 − 左右内边距）",
                  all(i["labelW"] <= i["w"] - 2 * TAB_PAD_X for i in g2["items"]),
                  "; ".join(f"{i['label']}:{i['labelW']}/{i['w']}" for i in g2["items"]
                            if i["labelW"] > i["w"] - 2 * TAB_PAD_X) or "全部装得下")
            check("停在左端：无左箭头、有右箭头",
                  g2["prevVisible"] is False and g2["nextVisible"] is True,
                  f"prev={g2['prevVisible']} next={g2['nextVisible']}")
            page.screenshot(path=str(SHOTS / "topbar_tabs_1200.png"))

            page.click(".tt-arrow.next")
            page.wait_for_timeout(700)
            g3 = page.evaluate(GEO)
            check("点右箭头会向右滚（滚到末尾）",
                  g3["scrollLeft"] > g2["scrollLeft"] and g3["scrollLeft"] >= g3["maxScroll"] - 2,
                  f"{g2['scrollLeft']} → {g3['scrollLeft']} / max {g3['maxScroll']}")
            check("滚到末尾：左箭头出现、右箭头消失",
                  g3["prevVisible"] is True and g3["nextVisible"] is False,
                  f"prev={g3['prevVisible']} next={g3['nextVisible']}")

            # 切页签：内容切换 + 活动页签自动滚进可视区、不被箭头压住
            page.evaluate("() => document.querySelector('.tt-strip').scrollTo({left: 0})")
            page.wait_for_timeout(400)
            page.evaluate("() => document.querySelectorAll('.tt-tab')[8].click()")   # 配置（最右）
            page.wait_for_timeout(900)
            g4 = page.evaluate(GEO)
            check("点顶栏页签能切换内容（配置）", g4["activeLabel"] == "配置", str(g4["activeLabel"]))
            check("切页签后活动页签自己滚进了可视区", g4["activeInView"] is True, str(g4["strip"]))
            check("活动页签没有被浮动箭头压住", g4["activeCovered"] is False, str(g4["activeLabel"]))
            page.screenshot(path=str(SHOTS / "topbar_tabs_1200_last.png"))

            # 全局 switchTab 是既有契约（12 个界面用例直接调它），它也得带动滚动
            page.evaluate("() => document.querySelector('.tt-strip').scrollTo({left: 0})")
            page.wait_for_timeout(400)
            page.evaluate("() => switchTab('extensions')")
            page.wait_for_timeout(900)
            check("switchTab 全局契约仍生效（pane 变活动）",
                  page.eval_on_selector("#pane-extensions", "el => el.classList.contains('active')"))
            g5 = page.evaluate(GEO)
            check("switchTab 切到最右页签后同样自动滚进来",
                  g5["activeLabel"] == "扩展能力" and g5["activeInView"] is True
                  and g5["activeCovered"] is False,
                  f"left={g5['scrollLeft']} inView={g5['activeInView']}")

            # 初态活动页签（统计，在最左）也不该被箭头压住
            page.evaluate("() => switchTab('stats')")
            page.wait_for_timeout(900)
            g6 = page.evaluate(GEO)
            check("回到第一个页签：完整可见且没被箭头压住",
                  g6["activeInView"] is True and g6["activeCovered"] is False, str(g6["activeLabel"]))

            # ── ④ 宽度变化：宽屏铺满、窄屏兜底 ──
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.wait_for_timeout(800)
            g7 = page.evaluate(GEO)
            check("1920 宽：不出箭头、每项更宽但仍等宽且铺满",
                  g7["maxScroll"] <= 0 and g7["nextVisible"] is False
                  and g7["tabMaxW"] - g7["tabMinW"] <= 2
                  and abs(g7["items"][-1]["right"] - g7["strip"]["right"]) <= 2
                  and g7["tabMinW"] > g["tabMinW"],
                  f"每项 {g7['tabMinW']}~{g7['tabMaxW']}（1440 时 {g['tabMinW']}）")
            check("1920 宽：顶栏高度仍是 76、下划线仍压在底边",
                  g7["barH"] == 76 and abs(g7["underlineBottom"] - g7["bar"]["bottom"]) <= 1,
                  f"barH={g7['barH']} 下划线底={g7['underlineBottom']}")
            page.screenshot(path=str(SHOTS / "topbar_tabs_1920.png"))

            page.set_viewport_size({"width": 1100, "height": 900})
            page.wait_for_timeout(800)
            g8 = page.evaluate(GEO)
            check("1100 窄屏：页面不横向溢出、页签条仍可滚",
                  g8["docOverflowX"] <= 0 and g8["tabs"]["bottom"] <= g8["bar"]["bottom"] + 1
                  and g8["maxScroll"] > 0,
                  f"overflowX={g8['docOverflowX']} max={g8['maxScroll']}")
            check("1100 窄屏：品牌与操作组没被页签条挤变形",
                  g8["brand"]["w"] == g["brand"]["w"] and g8["actions"]["w"] == g["actions"]["w"],
                  f"brand {g['brand']['w']}→{g8['brand']['w']} actions {g['actions']['w']}→{g8['actions']['w']}")
            page.screenshot(path=str(SHOTS / "topbar_tabs_1100.png"))

            # ──  换布局：切到侧边工作台顶栏页签条要让位，再切回来还得对 ──
            # （原来这里还有一段「切回三栏经典：主列页签头恢复显示」—— 三栏布局已删，
            #   两种布局都由自己的导航承担切页签，.el-tabs__header 恒为 display:none。）
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(300)
            page.locator(".layout-switch .ls-btn").nth(0).click()     # 侧边工作台
            page.wait_for_timeout(700)
            g9 = page.evaluate(GEO)
            check("切到侧边工作台：顶栏页签条隐藏、不再占位（操作组没被推走）",
                  g9["tabsHidden"] is True and g9["actions"]["left"] >= g["actions"]["left"] - 2,
                  f"hidden={g9['tabsHidden']} actionsLeft={g9['actions']['left']}")
            check("切到侧边工作台：主列页签头同样隐藏（只剩左侧分组导航一套导航）",
                  g9["headDisplay"] == "none", f"display={g9['headDisplay']}")
            check("切到侧边工作台：左侧分组导航有全部 9 个入口",
                  page.locator(".side-nav .nav-item").count() == 9
                  and page.locator(".side-nav").is_visible(),
                  str(page.locator(".side-nav .nav-item").count()))
            check("切到侧边工作台：顶栏高度不变、图标间距同样收紧",
                  g9["barH"] == 76 and max(g9["gaps"]) <= 12,
                  f"barH={g9['barH']} gaps={g9['gaps']}")
            page.screenshot(path=str(SHOTS / "topbar_tabs_side.png"))

            page.locator(".layout-switch .ls-btn").nth(1).click()     # 回到顶部导航
            page.wait_for_timeout(800)
            g10 = page.evaluate(GEO)
            check("切回顶部导航：页签条、让位、活动页签位置都对",
                  g10["tabsHidden"] is False and g10["headDisplay"] == "none"
                  and g10["activeInView"] is True,
                  f"hidden={g10['tabsHidden']} head={g10['headDisplay']}")

            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".tt-tab", timeout=10000)
            page.wait_for_timeout(600)
            check("刷新后仍是顶部导航（localStorage 记忆生效）",
                  page.evaluate("() => !document.querySelector('.topbar-tabs').classList.contains('hidden')"))

            check("无 pageerror", not [e for e in errors if e.startswith("pageerror")],
                  "; ".join(errors)[:200])
            check("无控制台报错", not [e for e in errors if e.startswith("console")],
                  "; ".join(errors)[:200])
            browser.close()

    print(f"\n{'全部通过' if not BAD else '失败 %d 项：%s' % (len(BAD), '；'.join(BAD))}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
