<script setup>
// 消耗趋势：堆叠柱（输入 + 输出）。旧版 app.js:2906 `usageRenderTrend` 的等价复刻。
//
// 手绘 SVG 而不是引图表库：项目要能在内网离线跑，多一个运行时依赖就多一个
// 离线安装的坑。坐标计算逻辑逐行搬运，只把 innerHTML 拼串换成模板绑定。
import { computed } from 'vue'
import { fmtTok, fmtInt, niceStep } from '../utils/format.js'

const props = defineProps({
  buckets: { type: Array, default: () => [] },
  granularity: { type: String, default: 'day' },
})

const W = 1000, H = 250, PL = 54, PR = 14, PT = 14, PB = 34
const iw = W - PL - PR
const ih = H - PT - PB

const granText = computed(() =>
  ({ day: '每天', week: '每周', month: '每月' }[props.granularity] || '每天')
)

const hasData = computed(() => props.buckets.some((b) => b.total))

const peak = computed(() => Math.max(1, ...props.buckets.map((b) => b.total)))
const stepv = computed(() => niceStep(peak.value / 4))
const top = computed(() => stepv.value * 4)
const slot = computed(() => iw / (props.buckets.length || 1))
const bw = computed(() => Math.max(1.5, Math.min(30, slot.value * 0.68)))
const labelEvery = computed(() => Math.max(1, Math.ceil(props.buckets.length / 12)))

const yOf = (v) => PT + ih - (v / top.value) * ih

/** 4 条水平网格线 + 左侧刻度 */
const grid = computed(() => {
  const out = []
  for (let i = 0; i <= 4; i++) {
    const v = stepv.value * i
    const y = yOf(v)
    out.push({ y: y.toFixed(1), label: fmtTok(v) })
  }
  return out
})

/** 父组件头部右侧的说明文字 */
const metaText = computed(() => {
  if (!hasData.value) return ''
  const peakTok = Math.max(...props.buckets.map((b) => b.total))
  return `${granText.value} · ${props.buckets.length} 个刻度 · 峰值 ${fmtTok(peakTok)}`
})
defineExpose({ metaText, hasData })

/** 每根柱子的几何 + tooltip 文案（tooltip 与旧版逐字一致，含全角空格分隔） */
const bars = computed(() =>
  props.buckets.map((b, i) => {
    const cx = PL + slot.value * i + slot.value / 2
    const x = cx - bw.value / 2
    const hOut = (b.output / top.value) * ih
    const hIn = (b.input / top.value) * ih
    const yIn = PT + ih - hIn
    const yOut = yIn - hOut
    const tip =
      `${b.date || b.key}　合计 ${fmtInt(b.total)} tokens` +
      `\n输入 ${fmtInt(b.input)} · 输出 ${fmtInt(b.output)}` +
      `\n${b.calls} 次调用` +
      (b.estimated ? `（${b.estimated} 次估算）` : '') +
      (b.failed ? `（${b.failed} 次失败）` : '')
    return {
      key: b.date || b.key || i,
      tip,
      x: x.toFixed(1),
      bw: bw.value.toFixed(1),
      // 零高度柱子画一根 2px 的短桩，否则「这天有调用但 token 为 0」会看起来像没数据
      stub: !hIn && !hOut ? { y: (PT + ih - 2).toFixed(1) } : null,
      input: hIn ? { y: yIn.toFixed(1), h: Math.max(1, hIn).toFixed(1) } : null,
      output: hOut ? { y: yOut.toFixed(1), h: Math.max(1, hOut).toFixed(1) } : null,
    }
  })
)

const xlabels = computed(() =>
  props.buckets
    .map((b, i) =>
      i % labelEvery.value
        ? null
        : {
            key: b.date || b.key || i,
            x: (PL + slot.value * i + slot.value / 2).toFixed(1),
            label: b.label,
          }
    )
    .filter(Boolean)
)
</script>

<template>
  <div class="empty" v-if="!hasData">
    这个区间里还没有真实的模型调用记录。<br>
    切到「缺陷修复」跑一次工单，或在「长任务作业」里跑一次作业，这里就会出现用量曲线。
  </div>

  <template v-else>
    <div class="usage-legend">
      <span><i style="background:var(--c-in)" />输入</span>
      <span><i style="background:var(--c-out)" />输出</span>
    </div>
    <svg class="usage-svg" :viewBox="`0 0 ${W} ${H}`" role="img"
         :aria-label="`${granText} token 消耗趋势`">
      <g v-for="(g, i) in grid" :key="`g${i}`">
        <line :x1="PL" :y1="g.y" :x2="W - PR" :y2="g.y"
              stroke="var(--border)" stroke-width="1" />
        <text :x="PL - 8" :y="Number(g.y) + 4" text-anchor="end"
              class="usage-axis">{{ g.label }}</text>
      </g>
      <line :x1="PL" :y1="PT + ih" :x2="W - PR" :y2="PT + ih"
            stroke="var(--border-strong)" stroke-width="1" />
      <g v-for="b in bars" :key="b.key" class="usage-bar">
        <title>{{ b.tip }}</title>
        <rect v-if="b.input" :x="b.x" :y="b.input.y" :width="b.bw"
              :height="b.input.h" rx="2" fill="var(--c-in)" />
        <rect v-if="b.output" :x="b.x" :y="b.output.y" :width="b.bw"
              :height="b.output.h" rx="2" fill="var(--c-out)" />
        <rect v-if="b.stub" :x="b.x" :y="b.stub.y" :width="b.bw"
              height="2" rx="1" fill="var(--border-strong)" />
      </g>
      <text v-for="l in xlabels" :key="l.key" :x="l.x" :y="H - 12"
            text-anchor="middle" class="usage-axis">{{ l.label }}</text>
    </svg>
  </template>
</template>
