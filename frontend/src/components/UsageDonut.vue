<script setup>
// 模型占比环形图。旧版 app.js:3035 `usageDonut` 的等价复刻。
//
// 两个分支要留意：只有 1 个模型时画整圈（单条 path 的 arc 首尾重合会变成空路径），
// 这也是旧版特意分开写的理由。
import { computed } from 'vue'
import { fmtTok, fmtInt } from '../utils/format.js'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  palette: { type: Array, default: () => [] },
})

const CX = 108, CY = 104, RO = 78, RI = 52

const total = computed(() => props.rows.reduce((s, r) => s + r.total, 0))

const single = computed(() => props.rows.length === 1)
const singleWidth = RO - RI

const colorAt = (i) => props.palette[i % (props.palette.length || 1)]

const arcPath = (rad, ang) => [
  (CX + rad * Math.cos(ang)).toFixed(2),
  (CY + rad * Math.sin(ang)).toFixed(2),
]

/** 逐段扫出扇环路径。起始角 -90°（12 点方向），顺时针铺满一圈。 */
const arcs = computed(() => {
  if (single.value || !total.value) return []
  let a = -Math.PI / 2
  return props.rows.map((r, i) => {
    const sweep = (r.total / total.value) * Math.PI * 2
    const a0 = a
    const a1 = a + sweep
    a = a1
    const large = sweep > Math.PI ? 1 : 0
    const [x0, y0] = arcPath(RO, a0)
    const [x1, y1] = arcPath(RO, a1)
    const [x2, y2] = arcPath(RI, a1)
    const [x3, y3] = arcPath(RI, a0)
    const d = `M${x0} ${y0} A${RO} ${RO} 0 ${large} 1 ${x1} ${y1}` +
      ` L${x2} ${y2} A${RI} ${RI} 0 ${large} 0 ${x3} ${y3} Z`
    const pct = ((r.total / total.value) * 100).toFixed(1)
    return {
      key: r.key,
      d,
      fill: colorAt(i),
      tip: `${r.key}：${fmtInt(r.total)} tokens（${pct}%）`,
    }
  })
})
</script>

<template>
  <svg class="usage-svg usage-donut" viewBox="0 0 216 208" role="img" aria-label="模型占比">
    <!-- 单模型：直接画一个粗描边圆环，比 arc 路径稳（首尾重合的 arc 会退化成空路径） -->
    <circle v-if="single" :cx="CX" :cy="CY" :r="(RO + RI) / 2" fill="none"
            stroke="var(--primary)" :stroke-width="singleWidth" />
    <path v-for="a in arcs" v-else :key="a.key" :d="a.d" :fill="a.fill">
      <title>{{ a.tip }}</title>
    </path>
    <text :x="CX" :y="CY - 2" text-anchor="middle" class="usage-donut-total">{{ fmtTok(total) }}</text>
    <text :x="CX" :y="CY + 16" text-anchor="middle" class="usage-donut-cap">tokens</text>
  </svg>
</template>
