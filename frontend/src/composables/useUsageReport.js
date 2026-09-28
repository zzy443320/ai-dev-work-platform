// 统计页签的数据层：账本口径来自后端 `/api/usage/report`，这里只做「取 + 存 + 派生」。
//
// 三条与旧版 app.js:2791 对齐的行为约束：
//   1. 失败时清空 USAGE，让各区块回到空态（旧版是把 6 个容器一起写成错误文案；
//      这里改成状态驱动，模板据 error/buckets 自行渲染，等价但不再手改 DOM）；
//   2. 筛选条件（粒度 / 天数）落 localStorage，键名 `usage-gran` / `usage-days` 不变
//      —— 旧版用户的选择在 /v2 里要能续上；
//   3. 粒度 / 天数的合法值做白名单校验，localStorage 被手改过也能回落。
import { ref, computed } from 'vue'
import { api } from '../api/client.js'

const GRANS = ['day', 'week', 'month']
const DAYS = [7, 30, 90, 365]

function readGran() {
  const v = localStorage.getItem('usage-gran') || 'day'
  return GRANS.includes(v) ? v : 'day'
}

function readDays() {
  const n = Number(localStorage.getItem('usage-days'))
  return DAYS.includes(n) ? n : 30
}

// 模块级单例：切走再切回不重复拉取（旧版是每次 switchTab 都重新 loadUsage，
// 拉取频率更低是这里唯一有意的行为变化，避免来回切页签刷爆网关）
const report = ref(null)
const loading = ref(false)
const error = ref('')

const gran = ref(readGran())
const days = ref(readDays())

// 请求序号：用户可能连点「按周」「7 天」，两次请求回来顺序不定。
// 只认最后一次发出的请求，避免旧响应覆盖新筛选。
let seq = 0

async function load() {
  const mine = ++seq
  loading.value = true
  try {
    const data = await api.usageReport(days.value, gran.value)
    if (mine !== seq) return          // 已有更新的请求在路上，丢弃这次结果
    report.value = data
    error.value = ''
  } catch (e) {
    if (mine !== seq) return
    report.value = null
    error.value = e && e.message ? e.message : String(e)
  } finally {
    if (mine === seq) loading.value = false
  }
}

/** 首次进入时拉一次；已有数据则跳过（除非显式 force） */
async function ensure(force = false) {
  if (report.value && !force && !error.value) return
  await load()
}

function setGran(g) {
  if (!GRANS.includes(g) || g === gran.value) return
  gran.value = g
  localStorage.setItem('usage-gran', g)
  return load()
}

function setDays(d) {
  d = Number(d)
  if (!DAYS.includes(d) || d === days.value) return
  days.value = d
  localStorage.setItem('usage-days', String(d))
  return load()
}

/** 图表配色（与旧版 USAGE_PALETTE 逐项一致） */
const PALETTE = ['var(--primary)', 'var(--primary-2)', '#14b8a6', '#f59e0b',
  '#0ea5e9', '#f472b6', '#84cc16', '#a855f7']

const totals = computed(() => (report.value && report.value.totals) || {})
const buckets = computed(() => (report.value && report.value.buckets) || [])
const kinds = computed(() => (report.value && report.value.kinds) || [])
const models = computed(() => (report.value && report.value.models) || [])
const tasks = computed(() => (report.value && report.value.tasks) || [])
const recent = computed(() => (report.value && report.value.recent) || [])
const kindLabels = computed(() => (report.value && report.value.kind_labels) || {})

/** 「区间内 / 今天 / 本周 / 本月 / 累计」五张卡片，label 与副标题文案同旧版 */
const cards = computed(() => {
  const r = report.value
  if (!r) return []
  const range = r.range || {}
  return [
    { label: '区间内', t: r.totals, sub: `${range.from} ~ ${range.to}`, primary: true },
    { label: '今天', t: r.today, sub: '自然日' },
    { label: '本周', t: r.this_week, sub: '周一起算' },
    { label: '本月', t: r.this_month, sub: '自然月' },
    { label: '累计', t: r.all_time, sub: '账本全部记录' },
  ]
})

export function useUsageReport() {
  return {
    report, loading, error, gran, days, totals, buckets, kinds, models,
    tasks, recent, kindLabels, cards, PALETTE,
    load, ensure, setGran, setDays,
  }
}
