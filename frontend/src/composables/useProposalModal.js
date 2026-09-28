// 提案详情弹窗（#pmodal）的状态与副作用。
// 旧版 app.js:233-245（openProposal）/ 247-252（closePModal）/ 583-660
// （decide + 四个操作函数）的等价迁移。模块级单例 —— App.vue 的事件接线、
// 产出物弹窗里的「查看提案」、KbModal 的关联链接都指向同一份状态。
import { computed, ref } from 'vue'
import { api } from '../api/client.js'
import { useToast } from './useToast.js'
import { useDefect } from './useDefect.js'
import { useHealth } from './useHealth.js'
import { renderProposalSections, proposalActions, statusCls, statusLabel, gateBadgeHtml } from '../utils/proposalRender.js'

const { toast } = useToast()

const visible = ref(false)
const detail = ref(null)
const sections = ref(null)
const note = ref('')
const forceConfirm = ref(false)
/** 后端拒绝时弹窗里那条红色错误（旧版 pmPendingError） */
const pendingError = ref('')

const actions = computed(() => proposalActions(detail.value || {}))
const statusText = computed(() => statusLabel(detail.value || {}))
const statusClass = computed(() => statusCls(detail.value || {}))
/** 弹窗头部的闸门徽标 HTML（旧版 gtEl.innerHTML = gateBadge(...)） */
const gateHtml = computed(() => {
  const g = (detail.value || {}).gate || {}
  return gateBadgeHtml(g.level, g.ok)
})
/** 预检横幅（旧版 renderProposal 里的 banner 逻辑） */
const preflight = computed(() => {
  const p = detail.value || {}
  const pre = p.preflight_live || p.preflight || {}
  const problems = (pre.problems || []).filter(Boolean)
  if (!problems.length) return ''
  return `<b>仓库预检未通过 —— 现在点采纳一定会被后端拒绝：</b>`
    + `<ul>${problems.map((x) => `<li>${escape(x)}</li>`).join('')}</ul>`
    + `<span class="muted">修好这些（如切换到配置的分支）后重新打开提案即可重试，无需重跑流水线。</span>`
})

function escape(s) { return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') }

async function openProposal(id) {
  try {
    const data = await api.proposal(id)
    detail.value = data
    sections.value = renderProposalSections(data)
    note.value = ((data.decision || {}).note) || ''
    visible.value = true
  } catch (e) {
    toast(`打开提案详情失败: ${e}`, 'err')
  }
}

function close() {
  visible.value = false
  detail.value = null
  pendingError.value = ''
}

function noteValue() { return (note.value || '').trim() }

function onApprove() {
  if (!detail.value) return
  decide(detail.value.id, 'approve', { note: noteValue() })
}
function onForceApprove() {
  if (!detail.value) return
  if (!forceConfirm.value) {
    toast('强制采纳前必须勾选“我已知悉风险”', 'err')
    return
  }
  decide(detail.value.id, 'approve', { force_gate: true, confirm_risk: true, note: noteValue() })
}
function onReject() {
  if (!detail.value) return
  if (!window.confirm('确认拒绝该提案？\n不会写任何文件，仅把提案标记为 rejected（可重跑流水线生成新提案）。')) return
  decide(detail.value.id, 'reject', { note: noteValue() })
}
function onUndo() {
  if (!detail.value) return
  const files = ((detail.value.apply || {}).files) || []
  const list = files.length ? `\n涉及文件：\n${files.map((f) => '· ' + f).join('\n')}` : ''
  if (!window.confirm(`确认撤销采纳？\n后端会把下列改动恢复为采纳前的内容（仅恢复本次采纳写入的文件，不影响其他改动）。${list}`)) return
  decide(detail.value.id, 'undo', {})
}

/** 决策后统一刷新提案列表 / 统计 / 健康行（旧版 decide 的收尾三连）。
    useDefect 是模块级单例，这里拿到的是 DefectPane 正在渲染的同一套状态。 */
async function decide(id, action, payloadObj) {
  const payload = payloadObj || {}
  const modalOpen = visible.value
  const prevNote = note.value
  let ok = false, msg = ''

  try {
    const data = await api.proposalAction(id, action, payload)
    ok = data.ok !== false
    msg = data.error || (Array.isArray(data.detail) ? data.detail.join('\n') : data.detail) || ''
    if (ok) {
      const res = data.result || {}
      if (action === 'approve') {
        toast(`已写入工作区（${(res.files || []).length} 个文件，未 commit）`)
      } else if (action === 'reject') {
        toast('已拒绝该提案，未改动仓库')
      } else if (action === 'undo') {
        toast(`已恢复采纳前的文件内容：${(data.restored || []).length} 个文件`)
      } else {
        toast('操作完成')
      }
    } else {
      toast(msg || '操作被拒绝', 'err')
    }
  } catch (e) {
    ok = false
    msg = `请求失败: ${e}`
    toast(msg, 'err')
  }

  const { loadProposals, loadStats } = useDefect()
  await Promise.all([loadProposals(), loadStats(), useHealth().loadHealth()])

  if (modalOpen && detail.value && detail.value.id === id) {
    if (!ok) pendingError.value = msg
    await openProposal(id)
    if (!noteValue()) note.value = prevNote
  } else if (!ok) {
    pendingError.value = msg
  }
}

export function useProposalModal() {
  return {
    visible, detail, sections, note, forceConfirm, pendingError,
    actions, statusText, statusClass, gateHtml, preflight,
    openProposal, close, decide, onApprove, onForceApprove, onReject, onUndo,
  }
}
