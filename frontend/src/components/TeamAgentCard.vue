<script setup>
// 单个子 Agent 的**名片**（2026-10-14 方案二）。
//
// 分工一眼读完：emoji + 角色名 + 阶段 + 状态 + 职责 + 当前动作 + 进度 + 产出文件。
// 逐字输出**不在这里**了 —— 它搬进右侧大抽屉（TeamAgentTerminal），点名片即可打开。
// 原来四张卡各带一个折叠终端（pre 封顶 150px），既挤又看不到内容，是本次改版的主因。
//
// ⚠️ 两个不能改的契约：
//   1. 根节点必须是 `.team-agent`，且 `#team-agents` 里正好四张
//      （tests/check_vue_team_ui.py: `#team-agents .team-agent` × 4，`.team-agent.done`）；
//   2. status 类（idle / working / done / error / waiting）是 CSS 与用例的状态钩子。
import { computed } from 'vue'

const props = defineProps({
  agent: { type: Object, required: true },
  /** 视图层翻译好的状态文案（口径只在 useTeam 里维护一份） */
  statusText: { type: String, default: '' },
  /** 是否是大抽屉里正在展示的那个角色 */
  picked: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

const prog = computed(() => props.agent.progress || {})
const pct = computed(() => (prog.value.total ? Math.round((prog.value.done / prog.value.total) * 100) : 0))
const files = computed(() => props.agent.files || [])
const shownFiles = computed(() => files.value.slice(0, 3))
const extraFiles = computed(() => Math.max(0, files.value.length - 3))
const note = computed(() => (props.agent.notes || []).slice(-1)[0] || '')

/** 短路径：末两段。旧版 shortPath（团队页签里的局部用法） */
function shortPath(p) {
  const parts = String(p || '').split('/')
  return parts.length <= 2 ? String(p || '') : parts.slice(-2).join('/')
}
</script>

<template>
  <div class="team-agent" :class="[agent.status || 'idle', { picked }]"
       :data-agent="agent.id"
       :style="{ '--agent-color': agent.color || '#6366f1' }"
       tabindex="0" role="button"
       :aria-label="`查看 ${agent.name || agent.id} 的实时输出`"
       @click="emit('select', agent.id)"
       @keydown.enter.prevent="emit('select', agent.id)"
       @keydown.space.prevent="emit('select', agent.id)">
    <div class="team-agent-head">
      <span class="team-dot" />
      <span class="team-agent-emoji">{{ agent.emoji || '' }}</span>
      <span class="team-agent-name">{{ agent.name || agent.id }}</span>
      <span class="team-agent-state">{{ statusText || '' }}</span>
      <!-- 阶段徽标靠右（.team-agent-stage 的 margin-left:auto）：桌面（≥1240）下
           本面板只有 ~700px，名字必须整行不折，让位的是它 —— 它是次要信息，
           且可以省略号截断。 -->
      <el-tag effect="light" round class="team-agent-stage">{{ agent.stage || '' }}</el-tag>
    </div>
    <!-- 职责文案限 2 行截断（CSS -webkit-line-clamp）；补 title 让悬停能读全文 -->
    <div class="team-agent-duty" :title="agent.duty || ''">{{ agent.duty || '' }}</div>
    <div class="team-agent-now">{{ agent.current || '待命中' }}</div>
    <div v-if="prog.total" class="team-progress" :title="`${prog.done}/${prog.total} 个工作项`">
      <i :style="{ width: pct + '%' }" />
    </div>
    <div v-if="note" class="team-agent-note">{{ note }}</div>
    <div v-if="shownFiles.length" class="team-agent-files">
      <span v-for="f in shownFiles" :key="f" class="team-file-chip" :title="f">{{ shortPath(f) }}</span><span v-if="extraFiles" class="team-file-chip">+{{ extraFiles }}</span>
    </div>
    <div class="team-agent-cta">
      {{ picked ? '正在这里看它的输出' : '看它的逐字输出' }}
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
    </div>
  </div>
</template>
