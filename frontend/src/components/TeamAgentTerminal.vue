<script setup>
// 单个子 Agent 的**大终端**（2026-10-14 方案二：名片墙 + 大抽屉里的输出区）。
//
// 为什么从 TeamAgentCard 里搬出来：原来逐字输出折叠在卡片内部（<details> 默认收起、
// pre 封顶 150px），四张卡挤在一行，跑起来也「什么都看不到」。现在卡片只留身份与
// 状态（名片），输出整体搬进右侧 620px 的抽屉里常开、无高度封顶。
//
// ⚠️ 逐字输出依旧**不走响应式**（与旧版 teamStreamDelta 同一条理由）：agent_delta
// 每个 token 一次，走 Vue 的 diff → patch 会把一次长作业变成几万次重渲染。
// 父组件（TeamPane）在缓冲区出现新增量时直接调 append()，只往 <pre> 追加文本。
// props.stream 只用于「组件挂载 / 切换角色后把已有缓冲回填一遍」（refill）。
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  agent: { type: Object, required: true },
  /** 该角色的逐字缓冲区 {think, out}，可能为 undefined */
  stream: { type: Object, default: null },
  /** AGENT_STATUS[status]，视图层已翻译好（文案口径只在 useTeam 里维护一份） */
  statusText: { type: String, default: '' },
})

const thinkEl = ref(null)
const outEl = ref(null)
/** 思考默认收起：输出才是主角，思考按需展开（旧版是 append 时强行 open） */
const showThink = ref(false)

/** 贴近底部判定：旧版 teamStreamDelta 用 40px 容差 */
function nearBottom(el) {
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

/** 把缓冲区回填到 <pre>。组件挂载（含切换角色后的重建）时调用 */
async function refill() {
  const st = props.stream
  if (!st || (!st.think && !st.out)) return
  await nextTick()
  if (st.think && thinkEl.value) thinkEl.value.textContent = st.think
  if (st.out && outEl.value) outEl.value.textContent = st.out
  stickToBottom()
}

watch(() => props.agent, refill, { deep: false, immediate: true })

/** 逐字追加入口。父组件拿到实例后调用（defineExpose 暴露） */
function append(kind, text) {
  if (!text) return
  const el = kind === 'reasoning' ? thinkEl.value : outEl.value
  if (!el) return
  const stick = nearBottom(el)
  el.textContent += text
  if (stick) el.scrollTop = el.scrollHeight
}

/** 贴底。终端刚从隐藏切到可见时 scrollTop 会停在 0（隐藏期间赋值无效），要补一次 */
function stickToBottom() {
  for (const el of [outEl.value, thinkEl.value]) {
    if (el) el.scrollTop = el.scrollHeight
  }
}

/** 清空终端。开始新作业 / 打开历史回放时缓冲区整体重置，已挂载的 <pre> 得跟着清，
 *  否则下一轮跑起来之前，抽屉里还留着上一轮的文字。 */
function reset() {
  if (thinkEl.value) thinkEl.value.textContent = ''
  if (outEl.value) outEl.value.textContent = ''
  showThink.value = false
}

/** 思考 / 输出块是否可见 —— 有缓冲内容就显示（沿用「始终渲染 + hidden 类」的契约） */
const hasThink = computed(() => !!(props.stream && props.stream.think))
const hasOut = computed(() => !!(props.stream && props.stream.out))

defineExpose({ append, refill, stickToBottom, reset })
</script>

<template>
  <div class="team-term" :data-agent="agent.id"
       :style="{ '--agent-color': agent.color || '#6366f1' }">
    <div class="team-term-bar">
      <span class="team-term-dot" />
      <span class="team-term-title">{{ agent.emoji }} {{ agent.name || agent.id }}</span>
      <span class="team-term-stage">{{ agent.stage || '' }}</span>
      <span class="team-term-status">{{ statusText || '' }}</span>
      <span class="team-term-now" :title="agent.current || ''">{{ agent.current || '待命中' }}</span>
    </div>
    <div class="ai-stream team-term-stream">
      <div class="ai-block think" :class="{ hidden: !hasThink }">
        <div class="ai-block-label team-think-toggle" :title="showThink ? '收起思考' : '展开思考'"
             @click="showThink = !showThink">
          思考 {{ showThink ? '▾' : '▸' }}
        </div>
        <pre ref="thinkEl" :class="{ 'term-collapsed': !showThink }" />
      </div>
      <div class="ai-block out" :class="{ hidden: !hasOut }">
        <div class="ai-block-label">输出</div>
        <pre ref="outEl" />
      </div>
    </div>
    <div v-if="!hasThink && !hasOut" class="team-term-empty">
      这个角色还没有输出。作业跑起来后，它的逐字输出会实时出现在这里；<br>
      也可以点上面的角色标签切过去看别人在写什么。
    </div>
  </div>
</template>
