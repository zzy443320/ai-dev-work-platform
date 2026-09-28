<script setup>
// 口径说明。旧版 app.js:2886 `usageRenderNote` 渲染进 `#usage-note`（index.html:114），
// 而旧版的 `#usage-note` 本身就带 `.info-note` 类 —— 这里保持同样的层级：
// 根元素即是 #usage-note.info-note，不再多套一层 div。
//
// 这段文字是这个面板的「诚实性声明」：网关没回 usage 的调用是按字符估算的，
// 会被标成「估算」。估算和真值混在一起算账单是不对的，所以宁可显式写出来。
import { computed } from 'vue'

const props = defineProps({
  totals: { type: Object, default: () => ({}) },
  ledger: { type: String, default: '' },
})

const parts = computed(() => {
  const t = props.totals || {}
  const bits = [
    '每次<strong>真实</strong>模型调用都会按任务归属记一笔账（任务类型 + 任务标题 + 调用阶段），' +
    '所以「单任务消耗」「每天/每周/每月消耗」都是账本直接聚合出来的，不是抽样估算。',
  ]
  if (t.estimated) {
    bits.push(
      `区间内有 <strong>${t.estimated}</strong> 次调用网关没有返回用量（流式响应最常见），` +
      '这些数字是按字符折算的，表中标了「估算」角标 —— 请当作量级参考，不要当账单。'
    )
  } else if (t.calls) {
    bits.push('区间内所有调用的用量都来自网关回包（无估算值）。')
  }
  bits.push(
    'Mock 模式的调用不消耗 token，因此不入账 —— 面板上的调用次数就是真实调用次数。' +
    `账本：<code>${escapeCode(props.ledger)}</code>`
  )
  return bits
})

// 账本路径来自后端配置，做最小转义防注入（其余文案是我们自己写死的 HTML）
function escapeCode(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- 文案为静态 HTML + 已转义的账本路径 -->
  <div class="info-note" id="usage-note">
    <div v-for="(b, i) in parts" :key="i" v-html="b" />
  </div>
</template>
