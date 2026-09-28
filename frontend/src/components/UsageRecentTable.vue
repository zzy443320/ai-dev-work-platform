<script setup>
// 最近调用明细。旧版 app.js:3100 `usageRenderRecent` 的等价复刻。
//
// 这里最重要的两个角标：
//   「估算」—— 网关没回 usage，token 是按字符折算的，不能当账单；
//   「失败」—— 失败的调用也计 token（网关照扣），所以不能从统计里剔掉。
import { computed } from 'vue'
import { fmtInt, shortTs } from '../utils/format.js'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  kindLabels: { type: Object, default: () => ({}) },
})

const items = computed(() =>
  props.rows.map((r, i) => ({
    key: i,
    ts: shortTs(r.ts),
    kind: props.kindLabels[r.kind] || r.kind || '—',
    step: r.step,
    agent: r.agent,
    model: r.model || '—',
    mode: r.mode,
    input: fmtInt(r.input_tokens),
    output: fmtInt(r.output_tokens),
    total: fmtInt(r.total_tokens),
    seconds: r.seconds ? r.seconds.toFixed(1) + 's' : '—',
    ok: r.ok !== false,
    error: r.error || '',
    estimated: !!r.estimated,
  }))
)
</script>

<template>
  <table class="usage-table">
    <thead>
      <tr>
        <th>时间</th><th>类型 / 阶段</th><th>模型</th>
        <th class="num">输入</th><th class="num">输出</th><th class="num">合计</th>
        <th class="num">耗时</th><th>状态</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="it in items" :key="it.key">
        <td class="muted">{{ it.ts }}</td>
        <td>
          <span class="usage-kind-chip">{{ it.kind }}</span>
          <!-- 旧版这几段是模板串直接拼接，之间没有空格；这里用 v-text 前置空格等价复刻。
               写成 `{{ ' ' + it.step }}` 而非 `<span>&nbsp;</span>`，是为了让
               innerText 与旧版逐字相同（用例在比这个）。 -->
          <span v-if="it.step" class="muted" v-text="' ' + it.step" />
          <span v-if="it.agent" class="muted" v-text="' · ' + it.agent" />
        </td>
        <td class="muted">{{ it.model }}<span v-if="it.mode" class="muted"> / {{ it.mode }}</span></td>
        <td class="num">{{ it.input }}</td>
        <td class="num">{{ it.output }}</td>
        <td class="num strong">{{ it.total }}</td>
        <td class="num muted">{{ it.seconds }}</td>
        <td>
          <span v-if="it.ok" class="usage-badge ok">成功</span>
          <span v-else class="usage-badge bad" :title="it.error">失败</span>
          <span v-if="it.estimated" class="usage-badge est" title="网关未返回 usage，按字符估算">估算</span>
        </td>
      </tr>
    </tbody>
  </table>
</template>
