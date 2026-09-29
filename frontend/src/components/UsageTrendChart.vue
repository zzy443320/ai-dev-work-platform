<script setup>
// 消耗趋势：堆叠柱（输入 + 输出）。旧版 app.js:2906 `usageRenderTrend` 的口径复刻，
// 但绘制交给 ECharts —— 换来入场动画（柱子依次长出）、hover 高亮、跟随光标的富
// tooltip、点击图例隐藏某个系列。
//
// 口径必须保持一致的三处（界面用例锁着）：
//   1. 柱子数 = 分桶数（**空桶也在**，有调用但 0 token 的那天不能凭空消失）；
//   2. 每根柱的 tooltip 文案：日期 + 合计/输入/输出/调用次数（+ 估算/失败）；
//   3. y 轴刻度 = niceStep(峰值/4) 的四等分，文案走 fmtTok。
// tooltip 文案改成「先算成 rows[].tip，再由 formatter 原样吐出来」——这样文案只
// 有一处来源，界面用例也能直接断言（见 EChart.vue 头部的 __probe 约定）。
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
// ECharts 走异步组件：静态 import 会把它一起打进入口 app.js（实测 287KB → 814KB），
// 拆出来是一个独立 chunk，首屏只多一次本地静态请求。
const EChart = defineAsyncComponent(() => import('./EChart.vue'))
import { fmtTok, fmtInt, niceStep } from '../utils/format.js'
import { readChartColors } from '../utils/chartTheme.js'
import { useTheme } from '../composables/useTheme.js'

const props = defineProps({
  buckets: { type: Array, default: () => [] },
  granularity: { type: String, default: 'day' },
})

const { theme } = useTheme()

const CHART_H = 250
// 旧的 viewBox 内边距（1000×250 里 PL/PR/PT/PB）直接当 ECharts 的 grid 像素边距，
// 保证轴刻度位置与旧版视觉一致。
const PL = 54, PR = 14, PT = 14, PB = 34

const wrap = ref(null)
const colors = ref(null)

function readColors() {
  if (wrap.value) colors.value = readChartColors(wrap.value)
}
onMounted(readColors)
// 主题换了要重算色值——ECharts 拿不到 CSS 变量，必须给具体色值
watch(theme, readColors)

const granText = computed(() =>
  ({ day: '每天', week: '每周', month: '每月' }[props.granularity] || '每天')
)

const hasData = computed(() => props.buckets.some((b) => b.total))

const peak = computed(() => Math.max(1, ...props.buckets.map((b) => b.total)))
const stepv = computed(() => niceStep(peak.value / 4))
const top = computed(() => stepv.value * 4)

/** y 轴刻度（5 条：0 到 top 的四等分） */
const axis = computed(() => {
  const out = []
  for (let i = 0; i <= 4; i++) out.push(fmtTok(stepv.value * i))
  return out
})

/** 父组件头部右侧的说明文字 */
const metaText = computed(() => {
  if (!hasData.value) return ''
  const peakTok = Math.max(...props.buckets.map((b) => b.total))
  return `${granText.value} · ${props.buckets.length} 个刻度 · 峰值 ${fmtTok(peakTok)}`
})

/** 每个分桶一行：绘图数值 + tooltip 文案（文案与旧版逐字一致，含全角空格分隔） */
const rows = computed(() =>
  props.buckets.map((b) => ({
    key: b.date || b.key || '',
    label: b.label || b.key || '',
    total: b.total || 0,
    input: b.input || 0,
    output: b.output || 0,
    tip:
      `${b.date || b.key}　合计 ${fmtInt(b.total)} tokens` +
      `\n输入 ${fmtInt(b.input)} · 输出 ${fmtInt(b.output)}` +
      `\n${b.calls} 次调用` +
      (b.estimated ? `（${b.estimated} 次估算）` : '') +
      (b.failed ? `（${b.failed} 次失败）` : ''),
  }))
)

// 图例点击隐藏系列：把该系列的值全置 0（视觉上等于整段消失，且柱子槽位数不变）
const off = ref({ input: false, output: false })
function toggleSeries(which) {
  off.value = { ...off.value, [which]: !off.value[which] }
}

const option = computed(() => (colors.value ? buildOption() : {}))

function buildOption() {
  const c = colors.value
  const vals = (name) => rows.value.map((r) => (off.value[name] ? 0 : r[name]))
  return {
    // 柱子依次长出；数据更新（切粒度/区间）走一次平滑过渡
    animationDelay: (i) => i * 12,
    grid: { left: PL, right: PR, top: PT, bottom: PB, containLabel: false },
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(148, 163, 184, 0.14)' } },
      backgroundColor: c.panel,
      borderColor: c.axisLine,
      textStyle: { color: c.text, fontSize: 11.5 },
      formatter: (ps) => {
        const r = rows.value[ps && ps.length ? ps[0].dataIndex : -1]
        return r ? r.tip.replace(/\n/g, '<br/>') : ''
      },
    },
    xAxis: {
      type: 'category',
      data: rows.value.map((r) => r.label),
      // 刻度太密就隔几个显示一个（旧版 labelEvery 的等价写法）
      axisLabel: {
        color: c.axis, fontSize: 10, fontFamily: 'ui-monospace, Consolas, monospace',
        interval: Math.max(0, Math.ceil(rows.value.length / 12) - 1),
      },
      axisLine: { lineStyle: { color: c.axisLine } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: top.value,
      interval: stepv.value,
      axisLabel: {
        color: c.axis, fontSize: 10, fontFamily: 'ui-monospace, Consolas, monospace',
        formatter: (v) => fmtTok(v),
      },
      splitLine: { lineStyle: { color: c.grid } },
      axisLine: { show: false },
    },
    series: [
      {
        id: 'in', name: '输入', type: 'bar', stack: 'tok',
        barMaxWidth: 30, barCategoryGap: '32%',
        data: vals('input'),
        itemStyle: { color: c.input },
        // 槽位底色：让「这天有记录但 token 为 0」看得出来，不至于像断档。
        // 用低透明度的中性色而不是 --border（实测 0.16 的灰在两套主题里都偏重）。
        showBackground: true,
        backgroundStyle: { color: 'rgba(148, 163, 184, 0.10)', borderRadius: 2 },
      },
      {
        id: 'out', name: '输出', type: 'bar', stack: 'tok',
        barMaxWidth: 30,
        data: vals('output'),
        itemStyle: { color: c.output, borderRadius: [2, 2, 0, 0] },
      },
    ],
  }
}

/** 给界面用例读的语义化数据（EChart.vue 头部的 __probe 约定） */
const probe = computed(() => ({
  kind: 'trend',
  axis: axis.value,
  rows: rows.value,
  series: [
    { name: '输入', data: rows.value.map((r) => (off.value.input ? 0 : r.input)) },
    { name: '输出', data: rows.value.map((r) => (off.value.output ? 0 : r.output)) },
  ],
}))

defineExpose({ metaText, hasData })
</script>

<template>
  <div ref="wrap" class="usage-chart-inner">
    <div class="empty" v-if="!hasData">
      这个区间里还没有真实的模型调用记录。<br>
      切到「缺陷修复」跑一次工单，或在「长任务作业」里跑一次作业，这里就会出现用量曲线。
    </div>

    <template v-else>
      <div class="usage-legend">
        <span class="clickable" :class="{ off: off.input }" title="点击显示 / 隐藏该系列"
              @click="toggleSeries('input')">
          <i style="background:var(--c-in)" />输入
        </span>
        <span class="clickable" :class="{ off: off.output }" title="点击显示 / 隐藏该系列"
              @click="toggleSeries('output')">
          <i style="background:var(--c-out)" />输出
        </span>
      </div>
      <EChart :option="option" :height="CHART_H" :probe="probe" />
    </template>
  </div>
</template>
