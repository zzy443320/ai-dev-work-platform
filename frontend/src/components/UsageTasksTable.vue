<script setup>
// 单任务消耗排行。旧版 app.js:3068 `usageRenderTasks` 的等价复刻。
//
// 表头列序与旧版一致（# / 类型 / 任务 / 调用 / 输入 / 输出 / 合计 / 占比 / 最后），
// 「占比」的分母是区间总计（USAGE.totals.total），不是本次列表合计 —— 别改。
import { computed } from 'vue'
import { fmtInt, relTime } from '../utils/format.js'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  totals: { type: Object, default: () => ({}) },
})

const max = computed(() => Math.max(1, ...props.rows.map((r) => r.total)))

const items = computed(() =>
  props.rows.map((r, i) => ({
    rk: i + 1,
    kind: r.label || r.kind || '—',
    title: r.title || '(无标题)',
    tip: `${r.title || ''}　${r.key || ''}`,
    calls: fmtInt(r.calls),
    failed: r.failed,
    input: fmtInt(r.input),
    output: fmtInt(r.output),
    total: fmtInt(r.total),
    pct: (props.totals.total ? (r.total / props.totals.total) * 100 : 0).toFixed(1),
    rel: relTime(r.last || ''),
    barWidth: ((r.total / max.value) * 100).toFixed(1),
  }))
)
</script>

<template>
  <table class="usage-table">
    <thead>
      <tr>
        <th>#</th><th>类型</th><th>任务</th><th class="num">调用</th>
        <th class="num">输入</th><th class="num">输出</th><th class="num">合计</th>
        <th class="num">占比</th><th>最后</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="it in items" :key="it.rk">
        <td class="rk">{{ it.rk }}</td>
        <td><span class="usage-kind-chip">{{ it.kind }}</span></td>
        <td class="tname" :title="it.tip">
          {{ it.title }}
          <div class="usage-rel"><i :style="{ width: it.barWidth + '%' }" /></div>
        </td>
        <td class="num">{{ it.calls }}<span v-if="it.failed" class="usage-badge bad">{{ it.failed }} 失败</span></td>
        <td class="num">{{ it.input }}</td>
        <td class="num">{{ it.output }}</td>
        <td class="num strong">{{ it.total }}</td>
        <td class="num">{{ it.pct }}%</td>
        <td class="muted">{{ it.rel }}</td>
      </tr>
    </tbody>
  </table>
</template>
