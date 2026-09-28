<script setup>
// 统计页签（#pane-stats）。旧版 index.html:90-168 + app.js:2752-3134 的等价迁移。
//
// 迁移要点（也是后面几个页签的模板）：
//   1. 五个 panel 的骨架照搬，class / id 与旧版一致 —— style.css 直接复用，
//      界面用例的 `#usage-cards` / `#usage-trend` 等选择器也还能命中；
//   2. 所有 innerHTML 拼串换成子组件 + 模板绑定，转义交给 Vue（不再手工 escapeHtml）；
//   3. 折叠状态走 useFold（localStorage 键 `panel-fold-state`，与旧版同源可互通）；
//   4. 进入页签时拉数据，granularity / days 落 localStorage（键名不变）。
import { computed, onMounted, ref } from 'vue'
import { useUsageReport } from '../composables/useUsageReport.js'
import { useFold } from '../composables/useFold.js'
import { useToast } from '../composables/useToast.js'
import UsageCards from '../components/UsageCards.vue'
import UsageNote from '../components/UsageNote.vue'
import UsageTrendChart from '../components/UsageTrendChart.vue'
import UsageBarRows from '../components/UsageBarRows.vue'
import UsageDonut from '../components/UsageDonut.vue'
import UsageTasksTable from '../components/UsageTasksTable.vue'
import UsageRecentTable from '../components/UsageRecentTable.vue'

const {
  loading, error, gran, days, totals, buckets, kinds, models, tasks,
  recent, kindLabels, cards, PALETTE, report, load, ensure, setGran, setDays,
} = useUsageReport()

const { isCollapsed, toggleFold } = useFold()
const { toast } = useToast()

const trendRef = ref(null)

// 旧的 `setUsageGran` / `setUsageDays` 会同时同步按钮态和重新拉取；
// 这里按钮态由 gran/days 派生（见模板的 :class），只需重新拉取。
async function onGran(g) {
  await setGran(g)
}
async function onDays(d) {
  await setDays(d)
}

async function refresh() {
  await load()
  if (error.value) toast(`用量统计加载失败: ${error.value}`, 'err')
}

onMounted(async () => {
  await ensure()
  if (error.value) toast(`用量统计加载失败: ${error.value}`, 'err')
})

const granText = computed(() =>
  ({ day: '按天', week: '按周', month: '按月' }[gran.value] || '按天')
)

const genText = computed(() => {
  const r = report.value
  return r && r.generated_at ? `更新于 ${String(r.generated_at).replace('T', ' ').slice(5, 16)}` : ''
})

/** 趋势图头部的说明文字由子组件算出（依赖刻度与峰值），这里取它的暴露值 */
const trendMeta = computed(() => {
  const t = trendRef.value
  return t ? t.metaText : ''
})

const granOptions = [
  { g: 'day', label: '按天' },
  { g: 'week', label: '按周' },
  { g: 'month', label: '按月' },
]
const dayOptions = [
  { d: 7, label: '7 天' },
  { d: 30, label: '30 天' },
  { d: 90, label: '90 天' },
  { d: 365, label: '1 年' },
]

const modelsTop = computed(() => models.value.slice(0, 8))
const modelsTotal = computed(() => modelsTop.value.reduce((s, r) => s + r.total, 0))

const taskMeta = computed(() =>
  tasks.value.length ? `${tasks.value.length} 个任务（按区间内合计降序）` : ''
)
const recentMeta = computed(() => {
  const rows = recent.value
  if (!rows.length) return ''
  const est = rows.filter((r) => r.estimated).length
  return `最近 ${rows.length} 次${est ? ` · 其中 ${est} 次为估算` : ''}`
})

const ledger = computed(() => (report.value && report.value.note && report.value.note.ledger) || '')
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18" /><rect x="7" y="12" width="3" height="6" /><rect x="12" y="8" width="3" height="10" /><rect x="17" y="5" width="3" height="13" /></svg>
        Token 用量统计
      </h2>
      <div class="usage-controls">
        <div class="seg" id="usage-gran">
          <button v-for="o in granOptions" :key="o.g" class="seg-btn"
                  :class="{ active: gran === o.g }" :data-g="o.g"
                  @click="onGran(o.g)">{{ o.label }}</button>
        </div>
        <div class="seg" id="usage-range">
          <button v-for="o in dayOptions" :key="o.d" class="seg-btn"
                  :class="{ active: days === o.d }" :data-d="o.d"
                  @click="onDays(o.d)">{{ o.label }}</button>
        </div>
        <button class="btn-ghost" :disabled="loading" @click="refresh">刷新</button>
        <span class="muted" id="usage-gen">{{ genText }}</span>
      </div>
    </div>

    <div class="usage-cards" id="usage-cards">
      <div class="empty" v-if="error">用量统计加载失败：{{ error }}</div>
      <div class="empty" v-else-if="!report">加载中…</div>
      <UsageCards v-else :cards="cards" />
    </div>
    <UsageNote v-if="report" :totals="totals" :ledger="ledger" />
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18" /><path d="M7 15l3-4 3 3 5-7" /></svg>
        消耗趋势
      </h2>
      <span class="muted" id="usage-trend-meta">{{ trendMeta }}</span>
    </div>
    <div class="usage-chart" id="usage-trend">
      <div class="empty" v-if="error">用量统计加载失败：{{ error }}</div>
      <div class="empty" v-else-if="!report">加载中…</div>
      <UsageTrendChart v-else ref="trendRef" :buckets="buckets"
                       :granularity="gran" />
    </div>
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 3v9l6.4 6.4" /></svg>
        构成
      </h2>
    </div>
    <div class="usage-split">
      <div class="usage-chart" id="usage-kinds">
        <div class="empty" v-if="error">用量统计加载失败：{{ error }}</div>
        <div class="empty" v-else-if="!report">加载中…</div>
        <template v-else>
          <div class="usage-chart-title">按任务类型</div>
          <div class="empty" v-if="!kinds.length">暂无数据</div>
          <UsageBarRows v-else :rows="kinds" :palette="PALETTE"
                        :label-of="(r) => r.label || r.key" />
        </template>
      </div>
      <div class="usage-chart" id="usage-models">
        <div class="empty" v-if="error">用量统计加载失败：{{ error }}</div>
        <div class="empty" v-else-if="!report">加载中…</div>
        <template v-else>
          <div class="usage-chart-title">按模型</div>
          <div class="empty" v-if="!modelsTop.length || !modelsTotal">暂无数据</div>
          <template v-else>
            <UsageDonut :rows="modelsTop" :palette="PALETTE" />
            <div class="usage-donut-legend">
              <div v-for="(r, i) in modelsTop" :key="r.key">
                <i :style="{ background: PALETTE[i % PALETTE.length] }" />
                <span class="usage-donut-name" :title="r.key">{{ r.key }}</span>
                <span class="muted">{{ r.total.toLocaleString('en-US') }} · {{ ((r.total / modelsTotal) * 100).toFixed(1) }}%</span>
              </div>
            </div>
          </template>
        </template>
      </div>
    </div>
  </section>

  <section class="panel collapsible" id="usage-tasks-panel"
           :class="{ collapsed: isCollapsed('usage-tasks-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h10" /></svg>
        单任务消耗排行
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('usage-tasks-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <span class="muted" id="usage-task-meta">{{ taskMeta }}</span>
    </div>
    <div id="usage-tasks" class="usage-table-wrap">
      <div class="empty" v-if="error">用量统计加载失败：{{ error }}</div>
      <div class="empty" v-else-if="!report">加载中…</div>
      <div class="empty" v-else-if="!tasks.length">区间内还没有任务级记录。</div>
      <UsageTasksTable v-else :rows="tasks" :totals="totals" />
    </div>
  </section>

  <section class="panel collapsible" id="usage-recent-panel"
           :class="{ collapsed: isCollapsed('usage-recent-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5l3.5 2" /></svg>
        最近调用
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('usage-recent-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <span class="muted" id="usage-recent-meta">{{ recentMeta }}</span>
    </div>
    <div id="usage-recent" class="usage-table-wrap">
      <div class="empty" v-if="error">用量统计加载失败：{{ error }}</div>
      <div class="empty" v-else-if="!report">加载中…</div>
      <div class="empty" v-else-if="!recent.length">区间内还没有调用记录。</div>
      <UsageRecentTable v-else :rows="recent" :kind-labels="kindLabels" />
    </div>
  </section>
</template>
