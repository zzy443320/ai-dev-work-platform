// 问答页签的全部状态与副作用。旧版 app.js:3830-4337 的等价迁移。
//
// 为什么抽成 composable 而不是塞进组件：这段逻辑里「会话列表 / 消息 / 流式草稿 /
// 待发附件」四份状态的更新顺序被旧版隐式定义过（例如 finish 里先设 session_id
// 再重新拉会话列表），组件只该负责画，顺序错了会出现「回答完列表不刷新」这类鬼影。
//
// 与后端的分工（旧版注释原文保留）：文件先单独 POST 到 /api/chat/attachments
// 落盘，拿回一个 id；提问时只提交 id 列表。好处是图片不会在流式请求体里再走一遍，
// 而且「上传失败」和「提问失败」是两件独立的事，界面能分别说清楚。
import { computed, reactive, ref } from 'vue'
import { api, uploadAttachment } from '../api/client.js'
import { sseConsume } from './useSse.js'
import { useToast } from './useToast.js'

/** localStorage 键与旧版同源：用户在旧版关掉「允许查仓库」，切到 /v2 也应记住 */
export const CHAT_PREF_KEY = 'chat-prefs'
/** 一次最多带几个附件 */
export const CHAT_ATT_MAX = 12
/** 判「贴近底部」的容差（px）。旧版 app.js:4119 */
const NEAR_BOTTOM_PX = 140

function prefs() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_PREF_KEY) || '{}') || {}
  } catch (e) {
    return {}
  }
}

/** 粘贴来的图片在浏览器里叫 image.png / blob，没有有意义的文件名。
 *  给个带时间戳的名字，历史回看时才分得清是哪一张。旧版 app.js:3933 */
export function autoName(file) {
  const t = String((file && file.type) || '')
  let ext = 'png'
  if (t.includes('/')) ext = t.split('/')[1].split('+')[0] || 'png'
  if (ext === 'jpeg') ext = 'jpg'
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `粘贴图片-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`
    + `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.${ext}`
}

export function useChat() {
  const { toast } = useToast()

  // ---- 偏好（localStorage 备份）----
  const saved = prefs()
  const useRepo = ref(saved.useRepo !== false)
  const allowPatch = ref(saved.allowPatch !== false)

  function persistPrefs() {
    try {
      localStorage.setItem(CHAT_PREF_KEY, JSON.stringify({
        useRepo: useRepo.value, allowPatch: allowPatch.value,
      }))
    } catch (e) { /* 隐私模式不记忆即可 */ }
  }

  // ---- 会话 ----
  const sessions = ref([])
  const sessionsError = ref('')
  /** '' = 还没落盘的新会话（首次发送时由后端创建） */
  const current = ref('')
  const messages = ref([])

  /** 当前这一次流式请求的中断句柄（stop() 用） */
  let abortRef = null

  // ---- 发送 / 流式 ----
  const busy = ref(false)
  /** 生成中的「草稿」：思考流、正文流、实时工具调用 */
  const draft = reactive({ think: '', out: '' })
  const liveTools = ref([])
  const status = ref('')
  const toolsText = ref('')
  const stopRequested = ref(false)

  // ---- 附件 ----
  /** 已上传到后端、还没随提问发出去的附件（顺序即用户加入顺序）。
   *  存的是后端返回的元数据（id/name/kind/size/url），发送时只提交 id 列表。 */
  const pendingAtts = ref([])
  const uploading = ref(0)

  const sendDisabled = computed(() => busy.value)
  const canSend = computed(() => !busy.value && !uploading.value)

  // ---------------------------------------------------------------- 会话列表
  async function loadSessions() {
    try {
      const j = await api.chatSessions()
      sessions.value = (j && j.sessions) || []
      sessionsError.value = ''
    } catch (e) {
      sessionsError.value = e && e.message ? e.message : String(e)
    }
  }

  async function openSession(sid) {
    if (busy.value) { toast('正在回答中，等它结束再切换', 'err'); return }
    try {
      const j = await api.chatSession(sid)
      current.value = sid
      messages.value = (j && j.session && j.session.messages) || []
    } catch (e) {
      toast(`打开会话失败：${e && e.message ? e.message : e}`, 'err')
    }
  }

  function newSession() {
    if (busy.value) { toast('正在回答中，等它结束再新建', 'err'); return }
    current.value = ''
    messages.value = []
    toast('已开一个新会话，提问后才会落盘')
  }

  async function clearSession() {
    if (busy.value) { toast('正在回答中', 'err'); return }
    if (!current.value) { messages.value = []; return }
    if (!window.confirm('清空当前会话的消息？（会话本身保留）')) return
    try {
      await api.chatClear(current.value)
      messages.value = []
      await loadSessions()
      toast('已清空')
    } catch (e) {
      toast(`清空失败：${e && e.message ? e.message : e}`, 'err')
    }
  }

  async function deleteSession() {
    if (busy.value) { toast('正在回答中', 'err'); return }
    if (!current.value) { toast('当前是尚未保存的新会话', 'err'); return }
    if (!window.confirm('删除这个会话？删除后不可恢复。')) return
    try {
      await api.chatDelete(current.value)
      current.value = ''
      messages.value = []
      await loadSessions()
      toast('已删除会话')
    } catch (e) {
      toast(`删除失败：${e && e.message ? e.message : e}`, 'err')
    }
  }

  // ------------------------------------------------------------------ 附件
  function removeAtt(i) {
    pendingAtts.value.splice(i, 1)
  }

  function clearAtts() {
    pendingAtts.value = []
    uploading.value = 0
  }

  /** 统一的入队口径：文本/图片都走这里，避免 paste 与 drop 两份逻辑漂移 */
  async function uploadFiles(files) {
    const list = Array.from(files || []).filter(Boolean)
    if (!list.length) return
    const room = CHAT_ATT_MAX - pendingAtts.value.length - uploading.value
    if (room <= 0) {
      toast(`最多一次带 ${CHAT_ATT_MAX} 个附件`, 'err')
      return
    }
    const use = list.slice(0, room)
    if (list.length > room) toast(`一次最多 ${CHAT_ATT_MAX} 个附件，已只取前 ${room} 个`, 'err')
    uploading.value += use.length
    for (const f of use) {
      try {
        const j = await uploadAttachment(
          f,
          f.name && f.name !== 'blob' ? f.name : autoName(f)
        )
        if (j && j.attachment) pendingAtts.value.push(j.attachment)
      } catch (e) {
        toast(`「${f.name || '文件'}」上传失败：${e && e.message ? e.message : e}`, 'err')
      } finally {
        uploading.value = Math.max(0, uploading.value - 1)
      }
    }
  }

  /** 粘贴事件：有文件就必须接管，否则浏览器只会把图片丢掉。纯文本粘贴保持默认行为 */
  function onPaste(e) {
    const items = (e.clipboardData && e.clipboardData.items) || []
    const files = []
    for (const it of items) {
      if (it.kind !== 'file') continue
      const f = it.getAsFile()
      if (f) files.push(f)
    }
    if (!files.length) return
    e.preventDefault()
    uploadFiles(files)
  }

  function onDrop(e) {
    const dt = e.dataTransfer
    if (dt && dt.files && dt.files.length) uploadFiles(dt.files)
  }

  // ------------------------------------------------------------ 发送 / 停止
  function onEvent(evt) {
    if (!evt) return
    if (evt.type === 'stage') { status.value = evt.stage || ''; return }
    if (evt.type === 'tool') {
      liveTools.value.push({ name: evt.tool, arguments: evt.arguments, chars: evt.chars })
      toolsText.value = `已检索仓库 ${liveTools.value.length} 次`
      return
    }
    if (evt.type === 'ai_delta') {
      if (evt.kind === 'reasoning') draft.think += evt.text || ''
      else draft.out += evt.text || ''
      return
    }
    if (evt.type === 'fatal') status.value = `出错了：${evt.error || ''}`
  }

  function resetLive() {
    draft.think = ''
    draft.out = ''
    liveTools.value = []
    toolsText.value = ''
  }

  /**
   * 发送当前输入。
   * @param {string} text textarea 的当前值（由组件传入，composable 不管输入框）
   * @returns {Promise<{artifact: object|null}|null>} 有新产出物时返回它，供外部刷新侧栏
   */
  async function send(text) {
    if (busy.value) return null
    if (uploading.value) { toast('还有附件正在上传，稍等一下', 'err'); return null }
    const body = String(text || '').trim()
    const atts = pendingAtts.value.slice()
    // 「只贴一张图、什么都不写」是真实用法（截图本身就是问题），所以两者有一即可
    if (!body && !atts.length) { toast('先写点什么，或粘贴一张截图', 'err'); return null }

    clearAtts()
    messages.value.push({
      role: 'user', content: body, ts: new Date().toISOString(),
      meta: atts.length ? { attachments: atts } : undefined,
    })
    busy.value = true
    stopRequested.value = false
    resetLive()
    status.value = '已提交，等待模型响应…'

  const ctrl = new AbortController()
  abortRef = ctrl
  let done = null
  let errMsg = ''
  try {
    // sseConsume 返回的是 done **事件**（`{type:'done', payload:{...}}`），
    // 真正的数据在 .payload 里 —— 旧版也是这么拆的（app.js:4286 `chatFinish(done.payload)`）。
    // 少拆一层会静默变成「（没有产出内容）」，不报错，最难查。
    done = await sseConsume('/api/chat/stream', {
      session_id: current.value,
      message: body,
      attachment_ids: atts.map((a) => a.id),
      use_repo: useRepo.value,
      allow_patch: allowPatch.value,
    }, onEvent, ctrl.signal)
  } catch (e) {
    const aborted = e && (e.name === 'AbortError' || String(e).includes('abort'))
    errMsg = aborted
      ? '已停止接收输出。服务端会把这一次跑完并存进会话，稍后刷新会话即可看到。'
      : String((e && e.message) || e)
  } finally {
    busy.value = false
    status.value = ''
    toolsText.value = ''
    abortRef = null
  }

  const payload = done && done.payload ? done.payload : null

  if (!payload && !errMsg) {
    errMsg = '连接中断了（服务端可能仍在跑完这次调用，稍后刷新能看到结果）'
  }

    let artifact = null
    if (payload) {
      if (payload.session_id) current.value = payload.session_id
      artifact = payload.artifact || null
      messages.value.push({
        role: 'assistant',
        content: payload.reply || '（没有产出内容）',
        ts: new Date().toISOString(),
        meta: {
          tools: payload.tools || [],
          artifact: artifact ? artifact.id : '',
          artifactTitle: artifact ? artifact.title : '',
          artifactFiles: artifact ? (artifact.files || []) : [],
          error: payload.error || '',
          ai_mode: payload.ai_mode || '',
        },
      })
    } else {
      messages.value.push({
        role: 'assistant', content: `⚠️ ${errMsg}`,
        ts: new Date().toISOString(), meta: { error: errMsg },
      })
    }

    await loadSessions()
    if (artifact) {
      toast(`已生成改动提案（${artifact.file_count} 个文件），采纳才会写工作区`, 'ok')
    }
    return { artifact }
  }

  function stop() {
    stopRequested.value = true
    if (abortRef) {
      try { abortRef.abort() } catch (e) { /* 已经断开 */ }
    }
  }

  return {
    // 偏好
    useRepo, allowPatch, persistPrefs,
    // 会话
    sessions, sessionsError, current, messages,
    loadSessions, openSession, newSession, clearSession, deleteSession,
    // 发送
    busy, draft, liveTools, status, toolsText, stopRequested,
    send, stop,
    // 附件
    pendingAtts, uploading, removeAtt, clearAtts, uploadFiles, onPaste, onDrop,
    canSend, sendDisabled,
  }
}
