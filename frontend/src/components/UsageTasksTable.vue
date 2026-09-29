<script setup>
// 单任务消耗排行。旧版 app.js:3068 `usageRenderTasks` 的等价复刻。
//
// 表格已换成 <el-table>：列定义走 el-table-column，右边距对齐用 align="right"
// （旧版是给 th/td 加 .num 类）。
//
// 表头列序与旧版一致（# / 类型 / 任务 / 调用 / 输入 / 输出 / 合计 / 占比 / 最后），
// 「占比」的分母是区间总计（USAGE.totals.total），不是本次列表合计 —— 别改。
//
// ⚠ 两个换 el-table 时踩过的坑（用例 check_vue_stats_ui.py 直接读 td 的文本）：
//   1. 用 `prop="pct"` 只会渲染 `37.2`——旧版表格的百分比是**带 % 的**，
//      所以占比列必须显式写插槽 `{{ row.pct }}%`，否则界面上丢了百分号；
//   2. 「调用」列的数字与「N 失败」角标之间**不能有换行空白**：模板里换行会被
//      压成一个空格，td 文本从旧版的 `11 失败` 变成 `1 1 失败`。两者写在同一行。
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
  <el-table :data="items" size="small" class="usage-el-table">
    <el-table-column label="#" width="52" align="center">
      <template #default="{ row }"><span class="rk">{{ row.rk }}</span></template>
    </el-table-column>
    <el-table-column label="类型" width="104">
      <template #default="{ row }">
        <el-tag class="usage-kind-chip" effect="light" round>{{ row.kind }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="任务" min-width="220">
      <template #default="{ row }">
        <span class="tname" :title="row.tip">
          {{ row.title }}
          <div class="usage-rel"><i :style="{ width: row.barWidth + '%' }" /></div>
        </span>
      </template>
    </el-table-column>
    <!-- 数字与角标必须紧邻（见文件头说明 2），别为了好看换行 -->
    <el-table-column label="调用" width="120" align="right">
      <template #default="{ row }">{{ row.calls }}<el-tag v-if="row.failed" class="usage-badge" type="danger" effect="light" round>{{ row.failed }} 失败</el-tag></template>
    </el-table-column>
    <el-table-column label="输入" width="94" align="right" prop="input" />
    <el-table-column label="输出" width="94" align="right" prop="output" />
    <el-table-column label="合计" width="94" align="right">
      <template #default="{ row }"><span class="strong">{{ row.total }}</span></template>
    </el-table-column>
    <!-- 百分比要显式补 %（见文件头说明 1） -->
    <el-table-column label="占比" width="74" align="right">
      <template #default="{ row }">{{ row.pct }}%</template>
    </el-table-column>
    <el-table-column label="最后" width="112">
      <template #default="{ row }"><span class="muted">{{ row.rel }}</span></template>
    </el-table-column>
  </el-table>
</template>
