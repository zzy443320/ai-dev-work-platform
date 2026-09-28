// 产出物列表 + 详情弹窗的全部状态与副作用。
// 旧版 app.js:2004-2321（loadArtifacts / renderArtifacts / artifactCard /
// openArtifact / renderArtifact / renderArtifactFiles / renderAActions /
// aDecide）的等价迁移。
//
// ⚠️ 为什么全部状态都是**模块级单例**（不在 useArtifacts() 里 new）：
// 产出物面板挂在 App.vue 的右侧、详情弹窗挂在 App.vue 的末尾、三个任务页签
// 跑完任务后都要刷新列表 —— 三处分别调 useArtifacts()。如果每次调用都新建
// 一套 ref，App.vue 里 loadArtifacts() 写的是 A 套，面板渲染读的是 B 套，
// 列表永远空（新版第一次接线时就是这么挂的：状态药丸说「产出物已生成」，
// 面板却显示「0 待采纳 / 0 条」）。同 useToast / useRunStatus / useHealth 的做法。
//
// 与旧版的一处刻意差异：aDecide 成功/失败后旧版重开详情弹窗并把「决策备注」
// 回填进输入框（因为 innerHTML 重渲染会把输入清掉）；Vue 里 note 是响应式状态，
// 详情重新拉取不会动它，天然保住。
import { computed, ref } from 'vue'
import { api } from '../api/client.js'
import { useToast } from './useToast.js'
import { useHealth } from './useHealth.js'
import { ARTIFACT_STATUS_ORDER } from '../utils/labels.js'
import { relTime } from '../utils/format.js'

// 两个被单例副作用用到的依赖（模块级取一次即可，它们本身也是单例）
const { toast } = useToast()
const { loadHealth } = useHealth()

// ------------------------------------------------------------- 模块级状态
// 列表
const items = ref([])
const error = ref('')
/** 当前列表对应哪个任务类型（reqdev/apidebug/codetest/chat/team） */
const type = ref('')

/** 排序：状态权重优先，同权重按时间倒序。旧版 app.js:2036 */
const sorted = computed(() =>
  items.value.slice().sort((a, b) =>
    (ARTIFACT_STATUS_ORDER.indexOf(a.status) + 1 || 99)
      - (ARTIFACT_STATUS_ORDER.indexOf(b.status) + 1 || 99)
    || String(b.updated || b.created || '').localeCompare(String(a.updated || a.created || '')))
)

/** 面板角标。旧版 setArtifactBadge */
const badge = computed(() => {
  const open = items.value.filter((a) => a.status === 'pending' || a.status === 'apply_failed').length
  return {
    text: `${open} 待采纳 / ${items.value.length} 条`,
    alert: open > 0,
    hidden: items.value.length === 0,
  }
})

// 详情弹窗
const modalVisible = ref(false)
const detail = ref(null)
const diffData = ref(null)
/** 文件区视图：false=改动高亮 diff，true=完整文件 */
const fileFullView = ref(false)
/** 后端拒绝操作时的错误（显示在弹窗里的红条） */
const pendingError = ref('')
const note = ref('')
const forceConfirm = ref(false)
const deciding = ref(false)

/** 文件区的 diff 行数据。把「新旧对齐视图 vs 完整文件」的选择收敛在这里，
 *  模板只管渲染。每个文件返回 {file, isNew, statAdd, statDel, lines}，
 *  lines 为 null 表示走完整文件视图。 */
const fileViews = computed(() => {
  const a = detail.value
  if (!a) return { hasDiff: false, applied: false, showDiff: false, list: [] }
  const files = a.files || []
  const diffByPath = {}
  ;((diffData.value && diffData.value.files) || []).forEach((d) => { diffByPath[d.path] = d })
  const applied = a.status === 'applied'
  const hasDiff = files.some((f) => {
    const d = diffByPath[f.path]
    return d && !d.stale && (d.added || d.removed)
  })
  const showDiff = hasDiff && !applied && !fileFullView.value
  return {
    hasDiff,
    applied,
    showDiff,
    list: files.map((f) => {
      const d = diffByPath[f.path]
      const usable = d && !d.stale
      const isNew = !usable || !d.exists
      const lines = showDiff && usable && (d.lines || []).length
        ? d.lines.map((l) => ({ cls: l.t === 'same' ? 'ctx' : l.t, s: String(l.s ?? '') }))
        : null
      return {
        path: f.path || '',
        desc: f.description || '',
        content: f.content || '',
        isNew,
        added: usable ? (d.added || 0) : 0,
        removed: usable ? (d.removed || 0) : 0,
        lineCount: String(f.content || '').split('\n').length,
        lines,
      }
    }),
  }
})

/** 操作按钮的可用性。旧版 renderAActions 的状态机 */
const actions = computed(() => {
  const st = (detail.value && detail.value.status) || 'pending'
  return {
    approve: st === 'pending',
    approveDisabled: st === 'apply_failed',
    approveTitle: st === 'apply_failed'
      ? '上次采纳时验收闸门未通过：只能勾选风险后「强制采纳」' : '',
    force: st === 'apply_failed',
    reject: st === 'pending' || st === 'apply_failed',
    undo: st === 'applied',
    undoTitle: '把写入的文件恢复为采纳前的内容（新建文件会被删除）',
  }
})

// ------------------------------------------------------------- 副作用
async function loadArtifacts(kind) {
  const t = kind || type.value
  if (!t) return
  type.value = t
  try {
    items.value = (await api.artifacts(t)) || []
    error.value = ''
  } catch (e) {
    error.value = e && e.message ? e.message : String(e)
  }
}

/** 打开详情：详情与基线 diff 并行拉；diff 失败不阻塞，退回完整文件视图 */
async function openArtifact(id) {
  try {
    const [data, diff] = await Promise.all([
      api.artifact(id),
      api.artifactDiff(id).catch(() => null),
    ])
    detail.value = data
    diffData.value = diff && diff.ok !== false ? diff : null
    fileFullView.value = false
    pendingError.value = ''
    note.value = (data.decision && data.decision.note) || (data.apply && data.apply.note) || ''
    forceConfirm.value = false
    modalVisible.value = true
  } catch (e) {
    toast(`打开产出物详情失败: ${e}`, 'err')
  }
}

function closeModal() {
  modalVisible.value = false
  detail.value = null
  pendingError.value = ''
}

/** 采纳 / 强制采纳 / 拒绝 / 撤销。旧版 aDecide 的等价收敛 */
async function decide(action, payload) {
  const a = detail.value
  if (!a) return false
  deciding.value = true
  let ok = false
  let msg = ''
  try {
    const data = await api.artifactAction(a.id, action, payload || {})
    ok = data.ok !== false
    msg = (data && data.error) || ''
    if (ok) {
      if (action === 'approve') toast(`已写入工作区（${(data.files || []).length} 个文件，未 commit）`)
      else if (action === 'reject') toast('已拒绝该产出物，未改动仓库')
      else if (action === 'undo') toast(`已恢复采纳前的文件内容：${(data.restored || []).length} 个文件`)
      else toast('操作完成')
    } else {
      toast(msg || '操作被拒绝', 'err')
    }
  } catch (e) {
    ok = false
    msg = e && e.message ? e.message : String(e)
    toast(msg, 'err')
  } finally {
    deciding.value = false
  }
  await Promise.all([loadArtifacts(), loadHealth()])
  if (modalVisible.value) {
    if (!ok) pendingError.value = msg
    // 重开详情刷新状态（note/forceConfirm 是响应式状态，不会被清掉）
    try {
      detail.value = await api.artifact(a.id)
    } catch (e) { /* 列表已刷新，弹窗保旧值即可 */ }
  } else if (!ok) {
    pendingError.value = msg
  }
  return ok
}

function onApprove() {
  return decide('approve', { note: (note.value || '').trim() })
}
function onForceApprove() {
  if (!forceConfirm.value) {
    toast('强制采纳前必须勾选“我已知悉风险”', 'err')
    return Promise.resolve(false)
  }
  return decide('approve', { force_gate: true, confirm_risk: true, note: (note.value || '').trim() })
}
function onReject() {
  // confirm() 由调用方（组件）挂 window.confirm，这里直接发请求。
  // 旧版在点按钮时弹 confirm —— 组件层保留同样的交互。
  return decide('reject', { note: (note.value || '').trim() })
}
function onUndo() {
  return decide('undo', {})
}

/** 卡片时间文案（模板直接用，避免每张卡算一次） */
function timeText(a) {
  return relTime(a.updated || a.created)
}

/** 拿到单例状态的读句柄。所有调用方共享同一套 ref。 */
export function useArtifacts() {
  return {
    // 列表
    items, error, type, sorted, badge, loadArtifacts, timeText,
    // 详情
    modalVisible, detail, diffData, fileFullView, pendingError,
    note, forceConfirm, deciding, fileViews, actions,
    openArtifact, closeModal,
    onApprove, onForceApprove, onReject, onUndo,
  }
}

