// 内嵌浏览器会话（2026-10-14）：开 / 帧流 / 输入转发 / 点选读属性 / 停。
//
// 为什么是"直播 + 遥控"而不是 iframe：figma.com 回 X-Frame-Options: SAMEORIGIN
// （实测响应头），任何第三方页面都嵌不进去。所以后端起受控 Playwright 浏览器，
// 页面截图推给前端画布，前端把指针/滚轮/键盘转发回去 —— 视觉上内嵌，机制上是
// 远程预览。地基全部复用：持久 profile 与浏览器兜底通道同一个目录（登录态互通），
// 登录代填沿用 verifier 的纪律（只在页面明确是登录表单时才填，不猜登录页）。
//
// 帧协议：POST /api/live/stream 的 SSE，事件 frame { seq, jpeg } 与
// status { session }，结尾 done —— 与 run/team 三条流同构（useSse.sseConsume）。
import { ref } from 'vue'
import { http } from '../api/client.js'
import { sseConsume } from './useSse.js'

const running = ref(false)
const session = ref(null)       // 后端快照：url/title/login/driving/error/frame_seq
const starting = ref(false)
const frameSeq = ref(0)
const frameData = ref('')       // data:image/jpeg;base64,...

let abort = null                // 停止帧流的 AbortSignal
let reconnectTimer = 0
let streamDone = true           // 当前帧流是否已自然结束（done / 断开）

export function useLiveBrowser() {
  /** 开会话并接帧流。失败抛 ApiError（409 = 已有会话在跑）。 */
  async function start(url) {
    if (running.value) throw new Error('内嵌浏览器已在运行')
    starting.value = true
    try {
      const r = await http.post('/api/live/start', { url })
      session.value = r.session
      running.value = true
      _consume()
      return r.session
    } finally {
      starting.value = false
    }
  }

  /** 帧流断了自动重连（会话还在的话）。点「停止」后不再连。 */
  function _consume() {
    abort = new AbortController()
    streamDone = false
    sseConsume('/api/live/stream', {}, (evt) => {
      if (evt.type === 'frame') {
        frameData.value = `data:image/jpeg;base64,${evt.jpeg}`
        frameSeq.value = evt.seq
      } else if (evt.type === 'status') {
        session.value = evt.session
        // 只标记，不 abort：服务端发完这条 status 就会把流正常收尾（done），
        // 中途 abort 会抛 "BodyStreamBuffer was aborted" 的 pageerror（实测）。
        if (evt.session && !evt.session.alive) {
          running.value = false
          session.value = evt.session
        }
      }
    }, abort.signal).then(() => {
      streamDone = true
      if (running.value && !reconnectTimer) {
        reconnectTimer = setTimeout(() => { reconnectTimer = 0; _consume() }, 800)
      }
    }).catch(() => {
      streamDone = true
      if (running.value && !reconnectTimer) {
        reconnectTimer = setTimeout(() => { reconnectTimer = 0; _consume() }, 1000)
      }
    })
  }

  function _markStopped() {
    running.value = false
    session.value = session.value || {}
    if (abort) abort.abort()
  }

  /** 输入转发：kind = move/down/up/click/wheel/key/type。坐标用前端画布坐标
   *  （vw/vh 告诉后端显示尺寸，缩放在后端做）。 */
  async function sendInput(payload) {
    if (!running.value) return null
    return http.post('/api/live/input', payload)
  }

  /** 点选 → 读右侧属性面板。返回 { panel: [行], title, url }。 */
  async function inspect(payload) {
    return http.post('/api/live/inspect', payload)
  }

  async function stop() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = 0 }
    running.value = false
    try {
      const r = await http.post('/api/live/stop', {})
      // 不立刻 abort 帧流：会话结束后服务端会把流正常收尾（done），
      // 中途 abort 会让 Chromium 抛 "BodyStreamBuffer was aborted" 的 pageerror。
      // 这里等它自己结束，2s 还没结束（网络异常）才强行断开兜底。
      for (let i = 0; i < 20 && !streamDone; i++) await new Promise(res => setTimeout(res, 100))
      if (!streamDone && abort) abort.abort()
      return r
    } catch (e) {
      if (abort) abort.abort()
      throw e
    } finally {
      session.value = null
      frameData.value = ''
    }
  }

  /** 会话是否真在跑（页面刷新后状态丢了，问一次后端）。 */
  async function refreshStatus() {
    try {
      const r = await http.get('/api/live/status')
      running.value = !!r.running
      session.value = r.session
      if (r.running && !abort) _consume()
      return r.running
    } catch (_) {
      return false
    }
  }

  return { running, starting, session, frameSeq, frameData, start, stop, sendInput, inspect, refreshStatus }
}
