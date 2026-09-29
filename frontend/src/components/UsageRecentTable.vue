<script setup>
// 最近调用明细。旧版 app.js:3100 `usageRenderRecent` 的等价复刻。
//
// 表格已换成 <el-table>；三个角标（成功/失败/估算）换成 el-tag 并保留
// `.usage-badge` 类 —— 界面用例读的是 `#usage-recent .usage-badge` 的文本，
// 换标签不能把这段文本改掉。
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
  <el-table :data="items" size="small" class="usage-el-table">
    <el-table-column label="时间" width="130">
      <template #default="{ row }"><span class="muted">{{ row.ts }}</span></template>
    </el-table-column>
    <el-table-column label="类型 / 阶段" min-width="200">
      <template #default="{ row }">
        <el-tag class="usage-kind-chip" effect="light" round>{{ row.kind }}</el-tag>
        <!-- 旧版这几段是模板串直接拼接，之间没有空格；这里用 v-text 前置空格等价复刻。
             写成 `{{ ' ' + it.step }}` 而非 `<span>&nbsp;</span>`，是为了让
             innerText 与旧版逐字相同（用例在比这个）。 -->
        <span v-if="row.step" class="muted" v-text="' ' + row.step" />
        <span v-if="row.agent" class="muted" v-text="' · ' + row.agent" />
      </template>
    </el-table-column>
    <el-table-column label="模型" width="170">
      <template #default="{ row }">
        <span class="muted">{{ row.model }}<span v-if="row.mode" class="muted"> / {{ row.mode }}</span></span>
      </template>
    </el-table-column>
    <el-table-column label="输入" width="94" align="right" prop="input" />
    <el-table-column label="输出" width="94" align="right" prop="output" />
    <el-table-column label="合计" width="94" align="right">
      <template #default="{ row }"><span class="strong">{{ row.total }}</span></template>
    </el-table-column>
    <el-table-column label="耗时" width="80" align="right">
      <template #default="{ row }"><span class="muted">{{ row.seconds }}</span></template>
    </el-table-column>
    <el-table-column label="状态" width="150">
      <template #default="{ row }">
        <el-tag v-if="row.ok" class="usage-badge ok" type="success" effect="light" round>成功</el-tag>
        <el-tag v-else class="usage-badge bad" type="danger" effect="light" round :title="row.error">失败</el-tag>
        <el-tag v-if="row.estimated" class="usage-badge est" type="warning" effect="light" round
                title="网关未返回 usage，按字符估算">估算</el-tag>
      </template>
    </el-table-column>
  </el-table>
</template>
