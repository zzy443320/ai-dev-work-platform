// ── 顶部导航布局（top）的页签条：横向滚动 + 两端浮层箭头 ──────────────
//
// 从 App.vue 拆出来的（2026-10-08）。App.vue 是装配点，不该同时管页签状态机、
// 布局切换和「量滚动条尺寸」这件事；这段逻辑自成一件事：输入 activeTab / layoutMode，
// 输出容器 ref、箭头显隐位与两个滚动动作，别处不引用它的内部函数。
//
// 三个刻意的实现选择，改之前先读：
//   1. 箭头是 **absolute 浮层**，不占布局宽度。若让它占位，「箭头出现 → 条变窄 →
//      更该出现」是单向自锁，而反向又会在阈值附近来回翻，ResizeObserver 直接自激；
//      浮层化之后显隐不改变任何盒模型，回路从根上没了。
//   2. 箭头只在**真的还能往那个方向滚**时出现（停在左端就没有左箭头）——
//      比「禁用态的灰箭头」语义清楚，也少两个常驻控件。
//   3. 切页签后把活动页签滚进可视区，并按箭头宽度内缩，否则点最靠右的页签时
//      它会被右箭头压住一半。
//
// 用例契约：tests/check_vue_topbar_tabs.py 断言 .tt-arrow 的 hidden 类、
// .tt-strip 的 has-prev/has-next、溢出时的可滚动宽度，以及换布局后的复原。
// 改名前先想它。
import { nextTick, onBeforeUnmount, onMounted, ref, unref, watch } from 'vue'

/** 浮动箭头会盖住条的两端，所以判定「可见」时两边各内缩这些像素 */
const TAB_ARROW_INSET = 30

export function useTabStrip({ activeTab, layoutMode }) {
  const tabStripEl = ref(null)
  const tabScrollable = ref(false)
  const tabAtStart = ref(true)
  const tabAtEnd = ref(true)   // 初始按「到头」处理：量之前不闪箭头

  function syncTabScroll() {
    const el = tabStripEl.value
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    tabScrollable.value = max > 2
    tabAtStart.value = el.scrollLeft <= 2
    tabAtEnd.value = el.scrollLeft >= max - 2
  }

  /** 一屏的 70%（至少 200px）：点一下保证换一批页签，而不是挪一格 */
  function scrollTabs(dir) {
    const el = tabStripEl.value
    if (!el) return
    const step = Math.max(200, Math.round(el.clientWidth * 0.7))
    el.scrollBy({ left: dir * step, behavior: 'smooth' })
  }

  function revealActiveTab() {
    const el = tabStripEl.value
    if (!el || unref(layoutMode) !== 'top') return
    const cur = el.querySelector('.tt-tab.active')
    if (!cur) return
    const view = el.getBoundingClientRect()
    const box = cur.getBoundingClientRect()
    // 只对**真会显示箭头的那一侧**内缩：停在左端时左边没有箭头，没必要白留 30px
    const left = tabScrollable.value && !tabAtStart.value ? TAB_ARROW_INSET : 0
    const right = tabScrollable.value && !tabAtEnd.value ? TAB_ARROW_INSET : 0
    if (box.left < view.left + left) {
      el.scrollBy({ left: box.left - view.left - left, behavior: 'smooth' })
    } else if (box.right > view.right - right) {
      el.scrollBy({ left: box.right - view.right + right, behavior: 'smooth' })
    }
  }

  // 切页签 / 换布局后重新量一次：不仅要更新箭头显隐，还要把活动页签带回可视区
  watch([activeTab, layoutMode], async () => {
    await nextTick()
    syncTabScroll()
    revealActiveTab()
  })

  let resizeObs = null
  onMounted(async () => {
    await nextTick()
    syncTabScroll()
    if (typeof ResizeObserver === 'undefined' || !tabStripEl.value) return
    // 窗口变宽变窄（或风格切换改了字号）都会改 clientWidth —— 只有这条能兜住
    resizeObs = new ResizeObserver(() => {
      syncTabScroll()
      revealActiveTab()
    })
    resizeObs.observe(tabStripEl.value)
  })
  onBeforeUnmount(() => {
    if (resizeObs) resizeObs.disconnect()
  })

  return {
    tabStripEl, tabScrollable, tabAtStart, tabAtEnd,
    scrollTabs, syncTabScroll,
  }
}
