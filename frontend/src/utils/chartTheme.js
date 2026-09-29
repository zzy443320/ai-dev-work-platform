// 图表取色：ECharts 的 option 里颜色必须是**具体色值**（canvas/svg 都不认 CSS 变量），
// 而本项目的配色全在 style.css 的变量里，还要跟着浅色/深色主题走。
// 所以统一在这里「从元素身上读计算后的变量值」——读完之后主题一换就重算一次
// （组件里 watch(theme) 触发），不要自己写死色值。

/** 把 `var(--x)` 解析成当前主题下的实际色值；已经是具体色值就原样返回。 */
export function resolveColor(el, c) {
  const s = String(c == null ? '' : c).trim()
  const m = s.match(/^var\(\s*(--[A-Za-z0-9_-]+)/)
  if (!m || !el) return s
  const v = String(getComputedStyle(el).getPropertyValue(m[1]) || '').trim()
  return v || s
}

/**
 * 从某个元素（通常是图表容器，它继承了 .usage-chart 上的 --c-in/--c-out）读出
 * 画图要用的全部颜色。fallback 只是兜底，正常情况下 style.css 里都有定义。
 */
export function readChartColors(el) {
  const cs = el ? getComputedStyle(el) : null
  const g = (name, fb) => {
    const v = cs ? String(cs.getPropertyValue(name) || '').trim() : ''
    return v || fb
  }
  return {
    input: g('--c-in', '#6366f1'),
    output: g('--c-out', '#14b8a6'),
    axis: g('--muted', '#94a3b8'),
    grid: g('--border', 'rgba(148, 163, 184, 0.18)'),
    axisLine: g('--border-strong', 'rgba(148, 163, 184, 0.35)'),
    text: g('--text', '#e5e7eb'),
    panel: g('--panel-solid', '#1e293b'),
  }
}
