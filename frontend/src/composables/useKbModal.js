// 知识卡片通用弹窗（#modal）。旧版 app.js:822-847（viewPattern）+ 893-918
// （viewCard）+ 883-891（openKbDoc 路由）+ 920-923（closeModal）的等价迁移。
// 模块级单例：DefectPane 的卡片 / 模式卡 / 正文下钻链接都开这一个弹窗。
import { ref } from 'vue'
import { api } from '../api/client.js'
import { useToast } from './useToast.js'
import { renderMarkdown } from '../utils/format.js'

const { toast } = useToast()

const visible = ref(false)
const title = ref('')
/** meta 行：[{ k, v }]；关联缺陷行额外带 chips: [{ category, id }]（可点下钻） */
const metaRows = ref([])
const bodyHtml = ref('')

function rowSpan(k, v) {
  return { k, v: String(v ?? '') }
}

/** 知识卡片正文里的站内相对链接（`分类/缺陷.md` / `_patterns/模式.md`）。
    旧版 openKbDoc app.js:883。 */
function openDoc(rel) {
  const parts = String(rel || '').split('/').filter(Boolean)
  if (parts.length < 2) return
  const file = parts[parts.length - 1]
  const id = file.replace(/\.md$/, '')
  if (!id) return
  if (parts[0] === '_patterns') openPattern(id)
  else openCard(parts[0], id)
}

async function openCard(category, id) {
  try {
    const data = await api.card(category, id)
    const meta = data.front || {}
    title.value = meta.title || id
    metaRows.value = [
      rowSpan('ID', meta.defect_id),
      rowSpan('分类', data.category),
      rowSpan('优先级', meta.priority),
      rowSpan('状态', statusTextOf(meta.status)),
      rowSpan('AI', meta.ai_mode),
      rowSpan('闸门', gateTextOf(meta.gate_level)),
      rowSpan('Commit', meta.commit || '—'),
      rowSpan('提案', meta.proposal_id),
      rowSpan('更新', (meta.updated || meta.created || '').replace('T', ' ').slice(0, 19)),
    ].filter((r) => r.v)
    bodyHtml.value = renderMarkdown(data.body)
    visible.value = true
  } catch (e) {
    toast(`打开失败: ${e}`, 'err')
  }
}

async function openPattern(id) {
  try {
    const data = await api.pattern(id)
    const meta = data.front || {}
    title.value = meta.name || id
    const rows = [
      rowSpan('模式', meta.pattern_id),
      rowSpan('复发', meta.recurrence ? `${meta.recurrence} 次` : ''),
      rowSpan('已采纳', meta.applied),
      rowSpan('分类', meta.categories),
      rowSpan('模块', meta.module),
      rowSpan('更新', (meta.updated || '').replace('T', ' ').slice(0, 19)),
    ].filter((r) => r.v)
    const chips = (data.cases || []).map((c) => ({ category: c.category, id: c.id }))
    if (chips.length) rows.push({ k: '关联缺陷', v: '', chips })
    metaRows.value = rows
    bodyHtml.value = renderMarkdown(data.body)
    visible.value = true
  } catch (e) {
    toast(`打开失败: ${e}`, 'err')
  }
}

function close() {
  visible.value = false
}

/** 展示口径与 labels.js 的 statusText/gateBadge 一致（proposed→待审批 等），
    这里只需要纯文本，不引徽标结构。 */
function statusTextOf(s) {
  const st = s || ''
  if (st === 'proposed') return '待审批'
  return ({ pending: '待审批', gate_failed: '闸门未通过', invalid: '无法生成补丁',
    applied: '已采纳', apply_failed: '采纳失败', rejected: '已拒绝' })[st] || st
}
function gateTextOf(lv) {
  // 旧版：GATE_TEXT[meta.gate_level] || meta.gate_level —— 无值时整行被过滤，不给默认
  return ({ explicit: '显式命令', package_json: 'package.json 探测',
    degraded: '降级语法检查', none: '未校验' })[lv] || lv
}

export function useKbModal() {
  return { visible, title, metaRows, bodyHtml, openCard, openPattern, openDoc, close }
}
