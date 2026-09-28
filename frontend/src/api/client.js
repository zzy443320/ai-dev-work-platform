// 统一的 HTTP 客户端。
//
// 与旧版 app.js 的行为约定保持一致，逐条对齐（迁移期不允许行为漂移）：
//   1. 非 2xx 也要尝试解析 body —— 后端用 {"error": "..."} 或 FastAPI 的
//      {"detail": "..."} 承载真正的失败原因，直接抛 `HTTP 500` 会丢掉信息；
//   2. detail 可能是数组（FastAPI 校验错误），要拼成可读文本；
//   3. 请求体统一 JSON，头统一 Content-Type: application/json。
//
// 这样上层（视图 / composable）拿到的永远是「已解析的数据」或「带可读消息的 Error」。

/** 把后端返回的错误载荷压成一句人话。与旧版 `data.error || data.detail || HTTP n` 同序。 */
export function extractError(data, status) {
  if (data) {
    if (data.error) return String(data.error)
    const d = data.detail
    if (Array.isArray(d)) return d.join('\n')
    if (d) return String(d)
  }
  return `HTTP ${status}`
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

/**
 * 发一个请求并把响应解析成 JSON。
 * @returns {Promise<any>} 解析后的数据
 * @throws {ApiError} 网络失败 / 非 2xx / body 不是 JSON 时
 */
async function request(url, { method = 'GET', body, signal } = {}) {
  const init = { method, signal }
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }
  let r
  try {
    r = await fetch(url, init)
  } catch (e) {
    // AbortError 是用户主动「停止」，交给调用方识别，不包装
    if (e && e.name === 'AbortError') throw e
    throw new ApiError(`请求失败：${e && e.message ? e.message : e}`, 0, null)
  }
  let data = null
  try {
    data = await r.json()
  } catch (e) {
    // 空响应（204 之类）或非 JSON：data 保持 null，错误信息回落到 HTTP 状态码
  }
  if (!r.ok) throw new ApiError(extractError(data, r.status), r.status, data)
  return data
}

export const http = {
  get: (url, opts) => request(url, opts),
  post: (url, body, opts) => request(url, { ...opts, method: 'POST', body }),
}

// ------------------------------------------------------------------ 各接口
// 集中在这里，是为了让「前端到底调了后端什么」一眼可查（旧版散在 4316 行里）。
const enc = encodeURIComponent

export const api = {
  // 基础
  health: () => http.get('/api/health'),
  stats: () => http.get('/api/stats'),
  changelog: () => http.get('/api/changelog'),

  // 设置与 AI 接入配置
  getSettings: () => http.get('/api/settings'),
  saveSettings: (payload) => http.post('/api/settings', payload),
  aiProviders: () => http.get('/api/ai/providers'),
  aiTest: (payload) => http.post('/api/ai/test', payload),
  aiModels: (payload) => http.post('/api/ai/models', payload),

  // 知识库
  patterns: () => http.get('/api/patterns'),
  pattern: (id) => http.get(`/api/pattern/${enc(id)}`),
  cards: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString()
    return http.get('/api/cards' + (qs ? `?${qs}` : ''))
  },
  card: (category, id) => http.get(`/api/card/${enc(category)}/${enc(id)}`),

  // 用量统计（按天 / 任务类型聚合）
  usageReport: (days, granularity) =>
    http.get(`/api/usage/report?days=${enc(days)}&granularity=${enc(granularity)}`),

  // 缺陷修复的单次执行（无事件流，一次返回全部结果）
  run: (payload) => http.post('/api/run', payload),
  probe: (payload) => http.post('/api/probe', payload),

  // 缺陷修复提案
  proposals: () => http.get('/api/proposals'),
  proposal: (id) => http.get(`/api/proposals/${enc(id)}`),
  proposalAction: (id, action, payload) =>
    http.post(`/api/proposals/${enc(id)}/${enc(action)}`, payload),

  // 产出物（需求开发 / 接口联调 / 代码测试共用）
  artifacts: (type) => http.get(`/api/artifacts?type=${enc(type)}`),
  artifact: (id) => http.get(`/api/artifacts/${enc(id)}`),
  artifactDiff: (id) => http.get(`/api/artifacts/${enc(id)}/diff`),
  artifactAction: (id, action, payload) =>
    http.post(`/api/artifacts/${enc(id)}/${enc(action)}`, payload),

  // 仓库只读浏览
  repoFile: (rel) => http.get(`/api/repo/file?path=${enc(rel)}`),
  figmaFetch: (payload) => http.post('/api/figma/fetch', payload),
  taskRun: (payload) => http.post('/api/tasks/run', payload),

  // 问答
  chatSessions: () => http.get('/api/chat/sessions'),
  chatCreateSession: (payload) => http.post('/api/chat/sessions', payload),
  chatSession: (sid) => http.get(`/api/chat/sessions/${enc(sid)}`),
  chatClear: (sid) => http.post(`/api/chat/sessions/${enc(sid)}/clear`, {}),
  chatDelete: (sid) => http.post(`/api/chat/sessions/${enc(sid)}/delete`, {}),

  // 长任务作业
  teamRoles: () => http.get('/api/team/roles'),
  teamRuns: () => http.get('/api/team/runs'),
  teamRun: (rid) => http.get(`/api/team/runs/${enc(rid)}`),
  teamIntervene: (rid, payload) => http.post(`/api/team/runs/${enc(rid)}/intervene`, payload),
  teamAbort: (rid) => http.post(`/api/team/runs/${enc(rid)}/abort`, {}),

  // 扩展能力（技能 / MCP）
  extensions: () => http.get('/api/extensions'),
  extensionSkills: (payload) => http.post('/api/extensions/skills', payload),
  mcpTest: (payload) => http.post('/api/extensions/mcp/test', payload),
  mcpSave: (payload) => http.post('/api/extensions/mcp', payload),
}

// ------------------------------------------------------------------ 流式接口
// 这五条都走 SSE（见 composables/useSse.js 的 sseConsume），不能套用上面的
// JSON 客户端 —— 它们的响应体是事件流，不是一次性的 JSON。
// 集中列出是为了「前端到底开了哪几条流」可一眼查全。
export const STREAM_URLS = {
  run: '/api/run/stream',           // 缺陷修复流水线
  probe: '/api/probe/stream',       // 缺陷探测
  chat: '/api/chat/stream',         // 问答
  team: '/api/team/stream',         // 长任务作业
}

// 附件上传走 multipart，单独一条：不能套用上面的 JSON 客户端。
export async function uploadAttachment(file, filename) {
  const fd = new FormData()
  fd.append('file', file, filename)
  const r = await fetch('/api/chat/attachments', { method: 'POST', body: fd })
  let j = null
  try {
    j = await r.json()
  } catch (e) { /* 空体 */ }
  if (!r.ok) throw new ApiError(extractError(j, r.status), r.status, j)
  return j
}
