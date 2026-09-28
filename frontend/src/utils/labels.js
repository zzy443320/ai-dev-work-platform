// 缺陷修复相关的文案字典与徽标渲染。
// 全部从旧版 app.js 逐条搬运（app.js:29-59、app.js:75-83、app.js:326-345）——
// 「换框架不换口径」，文案一旦漂移，历史截图和界面用例都会对不上。
import { escapeHtml } from './format.js'

export const STATUS_TEXT = {
  pending: '待审批',
  gate_failed: '闸门未通过',
  invalid: '无法生成补丁',
  applied: '已采纳',
  apply_failed: '采纳失败',
  rejected: '已拒绝',
}

/** 状态权重：只作为时间完全相同时的稳定兜底，不参与主排序 */
export const STATUS_ORDER = ['pending', 'gate_failed', 'invalid', 'apply_failed', 'applied', 'rejected']

export const GATE_TEXT = {
  explicit: '显式命令',
  package_json: 'package.json 探测',
  degraded: '降级语法检查',
  none: '未校验',
}

/** 知识卡片里 pending 写成 proposed，展示时归一 */
export function statusText(s) {
  const st = s || ''
  if (st === 'proposed') return STATUS_TEXT.pending
  return STATUS_TEXT[st] || st
}

/** 是否属于「还开着的」状态（面板角标口径） */
export function isOpenStatus(s) {
  return s === 'pending' || s === 'gate_failed'
}

/** 状态徽标。旧版 app.js:75 返回 HTML 串，这里改成结构化描述，由组件渲染 */
export function statusBadge(s) {
  const st = (s === 'proposed' || !s) ? 'pending' : s
  return { cls: `st st-${st}`, text: statusText(s) }
}

/** 闸门徽标。旧版 app.js:79。`ok === false` 追加「· 未通过」 */
export function gateBadge(level, ok) {
  const lv = level || 'none'
  const failed = ok === false ? ' · 未通过' : ''
  return {
    cls: `gate-badge gate-${lv}`,
    text: `闸门 ${GATE_TEXT[lv] || lv}${failed}`,
  }
}

/** 提案卡片里那个 `title` 的兜底（旧版直接写死文案） */
export function proposalTitle(p) {
  return p && p.title ? p.title : '(无标题)'
}

// ---- 「根因不在前端仓库」判定 ----
// 新版提案由后端 analysis.non_frontend 直出；历史提案没有该字段，
// 按同样的信号词兜底重算（否则 100002 这类正确结论在界面上仍显示成
// 红色「未生成可应用补丁」）。旧版 app.js:326 `looksNonFrontend`。
const NON_FE_STRONG = ['非前端缺陷', '不修改任何前端文件', '不需要修改前端',
  '前端无需修改', '根因在后端', '问题在后端', '后端缺陷', '需改后端',
  '后端修复', '不属于前端', '超出前端范围']
const NON_FE_WEAK = ['后端', '服务端', '接口层', '数据库', '未命中']

export function looksNonFrontend(a) {
  a = a || {}
  if (a.block_count) return false
  if (typeof a.non_frontend === 'boolean') return a.non_frontend
  const text = [a.root_cause, a.explanation, a.prevention].join(' ')
  if (!text.trim()) return false
  let score = 0
  NON_FE_STRONG.forEach((s) => { if (text.indexOf(s) >= 0) score += 2 })
  NON_FE_WEAK.forEach((s) => { if (text.indexOf(s) >= 0) score += 1 })
  if (/\/api\//i.test(text) && text.indexOf('接口') >= 0) score += 1
  // 阈值 2 —— 与旧版 app.js:336 逐字一致。别改成 3：一个强信号（+2）就够，
  // 改高会让「根因在后端」这类正确结论重新显示成红色「未生成可应用补丁」。
  return score >= 2
}

// ---------------------------------------------------------------- 产出物
// 旧版 app.js:1840-1846。与提案的状态字典分开维护 —— 产出物没有
// gate_failed/invalid，但有 apply_failed（采纳时验收闸门没过）。
export const ARTIFACT_STATUS_TEXT = {
  pending: '待采纳',
  apply_failed: '闸门未通过',
  applied: '已采纳',
  rejected: '已拒绝',
}

/** 列表排序权重：待处理的排前面，同权重按时间倒序（旧版 app.js:2036） */
export const ARTIFACT_STATUS_ORDER = ['pending', 'apply_failed', 'applied', 'rejected']

export function artifactStatusText(s) {
  return ARTIFACT_STATUS_TEXT[s] || s || '—'
}

/** checklist 里的 **加粗** 片段。旧版 app.js:539（只此一条语法） */
export function mdBold(s) {
  return escapeHtml(String(s ?? '')).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}
