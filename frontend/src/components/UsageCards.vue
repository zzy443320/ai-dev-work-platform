<script setup>
// 顶部五张汇总卡片。旧版 app.js:2861 `usageRenderCards` 的等价复刻。
import { fmtInt, fmtTok } from '../utils/format.js'

defineProps({
  cards: { type: Array, default: () => [] },
})

/** 「N 次调用 · 均 1.2k · 3 次估算」——与旧版 usageTotLine 逐字一致，含分隔符 */
function totLine(t) {
  t = t || {}
  if (!t.calls) return '暂无调用'
  const bits = [`${fmtInt(t.calls)} 次调用`]
  if (t.avg) bits.push(`均 ${fmtTok(t.avg)}`)
  if (t.estimated) bits.push(`${t.estimated} 次估算`)
  if (t.failed) bits.push(`${t.failed} 次失败`)
  return bits.join(' · ')
}
</script>

<template>
  <div class="usage-card" :class="{ primary: c.primary }" v-for="c in cards" :key="c.label">
    <div class="usage-card-label">{{ c.label }}</div>
    <div class="usage-card-value">{{ fmtInt((c.t || {}).total || 0) }}<span class="unit">tokens</span></div>
    <div class="usage-card-sub">{{ totLine(c.t) }}</div>
    <div class="usage-card-foot">
      <span title="输入 token">↑ {{ fmtTok((c.t || {}).input || 0) }}</span>
      <span title="输出 token">↓ {{ fmtTok((c.t || {}).output || 0) }}</span>
      <span class="muted">{{ c.sub }}</span>
    </div>
  </div>
</template>
