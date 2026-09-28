// 更新日志弹窗（#chmodal）+ 顶栏未读圆点。旧版 app.js:2592-2750 的等价迁移。
// 数据源是项目根 CHANGELOG.md，经 /api/changelog 解析后按日期分组返回；
// 未读提示靠条目 id（内容寻址，localStorage 键与旧版同源：changelog-seen）。
import { computed, ref } from 'vue'
import { api } from '../api/client.js'
import { escapeHtml } from '../utils/format.js'

const SEEN_KEY = 'changelog-seen'

let cache = null

const visible = ref(false)
const data = ref(null)
const filter = ref('all')

const chips = computed(() => {
  const d = data.value || {}
  return [{ name: '全部', slug: 'all', count: d.total }].concat(d.tags || [])
})

/** 按当前筛选过滤后的分组（空组丢弃）。旧版 renderChangelog 的中间段 */
const groups = computed(() => {
  const d = data.value || {}
  return (d.groups || []).map((g) => ({
    date: g.date,
    entries: g.entries.filter((e) => filter.value === 'all' || e.tag_slug === filter.value),
  })).filter((g) => g.entries.length)
})

const shownCount = computed(() =>
  groups.value.reduce((n, g) => n + g.entries.length, 0))

const footLeft = computed(() => {
  const d = data.value || {}
  return filter.value === 'all' ? `共 ${d.total} 条` : `筛选出 ${shownCount.value} 条`
})

/** 顶栏未读圆点：最新条目 id ≠ 上次已读 id 时亮起 */
const hasUnread = computed(() => {
  const latest = (data.value || {}).latest || ''
  if (!latest) return false
  let seen = ''
  try { seen = localStorage.getItem(SEEN_KEY) || '' } catch (e) { /* 隐私模式 */ }
  return latest !== seen
})

/** 首屏拉一次（给圆点用）。旧版 initChangelog —— 后端是旧代码时静默跳过 */
async function init() {
  try {
    data.value = await ensure(false)
  } catch (e) { /* 圆点拉不到就算了 */ }
}

async function ensure(force) {
  if (cache && !force) return cache
  const d = await api.changelog()
  if (!Array.isArray(d.entries)) throw new Error(d.error || '返回格式异常')
  cache = d
  return d
}

async function open() {
  filter.value = 'all'
  visible.value = true
  data.value = null
  try {
    const d = await ensure(true)
    data.value = d
    if (d.latest) {
      try { localStorage.setItem(SEEN_KEY, d.latest) } catch (e) { /* 隐私模式 */ }
    }
  } catch (e) {
    data.value = { offline: true, reason: String((e && e.message) || e),
      entries: [], groups: [], tags: [], total: 0 }
  }
}

function close() {
  visible.value = false
}

function setFilter(slug) {
  filter.value = slug
}

// ---- 条目渲染（旧版 chWeekday/chInline/chBlock/chField/renderChEntry 逐行移植）----
const CH_WEEK = '日一二三四五六'
const CH_CIRCLED_RE = /(?=[①-⓳])/
const CH_CIRCLED_START_RE = /^[①-⓳]/

export function chWeekday(dateStr) {
  const d = new Date(`${dateStr}T00:00:00`)
  return isNaN(d.getTime()) ? '' : `周${CH_WEEK[d.getDay()]}`
}

// 行内的 `code` 与 **加粗**：日志里写文件/方法名很常见
function chInline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

// ①②③ 这类序号常被顺手写进同一行，展示时拆开：每个序号自成一行
function chBlock(v) {
  const out = []
  String(v || '').split('\n').forEach((line) => {
    if (!line.trim()) return
    line.split(CH_CIRCLED_RE).map((s) => s.trim()).filter(Boolean).forEach((seg) => {
      const num = CH_CIRCLED_START_RE.test(seg)          // ① 本身就是项目符号，不再加 •
      const bullet = !num && /^[•·]/.test(seg)
      const text = chInline(seg.replace(/^[•·]\s*/, ''))
      const cls = num ? 'num' : (bullet ? 'bullet' : '')
      out.push(`<div class="ch-para${cls ? ` ${cls}` : ''}">${text}</div>`)
    })
  })
  return out.join('')
}

function chField(label, inner, cls) {
  return `<div class="ch-field${cls ? ` ${cls}` : ''}"><b>${label}</b>`
    + `<div class="ch-val">${inner}</div></div>`
}

export function renderChEntry(e) {
  const rows = []
  if (e.content) rows.push(chField('内容', chBlock(e.content)))
  if (e.how) rows.push(chField('做法', chBlock(e.how)))
  if ((e.files_list || []).length) {
    rows.push(chField('文件', e.files_list
      .map((f) => `<code class="ch-file">${escapeHtml(f)}</code>`).join(''), 'files'))
  }
  if (e.impact) {
    rows.push(chField('影响', chBlock(e.impact)
      + (e.restart ? '<span class="ch-restart">需重启后端</span>' : ''), 'impact'))
  }
  if (e.note) rows.push(chField('备注', chBlock(e.note)))
  if (e._extra) rows.push(chField('补充', chBlock(e._extra)))
  const cls = `ch-item t-${e.tag_slug || 'other'}${e.restart ? ' needs-restart' : ''}`
  return `<div class="${cls}">
    <div class="ch-item-head">
      <span class="ch-tag t-${e.tag_slug || 'other'}">${escapeHtml(e.tag)}</span>
      ${e.time ? `<span class="ch-time">${escapeHtml(e.time)}</span>` : ''}
      <span class="ch-title">${escapeHtml(e.title)}</span>
    </div>
    <div class="ch-fields">${rows.join('')}</div>
  </div>`
}

export function useChangelog() {
  return {
    visible, data, filter, chips, groups, footLeft, hasUnread,
    init, open, close, setFilter, renderChEntry, chWeekday,
  }
}
