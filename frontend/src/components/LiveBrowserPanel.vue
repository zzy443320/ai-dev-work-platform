<script setup>
// 内嵌浏览器预览面板（2026-10-14）：远程浏览器的"直播 + 遥控"。
//
// 交互契约：
//   * 画面是 <img>（后端 JPEG 帧，未动就复用上一帧），容器按 1440x900 等比留黑边；
//     所有指针坐标按 img 的**实际显示区**换算，黑边里的点击不转发。
//   * 指针三原语 down/move/up + wheel 直发；"点击"由前端合成（位移 < 4px 才算），
//     这样拖拽平移画布与单击选中互不打架。
//   * 键盘只在面板获得焦点后转发（外框 tabindex=0，聚焦时外圈有焦点环），
//     防止用户只想切页签却把按键打进远程页面。
//   * 「读属性」开着时，单击之后额外调 /api/live/inspect：canvas 里点不到 DOM，
//     但 Figma 的右侧属性面板是普通 DOM，后端从最右竖条收文本（不依赖它的
//     内部类名——那是构建期哈希，版本一换就失效）。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useLiveBrowser } from '../composables/useLiveBrowser.js'

const emit = defineEmits(['inspected', 'stopped'])

const { running, session, frameData, start, stop, sendInput, inspect, refreshStatus }
  = useLiveBrowser()

const VIEW_W = 1440
const VIEW_H = 900
const inspectMode = ref(true)     // 默认开：点节点顺手读属性，是这个面板的主要用途
const busy = ref(false)
const err = ref('')
const lastInspect = ref(null)
const frameEl = ref(null)

const loginText = computed(() => {
  const s = session.value || {}
  return s.login === 'ok' ? '已登录' : s.login === 'login_page' ? '登录页' : '未登录'
})
const urlShort = computed(() => {
  try { return new URL((session.value || {}).url || '').host } catch (_) { return '' }
})

onMounted(() => { refreshStatus() })
onBeforeUnmount(() => { /* 会话保留：切页签再回来还能续上（refreshStatus 恢复画面） */ })

async function open(url) {
  err.value = ''
  try { await start(url) } catch (e) { err.value = String(e.message || e) }
}
async function close() {
  await stop()
  emit('stopped')
}

// ---- 坐标换算：把点击限制在等比缩放后的画面内，黑边不转发 ----
function _pos(e) {
  const el = frameEl.value
  if (!el) return null
  const box = el.getBoundingClientRect()
  const scale = Math.min(box.width / VIEW_W, box.height / VIEW_H)
  const w = VIEW_W * scale, h = VIEW_H * scale
  const ox = box.left + (box.width - w) / 2, oy = box.top + (box.height - h) / 2
  const x = e.clientX - ox, y = e.clientY - oy
  if (x < 0 || y < 0 || x > w || y > h) return null
  return { x: Math.round(x / scale), y: Math.round(y / scale), vw: VIEW_W, vh: VIEW_H }
}

let downAt = null        // 指针按下时的 CSS 坐标（判断是否拖拽）
let downPos = null       // 指针按下时的后端坐标
let dragging = false
async function _send(payload) {
  try { await sendInput(payload) } catch (_) { /* 断流时静默，重连会补上 */ }
}
function _btn(e) { return e.button === 2 ? 'right' : 'left' }

// 点击语义：**不拆成 down/up**。Playwright 的 mouse.down()+up() 分两次 HTTP 调用
// 时偶发不产生 click 事件（实测 3 连点只中 1 次），而原子化的 kind:"click" 稳定。
// 所以普通点击等 pointerup 时一发 click；只有位移超过 4px 才升级成拖拽
// （先补 down 再 move，松开补 up）——Figma 画布的平移对 4px 的迟滞不敏感。
async function onDown(e) {
  const p = _pos(e)
  if (!p) return
  downAt = { x: e.clientX, y: e.clientY }
  downPos = p
  dragging = false
}
async function onMove(e) {
  if (!downAt) return
  const p = _pos(e)
  if (!p) return
  if (!dragging && (Math.abs(e.clientX - downAt.x) > 4 || Math.abs(e.clientY - downAt.y) > 4)) {
    dragging = true
    await _send({ ...downPos, kind: 'down', button: _btn(e) })
  }
  if (dragging) await _send({ ...p, kind: 'move' })
}
async function onUp(e) {
  if (!downAt) return
  const p = _pos(e) || downPos
  const wasDrag = dragging
  downAt = null
  dragging = false
  if (wasDrag) {
    await _send({ ...p, kind: 'up', button: _btn(e) })
    return
  }
  if (e.button === 2) {                       // 右键：只转发，不读属性
    await _send({ ...p, kind: 'click', button: 'right' })
    return
  }
  if (inspectMode.value) {                    // 读属性模式：inspect 自己完成"点 + 读"
    await doInspect(p)
  } else {
    await _send({ ...p, kind: 'click' })
  }
}
async function doInspect(p) {
  if (busy.value) { await _send({ ...p, kind: 'click' }); return }
  busy.value = true
  try {
    const r = await inspect(p)
    lastInspect.value = r
    emit('inspected', r)
  } catch (e) {
    err.value = String(e.message || e)
    await _send({ ...p, kind: 'click' })      // 读失败至少把点击本身送到
  } finally {
    busy.value = false
  }
}
async function onWheel(e) {
  const p = _pos(e)
  if (!p) return
  e.preventDefault()
  await _send({ ...p, kind: 'wheel', dx: Math.round(e.deltaX), dy: Math.round(e.deltaY) })
}
async function onKey(e) {
  if (!running.value) return
  const k = e.key
  if (k === 'Tab') return        // Tab 用来离开面板，不转发
  e.preventDefault()
  await _send({ kind: 'key', key: k.length === 1 ? k.toUpperCase() : k,
                text: k.length === 1 ? k : '' })
}
function onCtx(e) { e.preventDefault() }
async function onDbl(e) {
  const p = _pos(e)
  if (p) await _send({ ...p, kind: 'click', dbl: true })
}

defineExpose({ open, close, running, session })
</script>

<template>
  <section class="panel live-panel" id="live-browser-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2" /><path d="M2 9h20" /><circle cx="5" cy="6.5" r=".6" /><circle cx="7.5" cy="6.5" r=".6" /></svg>
        内嵌看稿 · 受控浏览器
      </h2>
      <span class="live-dot" :class="{ on: running }" aria-hidden="true" />
      <el-button size="small" id="btn-live-stop" :disabled="!running" @click="close">停止会话</el-button>
    </div>

    <div class="live-bar">
      <span class="live-chip" :class="`is-${(session || {}).login || 'unknown'}`">{{ running ? loginText : '未连接' }}</span>
      <span class="live-url" :title="(session || {}).url">{{ urlShort || '—' }}</span>
      <span class="live-chip is-info" title="agent 负责打开与登录代填；画面与输入此刻由你直接控制">agent 开路 · 人工接管</span>
      <label class="live-toggle" :class="{ on: inspectMode }" title="点节点后自动读右侧属性面板">
        <input type="checkbox" v-model="inspectMode" />
        <span>点选读属性{{ busy ? '…' : '' }}</span>
      </label>
    </div>

    <div v-if="err" class="live-err">{{ err }}</div>

    <div class="live-stage" tabindex="0" @keydown="onKey" @contextmenu="onCtx"
         @wheel="onWheel" @dragover.prevent>
      <img v-if="running && frameData" ref="frameEl" class="live-frame" :src="frameData"
           alt="受控浏览器画面" @pointerdown="onDown" @pointermove="onMove"
           @pointerup="onUp" @pointerleave="onUp" @dblclick="onDbl" draggable="false" />
      <div v-else-if="running" class="empty">正在连接受控浏览器…</div>
      <div v-else class="empty">
        <template v-if="err">{{ err }}</template>
        <template v-else>还没开会话。在右侧参数轨填好 Figma 链接后点「内嵌看稿」。<br>首次使用需在画面里登录一次（配置页可存账号密码，之后自动代填），登录态会保留。</template>
      </div>
    </div>

    <div v-if="lastInspect" class="live-inspect">
      <span class="src-title">属性面板（最近一次点选）</span>
      <pre class="live-inspect-text">{{ (lastInspect.panel || []).join('\n') || '（右栏没有读到文本）' }}</pre>
    </div>
    <div class="info-note">键盘输入只在面板获得焦点后转发；滚轮 = 缩放画布，拖拽 = 平移。遇验证码 / SSO 直接在这里人工操作，登录态会保存供下次复用。</div>
  </section>
</template>

<style scoped>
.live-panel { display: flex; flex-direction: column; min-height: 0; }
.live-dot { width: 7px; height: 7px; border-radius: 99px; background: var(--txt4, #4a5878); opacity: .5; margin-left: auto; flex: none; }
.live-dot.on { background: var(--primary); opacity: 1; box-shadow: 0 0 0 3px var(--primary-soft); }
.live-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 10px 0 8px; }
.live-chip { font-size: 10.5px; padding: 2px 8px; border-radius: 99px; border: 1px solid var(--border); color: var(--txt3); background: var(--code-bg); flex: none; }
.live-chip.is-ok { color: var(--success); border-color: var(--success-soft); }
.live-chip.is-login_page { color: var(--warn); border-color: var(--warn-soft); }
.live-chip.is-info { color: var(--txt2); }
.live-url { font-size: 11.5px; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.live-err { font-size: 11.5px; color: var(--danger); margin: 4px 0 0; line-height: 1.6; }
.live-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--txt3); cursor: pointer; flex: none; margin-left: auto; }
.live-toggle.on { color: var(--primary); }
.live-stage { position: relative; flex: 1; min-height: 320px; background: var(--term, #0b1120);
  border: 1px solid var(--line2); border-radius: var(--r-m); overflow: hidden; outline: none; }
.live-stage:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.live-frame { width: 100%; height: 100%; object-fit: contain; display: block; cursor: crosshair; user-select: none; }
.live-stage .empty { position: absolute; inset: 0; display: grid; place-items: center; text-align: center;
  color: var(--muted); font-size: 12.5px; line-height: 1.9; padding: 0 20px; }
.live-inspect { margin-top: 10px; min-width: 0; }
.live-inspect-text { margin: 6px 0 0; padding: 10px 12px; max-height: 150px; overflow: auto;
  background: var(--term, #0b1120); border: 1px solid var(--line2); border-radius: var(--r-m);
  color: var(--text-dim); font-size: 11.5px; line-height: 1.7; white-space: pre-wrap; word-break: break-all; }
</style>
