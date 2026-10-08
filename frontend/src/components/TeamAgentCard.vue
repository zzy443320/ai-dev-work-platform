<script setup>
// 单个子 Agent 的看板卡片。旧版 app.js:3226 `teamAgentCard` + 3268 `teamStreamDelta`
// + 3220 `teamScrollCard` 的等价复刻。
//
// ⚠️ 逐字输出（.ai-block pre）**不走响应式**：agent_delta 事件频率极高，
// 走 Vue 的 diff 会把每次增量都变成一次重渲染，长作业下会卡。
// 这里沿用旧版的做法：直接往 DOM 的 <pre> 追加 textContent，并只在贴近底部时跟随滚动。
// props.stream 只用于「卡片因为 agent_status 重渲染后，把已有缓冲回填一遍」
// （旧版 teamRenderAgents 末尾的那段回填）。
//
// ⚠️ 空白保真：旧版模板串里 `stage · status` 之间是 ` · `（前后各一空格），
// Vue 模板的换行缩进会被吃成单个空格甚至没有，所以这里显式用模板插值拼成一个字符串。
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  agent: { type: Object, required: true },
  /** 该角色的逐字缓冲区 {think, out}，可能为 undefined */
  stream: { type: Object, default: null },
  /** TEAM_AGENT_STATUS[status]，视图层已翻译好（文案口径只在 useTeam 里维护一份） */
  statusText: { type: String, default: '' },
})

const thinkEl = ref(null)
const outEl = ref(null)
const liveEl = ref(null)

const prog = computed(() => props.agent.progress || {})
const pct = computed(() => (prog.value.total ? Math.round((prog.value.done / prog.value.total) * 100) : 0))
const files = computed(() => props.agent.files || [])
const shownFiles = computed(() => files.value.slice(0, 5))
const extraFiles = computed(() => Math.max(0, files.value.length - 5))
const note = computed(() => (props.agent.notes || []).slice(-1)[0] || '')

/** 卡片头上的一行「拆解 · 执行中」 */
const stageLine = computed(() =>
  `${props.agent.stage || ''} · ${props.statusText || ''}`
)

/** 短路径：末两段。旧版 shortPath（团队页签里的局部用法） */
function shortPath(p) {
  const parts = String(p || '').split('/')
  return parts.length <= 2 ? String(p || '') : parts.slice(-2).join('/')
}

/** 贴近底部判定：旧版 teamStreamDelta 用 40px 容差 */
function nearBottom(el) {
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

/** 把缓冲区回填到 <pre>。卡片重渲染（agent_status / item_files）后调用 */
async function refill() {
  const st = props.stream
  if (!st || (!st.think && !st.out)) return
  await nextTick()
  const map = { think: thinkEl.value, out: outEl.value }
  for (const k of ['think', 'out']) {
    if (!st[k] || !map[k]) continue
    map[k].textContent = st[k]
  }
  if (liveEl.value) liveEl.value.setAttribute('open', '')
  for (const el of [thinkEl.value, outEl.value]) {
    if (el) el.scrollTop = el.scrollHeight
  }
}

// 卡片被重渲染（props.agent 换了引用）时回填逐字缓冲区
watch(() => props.agent, refill, { deep: false, immediate: true })

/**
 * 供父组件（TeamPane）在收到 agent_delta 时调用的追加入口。
 * 通过 defineExpose 暴露，父组件拿到卡片实例后直接 append。
 */
function append(kind, text) {
  if (!text) return
  const el = kind === 'reasoning' ? thinkEl.value : outEl.value
  if (!el) return
  if (liveEl.value) liveEl.value.setAttribute('open', '')
  const stick = nearBottom(el)
  el.textContent += text
  if (stick) el.scrollTop = el.scrollHeight
}

/** think / out 块是否可见 —— 有缓冲内容就显示 */
const hasThink = computed(() => !!(props.stream && props.stream.think))
const hasOut = computed(() => !!(props.stream && props.stream.out))

defineExpose({ append, refill })
</script>

<template>
  <!-- 外壳换成 el-card；内部 .team-agent 必须原样保留：
       append() 直接写里面的 <pre>，且 .team-agent.<status> 是被用例断言的类钩子 -->
  <el-card shadow="never" class="team-agent-card">
    <div class="team-agent" :class="agent.status || 'idle'" :data-agent="agent.id"
         :style="{ '--agent-color': agent.color || '#6366f1' }">
      <div class="team-agent-head">
        <span class="team-dot" />
        <span class="team-agent-emoji">{{ agent.emoji || '' }}</span>
        <span class="team-agent-name">{{ agent.name || agent.id }}</span>
        <!-- 阶段 / 状态徽标：用 el-tag 承载 -->
        <el-tag effect="light" round class="team-agent-stage">{{ stageLine }}</el-tag>
      </div>
      <!-- 职责文案 CSS 里限 2 行截断（-webkit-line-clamp），窄屏实测会被裁掉一行多；
           补 title 让悬停能读全文，否则被截的部分没有任何找回途径。 -->
      <div class="team-agent-duty" :title="agent.duty || ''">{{ agent.duty || '' }}</div>
      <div class="team-agent-now">{{ agent.current || '待命中' }}</div>
      <div v-if="prog.total" class="team-progress" :title="`${prog.done}/${prog.total} 个工作项`">
        <i :style="{ width: pct + '%' }" />
      </div>
      <div v-if="note" class="team-agent-note">{{ note }}</div>
      <div v-if="shownFiles.length" class="team-agent-files">
        <span v-for="f in shownFiles" :key="f" class="team-file-chip" :title="f">{{ shortPath(f) }}</span><span v-if="extraFiles" class="team-file-chip">+{{ extraFiles }}</span>
      </div>
      <!-- ⚠️ 折叠区用原生 <details>：append() 通过 liveEl.setAttribute('open','') 控制展开，
           不能用 el-collapse（其根不是 details，setAttribute 会失效） -->
      <details class="team-live" ref="liveEl">
        <summary>实时思考 / 输出</summary>
        <div class="ai-stream">
          <div class="ai-block think" :class="{ hidden: !hasThink }"><div class="ai-block-label">思考</div><pre ref="thinkEl" /></div>
          <div class="ai-block out" :class="{ hidden: !hasOut }"><div class="ai-block-label">输出</div><pre ref="outEl" /></div>
        </div>
      </details>
    </div>
  </el-card>
</template>
