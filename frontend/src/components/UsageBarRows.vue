<script setup>
// 横向条列表（按任务类型 / 按模型共用）。旧版 app.js:2986 `usageBarRows` 的等价复刻。
//
// 条宽不是按百分比铺满，而是按「相对最大值」缩放 —— 这样第一行永远顶满，
// 一眼能看出谁是主要消耗方；右侧仍显示真实占比。
import { computed } from 'vue'
import { fmtInt } from '../utils/format.js'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  labelOf: { type: Function, default: (r) => r.label || r.key },
  palette: { type: Array, default: () => [] },
})

const total = computed(() => props.rows.reduce((s, r) => s + r.total, 0) || 1)
const max = computed(() => Math.max(1, ...props.rows.map((r) => r.total)))

const items = computed(() =>
  props.rows.map((r, i) => ({
    key: r.key || i,
    label: props.labelOf(r),
    calls: fmtInt(r.calls),
    estimated: r.estimated,
    failed: r.failed,
    total: fmtInt(r.total),
    pct: ((r.total / total.value) * 100).toFixed(1),
    width: Math.max(1, (r.total / max.value) * 100).toFixed(1),
    color: props.palette[i % (props.palette.length || 1)],
  }))
)
</script>

<template>
  <div class="usage-row" v-for="it in items" :key="it.key">
    <div class="usage-row-label" :title="it.label">{{ it.label }}</div>
    <div class="usage-row-track">
      <i :style="{ width: it.width + '%', background: it.color }" />
    </div>
    <div class="usage-row-val">{{ it.total }}
      <span class="muted">{{ it.pct }}%</span>
    </div>
    <div class="usage-row-sub">{{ it.calls }} 次<span v-if="it.estimated"> · {{ it.estimated }} 估算</span><span v-if="it.failed"> · {{ it.failed }} 失败</span></div>
  </div>
</template>
