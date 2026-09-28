// SSE 消费：POST + fetch ReadableStream，逐事件回调。
//
// 这是旧版 `sseConsume()` 的原样移植 —— 那段解析逻辑经过实测（跨 chunk 边界、
// 心跳注释、坏 JSON 容错），迁移期不重写、只搬家。
//
// 为什么不用原生 EventSource：它只支持 GET、不能带请求体，而本项目所有流式
// 接口（/api/run/stream、/api/chat/stream、/api/team/stream…）都要 POST JSON。
//
// 协议约定（后端 web/server.py 的 _sse 封装）：
//   - 事件以空行 `\n\n` 分隔，行前缀 `data:`
//   - `: ping` 是 15s 心跳注释，须忽略
//   - **任何流结尾必发 {"type":"done"}**，本函数把它单独返回

import { ref } from 'vue'

const DONE = 'done'

/**
 * 消费一个 SSE 流。
 * @param {string} url
 * @param {object} bodyObj 请求体（会被 JSON 序列化）
 * @param {(evt: object) => void} onEvent 每条非 done 事件的回调
 * @param {AbortSignal} [signal] 用于「停止」按钮主动断开
 * @returns {Promise<object|null>} done 事件；流结束没收到则返回 null
 */
export async function sseConsume(url, bodyObj, onEvent, signal) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyObj),
    signal,
  })
  if (!r.ok || !r.body) {
    let msg = `HTTP ${r.status}`
    try {
      const j = await r.json()
      if (j && j.error) msg = j.error
      else if (j && j.detail) msg = Array.isArray(j.detail) ? j.detail.join('\n') : j.detail
    } catch (_) { /* 非 JSON 错误体 */ }
    throw new Error(msg)
  }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let doneEvt = null
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let sep
      while ((sep = buf.indexOf('\n\n')) !== -1) {
        const chunk = buf.slice(0, sep)
        buf = buf.slice(sep + 2)
        for (const line of chunk.split('\n')) {
          const t = line.trim()
          if (!t.startsWith('data:')) continue     // 含 ': ping' 心跳
          const payload = t.slice(5).trim()
          if (!payload) continue
          let evt
          try {
            evt = JSON.parse(payload)
          } catch (_) {
            continue                               // 坏行跳过，不中断整条流
          }
          if (evt.type === DONE) {
            doneEvt = evt
            continue
          }
          onEvent(evt)
        }
      }
    }
  } catch (e) {
    try { reader.cancel() } catch (_) { /* 已断开 */ }
    throw e
  }
  return doneEvt
}

/**
 * 把一个 SSE 流包成 Vue 状态：running / error / 主动停止。
 * 视图里只需 `const s = useSse(); await s.start(url, body, onEvt)`。
 */
export function useSse() {
  const running = ref(false)
  const error = ref('')
  let ctrl = null

  async function start(url, bodyObj, onEvent) {
    if (running.value) return null
    running.value = true
    error.value = ''
    ctrl = new AbortController()
    try {
      return await sseConsume(url, bodyObj, onEvent, ctrl.signal)
    } catch (e) {
      if (e && e.name === 'AbortError') return null   // 用户主动停止，不算错误
      error.value = e && e.message ? e.message : String(e)
      throw e
    } finally {
      running.value = false
      ctrl = null
    }
  }

  function stop() {
    if (ctrl) {
      try { ctrl.abort() } catch (_) { /* 已结束 */ }
    }
  }

  return { running, error, start, stop }
}
