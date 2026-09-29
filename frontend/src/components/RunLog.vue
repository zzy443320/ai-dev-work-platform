<script setup>
// 流水线日志区。旧版 app.js:1437 `liveSetup` + 1465 `liveLine` + 1480 `liveAI` +
// 1562 `aiReplayHtml` 的等价复刻。
//
// 三种内容叠在同一个 `<pre id="run-log" class="log">` 里（旧版是 innerHTML 直接拼）：
//   1. live 行（带 HH:MM:SS 前缀，按 kind 上色）；
//   2. 大模型实时输出（思考 / 输出两个块，运行中滚动）；
//   3. 运行结束后的结构化结果 HTML + 按工单分组的回放。
//
// ⚠️ 两个必须守住的细节（都是 style.css / 既有用例锁定的契约）：
//   a. 空态必须让 `#run-log` 真正 `:empty` —— CSS 用
//      `#run-log:empty::before { content: attr(data-placeholder) }` 显示占位提示，
//      并靠 `#run-log:empty { display:flex; ... }` 让提示居中（顺带撑满 420px，
//      避免出结果后布局跳动）。多塞一个空 span 就会让 :empty 失效、提示消失。
//   b. 结构化结果块的空白由 `.log .log-html { white-space: normal }` 接管，
//      所以 `v-html` 那一层不能自己加 `white-space: pre-wrap`。
//      这里用 `<pre>` 做容器（与旧版一致），靠 CSS 的 `.log-html` 规则复位。
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  lines: { type: Array, default: () => [] },       // [{t, text, kind}]
  live: { type: Object, default: () => ({ think: '', out: '' }) },
  hasLive: { type: Boolean, default: false },
  replay: { type: Array, default: () => [] },      // [{id, think, out}]
  resultHtml: { type: String, default: '' },
  modelName: { type: String, default: '' },
  /** 运行中为 true：此时才显示「生成中」的空态文案 */
  running: { type: Boolean, default: false },
  title: { type: String, default: '运行结果' },
  /** 点放大时的直连回调 `(title, html) => void`（等价旧版 `openRunModal()`） */
  onOpen: { type: Function, default: null },
})

const emit = defineEmits(['open-proposal', 'expand'])

const logEl = ref(null)
const thinkEl = ref(null)
const outEl = ref(null)

/** 有结构化结果 —— 决定是否换成结果视图、是否显示放大按钮 */
const hasResult = computed(() => !!props.resultHtml)

const PLACEHOLDER = '点击「运行」或「试运行」后，这里会实时显示每个阶段的进展，'
  + '以及大模型的思考与输出'

/** live 行 + 大模型流都存在时才渲染内容容器（否则保持 :empty 走占位提示） */
const hasLiveContent = computed(() => props.lines.length > 0 || props.hasLive)

/** 贴近底部判定：旧版 liveScroll 用 120px、liveAI 用 40px */
function nearBottom(el, tol) {
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < tol
}

watch(
  () => [props.lines.length, props.live.think, props.live.out],
  async () => {
    const logStick = nearBottom(logEl.value, 120)
    const thinkStick = nearBottom(thinkEl.value, 40)
    const outStick = nearBottom(outEl.value, 40)
    await nextTick()
    if (logStick && logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
    if (thinkStick && thinkEl.value) thinkEl.value.scrollTop = thinkEl.value.scrollHeight
    if (outStick && outEl.value) outEl.value.scrollTop = outEl.value.scrollHeight
  }
)

/** 结构化结果里的「查看提案」链接：用事件委托接回上层（旧版是内联 onclick） */
function onResultClick(e) {
  const a = e.target && e.target.closest ? e.target.closest('a[data-proposal]') : null
  if (!a) return
  e.preventDefault()
  emit('open-proposal', a.getAttribute('data-proposal'))
}

/** 放大：把 #run-log 的整块 innerHTML 交给上层灌进 #runmodal（旧版 openRunModal 同语义） */
function onExpand() {
  const el = logEl.value
  if (!el || !el.innerHTML.trim()) return
  // 两条路径都留着：直接调 props 回调（旧版就是 `onclick="openRunModal()"` 直连，
  // 不经过事件总线），同时 emit 让仍想用事件的上层也能接。
  if (props.onOpen) props.onOpen(props.title, el.innerHTML)
  emit('expand', { title: props.title, html: el.innerHTML })
}
</script>

<template>
  <div class="log-wrap">
    <!-- ⚠️ #run-log 容器与 class 一律不动：旧版 innerHTML 直接拼的结构化结果靠它复位空白，
         :empty 占位提示也绑定在它身上。 -->
    <pre
      class="log"
      id="run-log"
      ref="logEl"
      :data-placeholder="PLACEHOLDER"
      @click="onResultClick"
    ><template v-if="hasResult"><span v-html="resultHtml" /><details v-if="replay.length" class="ai-replay"><summary>展开本次大模型的实时思考 / 输出记录</summary><template v-for="r in replay" :key="r.id"><div class="ai-replay-defect">工单 {{ r.id }}</div><div class="ai-stream"><template v-if="r.think"><div class="ai-block think"><div class="ai-block-label">思考</div><pre>{{ r.think }}</pre></div></template><template v-if="r.out"><div class="ai-block out"><div class="ai-block-label">输出</div><pre>{{ r.out }}</pre></div></template></div></template></details></template><template v-else-if="hasLiveContent"><span class="live-lines"><span v-for="(l, i) in lines" :key="i" class="live-line" :class="l.kind"><span class="t">{{ l.t }}</span><span>{{ l.text }}</span></span></span><span class="ai-stream" :class="{ hidden: !hasLive }"><span class="ai-stream-head"><span class="ai-dot" /><span>大模型实时输出</span><span class="ai-stream-meta">{{ modelName }}</span></span><span class="ai-block think" :class="{ hidden: !live.think }"><span class="ai-block-label">思考</span><pre ref="thinkEl">{{ live.think }}</pre></span><span class="ai-block out" :class="{ hidden: !live.out }"><span class="ai-block-label">输出</span><pre ref="outEl">{{ live.out }}</pre></span></span></template></pre>

    <!-- 放大按钮：外层悬浮控件，迁移到 el-button；保留 id / class 钩子与 absolute 定位。 -->
    <el-button
      class="log-expand"
      :class="{ hidden: !hasResult }"
      id="btn-run-expand"
      title="放大查看"
      @click="onExpand"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" /><line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" /></svg>
    </el-button>
  </div>
</template>

<style scoped>
/* el-button 默认有内边距/边框，会破坏 .log-expand 的绝对定位方块外观；
   用 scoped 覆盖回原生 .log-expand 的样式（位置/尺寸由全局 style.css 负责）。 */
.log-expand {
  padding: 0 !important;
  border: 1px solid var(--border-strong) !important;
  background: var(--panel-solid) !important;
}
</style>
