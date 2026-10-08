<script setup>
// ECharts 通用壳。全项目只有统计页签用图，所以统一走这里，好处是：
//   1. **按需注册**：只引 Bar/Pie + Grid/Tooltip + SVG 渲染器。
//      这条一直是生效的，别再来"修"一次：2026-10-08 实测过，把 `echarts/charts`
//      换成逐个 `lib/chart/bar/install.js` 深路径，chunk 体积**一位小数都没变**
//      （517.29 kB）。产物里 grep 到的 sankey/candlestick/treemap 是
//      lib/i18n/langZH.js 的中文名称表（"桑基图"、"K线图"），不是图表实现——
//      sunburstBeginAngle / getSectorForm / sankeyCircular 这类实现独有符号全为 0。
//      517KB 就是 echarts 6 的 core + 两种图 + Grid/Tooltip + SVG 渲染器的真实成本；
//      想再降只能换更轻的图表库或砍功能，不是靠改 import 写法。
//      （旧注释说"`import * as echarts` 会让 tree-shaking 失效、chunk 从 ~300KB
//      涨到 517KB"，那个 300KB 无从复现，已按实测改掉。）
//   2. **SVG 渲染器**：产物是真实 DOM，截图与主题调试都能直接看，小图也更清晰；
//   3. **给界面用例留读取口**：ECharts 画出来的节点没有我们的 class，
//      `tests/check_vue_stats_ui.py` / `check_usage_ui.py` 靠宿主节点上的
//      `__ec`（实例，可 `getOption()` 断言系列数据）与 `__probe`（父组件给的
//      语义化数据：每根柱/每个扇区的 tooltip 文案、轴刻度）来断言，
//      比数 SVG 节点稳。**这两个字段是对外契约，改名要同步改用例。**
//
// 本组件由父组件用 defineAsyncComponent 异步加载（ECharts 单独一个 chunk），
// 所以挂载/卸载的时序可能在数据到达之后，probe 与 option 都用 watch 兜住。
//
// 主题：ECharts 不认 CSS 变量，颜色由父组件用 utils/chartTheme.js 解析成具体色值后
// 写进 option，主题切换时父组件重算 option → 这里的 watch 重设即可，本组件不碰主题。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import { BarChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'

use([BarChart, PieChart, GridComponent, TooltipComponent, SVGRenderer])

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: Number, default: 250 },
  /** 给界面用例读的语义化数据（见文件头注释），会挂到宿主节点的 __probe 上 */
  probe: { type: Object, default: null },
})

const host = ref(null)
let chart = null
let ro = null

function render() {
  if (!chart) return
  chart.setOption(
    // 默认动效放在前面，父组件的 option 可以覆盖。notMerge 是刻意的：
    // 切粒度/区间时柱子数量会变，合并旧 series 会留下脏数据。
    { animation: true, animationDuration: 600, animationEasing: 'cubicOut',
      animationDurationUpdate: 400, ...props.option },
    { notMerge: true }
  )
}

function applyProbe() {
  const el = host.value
  if (el) el.__probe = props.probe
}

onMounted(() => {
  chart = init(host.value, null, { renderer: 'svg' })
  host.value.__ec = chart
  applyProbe()
  render()
  // 布局是响应式的（.usage-split 在窄屏会换行），窗口/容器变化都要跟着重画
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      // 页签被 display:none 时宽高是 0，这时候 resize 会报警告，跳过
      if (chart && host.value && host.value.clientWidth > 0) chart.resize()
    })
    ro.observe(host.value)
  }
})

watch(() => props.option, render, { deep: true })
watch(() => props.probe, applyProbe, { deep: true })

onBeforeUnmount(() => {
  if (ro) { ro.disconnect(); ro = null }
  if (chart) { chart.dispose(); chart = null }
})

defineExpose({ chart: () => chart })
</script>

<template>
  <div ref="host" class="echart-host" :style="{ height: height + 'px' }" />
</template>
