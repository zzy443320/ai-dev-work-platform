<script setup>
// 长任务作业页签（#pane-team）。旧版 index.html:353-533 的骨架 +
// app.js:3136-3820 全部逻辑的等价迁移。
//
// 六个面板（顺序与 id 都是既有用例锁定的，见 tests/check_team_ui.py 的 PANELS）：
//   #team-run-panel       作业表单（标题 / 范围 / 技术栈 / 轮次 / 描述 / 角色开关）
//   #team-board-panel     子 Agent 看板（实时逐字输出）
//   #team-plan-panel      工作项 + 复核结论
//   #team-timeline-panel  实时时间线（可筛选 / 放大）
//   #team-intervene-panel 人工介入（追问 / 纠偏 / 重规划 / 暂停 / 跳过 / 终止）
//   #team-history-panel   历史作业（可回放）
//
// 与其它页签一样**必须解构 useTeam()**：`<script setup>` 只对顶层绑定做模板自动
// 解包，写 `const t = useTeam()` 再在模板里用 `t.task` 会拿到 ref 对象本身。
import { computed, onMounted, ref, watch } from 'vue'
import { useTeam, AGENT_STATUS, ITEM_STATUS, RUN_STATUS, VERDICT_TEXT,
  INTERVENE_TEXT, TEAM_FRAMEWORKS, TEAM_ROLE_KEYS } from '../composables/useTeam.js'
import { useFold } from '../composables/useFold.js'
import { useToast } from '../composables/useToast.js'
import { useRunModal } from '../composables/useRunModal.js'
import { relTime } from '../utils/format.js'
import TeamAgentCard from '../components/TeamAgentCard.vue'
import TeamPlanItem from '../components/TeamPlanItem.vue'

const {
  roles, title, scope, framework, rounds, task, notes, roleOn,
  running, paused, replaying, agents, plan, review,
  interventions, filter, runMeta, interveneHint, planSummary, stream,
  visibleLogLines, hasLog, filterOptions, setFilter,
  start, intervene, sendMessage, askReplan, interveneAgent, interveneText,
  runs, runsError, loadRuns, openRun, replay, loadRoles,
  ensure,
} = useTeam()

const { isCollapsed, toggleFold } = useFold()
const { toast } = useToast()

const emit = defineEmits(['open-artifact'])

/** 角色开关的展示文案（emoji + 名字从 roles 里取，保证与后端定义一致） */
function roleEmoji(id) {
  const r = roles.value.find((x) => x.id === id)
  return r ? r.emoji : ''
}
function roleName(id) {
  const r = roles.value.find((x) => x.id === id)
  return r ? r.name : id
}

/** 运行中/暂停中才允许介入，模板里多处判断 */
const canIntervene = computed(() => running.value)

// ---- 时间线放大：走 #runmodal（旧版直接灌 DOM，这里改走单例 openRunModal）----
const logEl = ref(null)
const { openRunModal } = useRunModal()
function openLog() {
  const el = logEl.value
  if (!el || !el.textContent.trim()) return
  openRunModal('长任务作业 · 实时时间线', el.innerHTML)
}

/** 历史卡片的 onClick 回调 —— 旧版是内联 onclick="openTeamRun('id')" */
function onRunClick(id) {
  openRun(id)
}

/** 用户点了「开始作业」 */
function onStart() {
  start()
}

/** 介入按钮统一入口（带确认的按旧版语义自己 toast） */
function onIntervene(kind) {
  if (kind === 'stop') {
    intervene('stop')
    return
  }
  intervene(kind)
}

/** 历史卡片的状态类名。旧版 teamRunCard 里 cls 的三分支 */
function runCls(x) {
  return x.status === 'done' ? 'applied' : (x.status === 'failed' ? 'apply_failed' : 'pending')
}
function runProg(x) {
  return x.item_total ? `${x.item_done}/${x.item_total} 项` : '—'
}

/** 工作项/角色卡片上用到的「短路径」——旧版 shortPath，取末两段 */
function shortPath(p) {
  const parts = String(p || '').split('/')
  return parts.length <= 2 ? String(p || '') : parts.slice(-2).join('/')
}

// ---- 逐字输出：不走响应式，直接写卡片里的 <pre> ----
// 为什么不让 TeamAgentCard 用 `{{ stream[a.id].out }}` 绑？
// agent_delta 的事件频率极高（每个 token 一次），走 Vue 的响应式 → diff → patch
// 会把一次长作业变成几万次重渲染，卡片一多直接卡死。旧版 teamStreamDelta 就是
// 直接 `pre.textContent += text`，这里保留同样的做法：
//   - useTeam 维护 `stream` 缓冲区（卡片因 agent_status 重渲染后靠它回填）；
//   - 父组件 watch 缓冲区长度变化，把新增量 append 给对应卡片实例。
const cardRefs = ref({})       // agent id -> TeamAgentCard 实例
const seenLen = {}             // agent id -> {think, out} 已消费的长度
function setCardRef(id) {
  return (el) => {
    if (el) cardRefs.value[id] = el
    else delete cardRefs.value[id]
  }
}

watch(
  () => stream.value,
  (buf) => {
    Object.keys(buf || {}).forEach((id) => {
      const st = buf[id] || { think: '', out: '' }
      const seen = seenLen[id] || (seenLen[id] = { think: 0, out: 0 })
      const card = cardRefs.value[id]
      for (const [k, kind] of [['think', 'reasoning'], ['out', 'content']]) {
        const txt = st[k] || ''
        if (txt.length <= seen[k]) {
          // 被重置（新作业 / 回放）→ 计数归零，等下一次 watch 从 0 追平
          if (txt.length < seen[k]) seen[k] = txt.length
          continue
        }
        if (card) card.append(kind, txt.slice(seen[k]))
        seen[k] = txt.length
      }
    })
  },
  { deep: true }
)

onMounted(async () => {
  await ensure()
})
</script>

<template>
  <!-- 作业表单 -->
  <section class="panel collapsible" id="team-run-panel"
           :class="{ collapsed: isCollapsed('team-run-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="7" r="3" /><circle cx="18" cy="7" r="3" /><circle cx="12" cy="18" r="3" /><path d="M9 7h6M8.3 9.4l2.5 6M15.7 9.4l-2.5 6" /></svg>
        长任务作业 · 多子 Agent 协作
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('team-run-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
    </div>
    <div class="run-form">
      <label class="field-inline grow">
        <span>任务标题</span>
        <input type="text" id="team-title" v-model="title" placeholder="例如：订单列表页从旧架构迁移到新架构">
      </label>
      <label class="field-inline grow">
        <span>改造范围（glob，可留空）</span>
        <input type="text" id="team-scope" v-model="scope" placeholder="src/views/order/** , src/api/**">
      </label>
    </div>
    <div class="run-form">
      <label class="field-inline">
        <span>技术栈</span>
        <select id="team-framework" v-model="framework">
          <option v-for="f in TEAM_FRAMEWORKS" :key="f">{{ f }}</option>
        </select>
      </label>
      <label class="field-inline">
        <span>最多返工轮次</span>
        <input type="number" id="team-rounds" v-model.number="rounds" min="0" max="5">
      </label>
    </div>
    <label class="field">
      <span>任务描述（越大越模糊的任务越要写清目标与不做什么）</span>
      <textarea id="team-task" rows="4" v-model="task" placeholder="要改什么、改成什么样、哪些必须保持不变（对外接口 / 埋点 / 兼容性）。例如：把 src/views 下的 12 个列表页统一迁移到新表格组件，接口调用改为 src/api/v2，页面路由与参数保持不变。"></textarea>
    </label>
    <label class="field">
      <span>其他约定（可选）</span>
      <input type="text" id="team-notes" v-model="notes" placeholder="目录规范、必须复用的工具函数、禁止引入的依赖等">
    </label>
    <div class="run-form team-roles-bar">
      <span class="scope-label">参与角色：</span>
      <label class="switch" title="没有决策官就无法拆解任务，因此它始终参与">
        <input type="checkbox" id="team-role-planner" v-model="roleOn.planner" checked disabled>
        <span class="track" /><span class="switch-label">🧭 决策官</span>
      </label>
      <label class="switch"><input type="checkbox" id="team-role-coder" v-model="roleOn.coder"><span class="track" /><span class="switch-label">⌨️ 编码工程师</span></label>
      <label class="switch"><input type="checkbox" id="team-role-tester" v-model="roleOn.tester"><span class="track" /><span class="switch-label">🧪 测试工程师</span></label>
      <label class="switch"><input type="checkbox" id="team-role-reviewer" v-model="roleOn.reviewer"><span class="track" /><span class="switch-label">🔍 复核官</span></label>
    </div>
    <div class="run-form">
      <button class="btn-primary" id="btn-team-run" :disabled="running" @click="onStart">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3" /></svg>
        开始作业
      </button>
      <button class="btn-ghost" id="btn-team-pause" :disabled="!(running && !paused)" @click="onIntervene('pause')">暂停</button>
      <button class="btn-ghost" id="btn-team-resume" :disabled="!(running && paused)" @click="onIntervene('resume')">继续</button>
      <button class="btn-ghost" id="btn-team-replan" :disabled="!running" @click="askReplan()">要求重规划</button>
      <button class="btn-ghost" id="btn-team-skip" :disabled="!running" @click="onIntervene('skip')">跳过当前项</button>
      <button class="btn-danger" id="btn-team-stop" :disabled="!running" @click="onIntervene('stop')">终止</button>
      <button class="btn-ghost" @click="loadRuns()">刷新历史</button>
    </div>
    <div class="info-note">
      把大任务拆给不同职责的子 Agent 去跑：<strong>决策官</strong>拆工作项定方案，<strong>编码工程师</strong>逐项实现，<strong>测试工程师</strong>补测试，<strong>复核官</strong>收口找问题。<br>
      跑的过程中右侧看板会实时显示每个角色在做什么，你可以随时<strong>追问 / 纠偏 / 重规划 / 暂停 / 跳过 / 终止</strong>——注意模型调用无法中途打断，这些指令都在<strong>当前步骤结束后的安全边界</strong>生效，界面会如实标出。<br>
      全程<strong>不写目标仓库</strong>：结果汇总成一份产出物，人工采纳才写工作区（不 commit、不 push）。
    </div>
  </section>

  <!-- 子 Agent 看板 -->
  <section class="panel collapsible" id="team-board-panel"
           :class="{ collapsed: isCollapsed('team-board-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>
        子 Agent 看板
        <span class="count-badge" :class="{ hidden: !running }" id="team-live-badge">运行中</span>
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('team-board-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <span class="muted" id="team-run-meta">{{ runMeta }}</span>
    </div>
    <div id="team-agents" class="team-agents">
      <div v-if="!agents.length" class="empty">还没有作业。点「开始作业」后，每个子 Agent 会占一张卡片，实时显示它当前在做什么、计划进度和它产出的文件。</div>
      <TeamAgentCard v-for="a in agents" :key="a.id" :ref="setCardRef(a.id)" :agent="a"
                     :stream="stream[a.id]"
                     :status-text="AGENT_STATUS[a.status] || a.status || ''" />
    </div>
  </section>

  <!-- 工作项 + 复核 -->
  <section class="panel collapsible" id="team-plan-panel"
           :class="{ collapsed: isCollapsed('team-plan-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>
        工作项
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('team-plan-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <span class="muted" id="team-plan-summary">{{ planSummary }}</span>
    </div>
    <div id="team-plan" class="team-plan">
      <div v-if="!(plan.items || []).length" class="empty">还没有作业。填好左边的任务描述点「开始作业」，决策官会先把活拆成工作项。</div>
      <TeamPlanItem v-for="it in (plan.items || [])" :key="it.id" :item="it"
                    :status-text="ITEM_STATUS[it.status] || it.status || ''" />
    </div>
    <div id="team-review" class="team-review" :class="{ hidden: !(review && review.verdict) }">
      <template v-if="review && review.verdict">
        <div class="team-review-head">
          <span class="team-verdict" :class="review.verdict">复核 · {{ VERDICT_TEXT[review.verdict] || review.verdict }}</span>
          <span v-if="review.round" class="muted">第 {{ review.round }} 轮返工后</span>
        </div>
        <div v-if="review.summary" class="team-review-sum">{{ review.summary }}</div>
        <template v-if="(review.risks || []).length">
          <div class="team-sec-t">风险</div>
          <ul class="team-list"><li v-for="(x, i) in review.risks" :key="i">{{ x }}</li></ul>
        </template>
        <template v-if="(review.must_fix || []).length">
          <div class="team-sec-t">必须修</div>
          <ul class="team-list bad">
            <li v-for="(m, i) in review.must_fix" :key="i">{{ m.item || '' }} {{ m.why || '' }}<template v-if="m.how"> → {{ m.how }}</template></li>
          </ul>
        </template>
        <template v-if="(review.suggestions || []).length">
          <div class="team-sec-t">建议</div>
          <ul class="team-list"><li v-for="(x, i) in review.suggestions" :key="i">{{ x }}</li></ul>
        </template>
      </template>
    </div>
  </section>

  <!-- 实时时间线 -->
  <section class="panel collapsible" id="team-timeline-panel"
           :class="{ collapsed: isCollapsed('team-timeline-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4" /></svg>
        实时时间线
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('team-timeline-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <label class="filter-wrap">
        <span class="muted">只看</span>
        <select id="team-filter" :value="filter" @change="setFilter($event.target.value)">
          <option value="all">全部</option>
          <option v-for="a in filterOptions" :key="a.id" :value="a.id">{{ a.emoji ? a.emoji + ' ' : '' }}{{ a.name }}</option>
        </select>
      </label>
    </div>
    <div class="log-wrap">
      <!-- ⚠️ #team-log 与 #run-log 同一套 CSS 契约：空态必须真正 :empty，
           占位提示才由 :empty::before 的 attr(data-placeholder) 显示出来。
           所以这里内容容器只在有行时渲染，且外层 <pre> 内部不留任何空节点。 -->
      <pre id="team-log" class="log" ref="logEl"
           data-placeholder="开始作业后，这里按时间顺序显示每个子 Agent 的动作、人工介入与阶段结论（大模型的逐字思考/输出在各自看板卡片里）"
      ><span v-if="visibleLogLines.length" class="team-lines"><span v-for="l in visibleLogLines" :key="l.seq" class="live-line" :class="l.kind"><span class="t">{{ l.t }}</span><span>{{ l.text }}</span></span></span></pre>
      <button class="log-expand" :class="{ hidden: !hasLog }" id="btn-team-expand"
              title="放大查看" @click="openLog">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" /><line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" /></svg>
      </button>
    </div>
  </section>

  <!-- 人工介入 -->
  <section class="panel collapsible" id="team-intervene-panel"
           :class="{ collapsed: isCollapsed('team-intervene-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" /></svg>
        人工介入 · 追问与纠偏
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('team-intervene-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <span class="muted" id="team-intervene-hint">{{ interveneHint }}</span>
    </div>
    <div class="run-form">
      <label class="field-inline">
        <span>发给</span>
        <select id="team-intervene-agent" v-model="interveneAgent">
          <option value="*">全部角色</option>
          <option value="planner">🧭 决策官</option>
          <option value="coder">⌨️ 编码工程师</option>
          <option value="tester">🧪 测试工程师</option>
          <option value="reviewer">🔍 复核官</option>
        </select>
      </label>
      <label class="field-inline grow">
        <span>指令内容</span>
        <input type="text" id="team-intervene-text" v-model="interveneText"
               placeholder="例如：不要新建工具文件，直接改 src/utils/request.ts；迁移顺序先把 api 层做完再做页面"
               @keydown.enter.exact.prevent="sendMessage()">
      </label>
      <button class="btn-primary" id="btn-team-send" :disabled="!canIntervene" @click="sendMessage()">发送</button>
    </div>
    <div id="team-intervene-list" class="team-intervene-list">
      <div v-if="!interventions.length" class="empty">还没有人工指令。跑的过程中发现某个子 Agent 方向不对，随时在这里纠正它。</div>
      <div v-for="(m, i) in interventions.slice().reverse()" :key="m.seq ?? i"
           class="team-msg" :class="m.consumed_by ? 'ack' : 'pending'">
        <div class="team-msg-head">
          <span>{{ String(m.ts || '').replace('T', ' ').slice(5, 19) }} · {{ INTERVENE_TEXT[m.kind] || m.kind }} → {{ m.agent === '*' ? '全部角色' : roleName(m.agent) }}</span>
          <span class="team-msg-state">{{ m.consumed_by ? `已由 ${roleName(m.consumed_by)} 收到` : '待生效（当前步骤结束后）' }}</span>
        </div>
        <div v-if="m.text" class="team-msg-text">{{ m.text }}</div>
      </div>
    </div>
    <p class="panel-note">
      指令会在该角色<strong>下一步开始时</strong>注入它的提示词，并标记为最高优先级；被签收后会在这里显示「已由 XX 收到」。<br>
      如果只是想调整计划，用上面的「要求重规划」；想让决策官重排顺序也可以直接把话说给它听。
    </p>
  </section>

  <!-- 历史作业 -->
  <section class="panel collapsible" id="team-history-panel"
           :class="{ collapsed: isCollapsed('team-history-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.4" /><polyline points="3 4 3 9 8 9" /><path d="M12 7.5v5l3.5 2" /></svg>
        历史作业
        <span class="count-badge" :class="{ hidden: !runs.length }" id="team-history-count">{{ runs.length }}</span>
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('team-history-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
    </div>
    <div id="team-history" class="proposal-grid">
      <div v-if="runsError" class="empty">历史作业加载失败: {{ runsError }}</div>
      <div v-else-if="!runs.length" class="empty">还没有作业记录。跑一次之后，计划、每个角色的动作轨迹与人工介入都会留在这里。</div>
      <div v-for="x in runs" :key="x.id" class="pcard" :class="`pc-${runCls(x)}`" @click="onRunClick(x.id)">
        <div class="pcard-top">
          <span class="st" :class="`st-${runCls(x)}`">{{ RUN_STATUS[x.status] || x.status || '—' }}</span>
          <span v-if="x.running" class="team-running-chip">运行中</span>
          <span class="pcard-id">{{ x.id || '' }}</span>
          <span class="pcard-time">{{ relTime(x.updated || x.created) }}</span>
        </div>
        <div class="pcard-title">{{ x.title || '(无标题)' }}</div>
        <div class="pcard-meta">
          <span>工作项 {{ runProg(x) }}</span>
          <span>{{ x.file_count || 0 }} 个文件</span>
          <span>AI {{ x.ai_mode || '—' }}</span>
          <span v-if="x.interventions">人工介入 {{ x.interventions }} 次</span>
        </div>
        <div v-if="x.error" class="pcard-root" style="color:var(--danger)">{{ x.error }}</div>
        <div v-else-if="x.summary" class="pcard-root">{{ x.summary }}</div>
      </div>
    </div>
    <p class="panel-note">点开任意一次作业可以回看它的计划、每个角色的动作轨迹与人工介入记录（不含大模型逐字输出——那部分体量太大，不落盘）。</p>
  </section>
</template>
