<script setup>
// 长任务作业 · **运行监视**（一块卡，三段）：看板 → 中间段 → 人工介入。
//
// 2026-10-15 第七轮（用户点名）：右侧不再堆三块独立面板，而是合成一块 ——
//   · 上看板：四个角色一眼读分工、进度、产出文件；
//   · 中间段：实时时间线 / 工作项，二选一（互斥切换）；
//   · 下介入条：常驻卡底，滚时间线时输入框不跑掉，随手插话。
//
// 2026-10-15 第八轮（用户点名，推翻第七轮的一个决定）：
//   ① 点角色名片看逐字输出**恢复浮层抽屉**（#team-drawer）——「就地切视图」
//      让中间段的时间线消失，看输出时就看不到现场了；抽屉盖在上面，关掉即回。
//      抽屉 Teleport 到 body、始终渲染只切 .hidden（hidden 契约）。
//   ② 时间线「只看」筛选真正落到每一行：中间段不再被输出视图占用后，
//      筛选是时间线唯一的切片手段，用例里对真实数据断言（见 check_vue_team_ui）。
//   ③ 时间线区域**占满中间段剩余高度**（旧的全局 .log 封顶 420px，
//      在等高布局里下方留出一大截空白 —— 已在 cockpit 作用域内解除封顶）。
//
// ⚠️ useTeam 是**模块级单例**（useTeam.js: sharedTeam），所以这里直接调用它拿到的
// 就是 TeamPane 那一份状态，不需要把十几个 ref 用 props 传一遍。
import { computed, nextTick, ref, watch } from 'vue'
import { useTeam, AGENT_STATUS, ITEM_STATUS, VERDICT_TEXT, INTERVENE_TEXT } from '../composables/useTeam.js'
import { useRunModal } from '../composables/useRunModal.js'
import { useToast } from '../composables/useToast.js'
import TeamAgentCard from './TeamAgentCard.vue'
import TeamAgentTerminal from './TeamAgentTerminal.vue'
import TeamPlanItem from './TeamPlanItem.vue'

const {
  roles, running, paused, replaying, agents, plan, review, runMeta,
  filter, filterOptions, setFilter, visibleLogLines, hasLog, planSummary,
  interventions, interveneHint, interveneAgent, interveneText,
  sendMessage, stream,
} = useTeam()

const { toast } = useToast()
const { openRunModal } = useRunModal()

const canIntervene = computed(() => running.value)

/** 中间段视图：'timeline' | 'items'（互斥）。角色输出挪去了浮层抽屉。 */
const view = ref('timeline')

/** 浮层抽屉当前打开的角色 id（null = 关闭）。null 而不是 ''：模板只判真假。 */
const drawerAgent = ref('')
const drawerAgentObj = computed(() => (agents.value || []).find((a) => a.id === drawerAgent.value) || null)

function openAgent(id) {
  if (!id) return
  drawerAgent.value = id
}
function closeDrawer() {
  drawerAgent.value = ''
}

/** 角色格显示名以后端 /api/team/roles 为准（时间线筛选与介入下拉共用） */
function roleName(id) {
  const r = (roles.value || []).find((x) => x.id === id)
  return r ? r.name : id
}
function agentName(a) {
  return (a && (a.name || a.id)) || ''
}
function agentPct(a) {
  const p = (a && a.progress) || {}
  return p.total ? Math.round((p.done / p.total) * 100) : 0
}
function agentFiles(a) {
  return (a && a.files) || []
}
function agentNote(a) {
  const n = (a && a.notes) || []
  return n[n.length - 1] || ''
}

/** 把「人工介入」的收件人切到当前正在看输出的这个角色（发送流程一行没改） */
function pickInterveneTarget() {
  const a = drawerAgentObj.value
  if (!a) return
  interveneAgent.value = a.id
  toast(`已把「人工介入」的收件人选为 ${agentName(a)}`, 'ok')
}

/** 作业开始/切换历史回放：中间段回到时间线，抽屉跟着关掉（新现场新看） */
watch([running, replaying], ([r, rp]) => {
  if (r || rp) {
    view.value = 'timeline'
    drawerAgent.value = ''
  }
})

// ---- 时间线放大：走 #runmodal（与 TeamPane 原来同一入口）----
const logEl = ref(null)
function openLog() {
  const el = logEl.value
  if (!el || !el.textContent.trim()) return
  openRunModal('长任务作业 · 实时时间线', el.innerHTML)
}

/* ---- 逐字输出：不走响应式，直接写终端里的 <pre> ----
   agent_delta 每个 token 一次事件，走 Vue diff 会把一次长作业变成几万次重渲染。
   useTeam 维护 stream 缓冲区，这里 watch 它的增量 append 给对应角色的终端实例；
   终端常驻在抽屉里（hidden 契约：始终渲染），隐藏期间 append 落空但 seenLen
   照常推进 —— 不对，终端一直在 DOM 里，append 不会落空；真正的坑是隐藏期间
   给 <pre> 赋 scrollTop 无效，所以打开抽屉时要 stickToBottom() 补一次。 */
const termRefs = ref({})
const seenLen = {}
function setTermRef(id) {
  return (el) => {
    if (el) termRefs.value[id] = el
    else delete termRefs.value[id]
  }
}
watch(
  () => stream.value,
  (buf) => {
    const b = buf || {}
    // 缓冲区被清掉的角色（开始新作业 / 打开历史回放）：已挂载的终端跟着清空
    Object.keys(seenLen).forEach((id) => {
      if (b[id]) return
      const seen = seenLen[id]
      if (!seen) return
      const term = termRefs.value[id]
      if (term && term.reset) term.reset()
      seen.think = 0
      seen.out = 0
    })
    Object.keys(b).forEach((id) => {
      const st = b[id] || { think: '', out: '' }
      const seen = seenLen[id] || (seenLen[id] = { think: 0, out: 0 })
      const term = termRefs.value[id]
      for (const [k, kind] of [['think', 'reasoning'], ['out', 'content']]) {
        const txt = st[k] || ''
        if (txt.length <= seen[k]) {
          if (txt.length < seen[k]) seen[k] = txt.length
          continue
        }
        if (term) term.append(kind, txt.slice(seen[k]))
        seen[k] = txt.length
      }
    })
  },
  { deep: true }
)

/** 打开抽屉后把终端补到最新（隐藏期间 scrollTop 赋值是无效的） */
watch(drawerAgent, (id) => {
  if (!id) return
  nextTick(() => {
    const t = termRefs.value[id]
    if (t && t.stickToBottom) t.stickToBottom()
  })
})
</script>

<template>
  <!-- 运行监视：一整块卡，三段竖排（看板 / 中间段 / 介入条）。
       ⚠️ 四段容器的 id（#team-board-panel / #team-plan-panel / #team-timeline-panel /
       #team-intervene-panel）沿用契约 —— 界面用例按这些 id 找元素，
       DOM 顺序也是「看板 → 工作项 → 时间线 → 介入」。 -->
  <section class="panel team-cockpit" id="team-cockpit-panel">
    <div class="panel-head ck-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>
        运行监视 · 子 Agent 实况
      </h2>
      <span class="count-badge" :class="{ hidden: !running }" id="team-live-badge">运行中</span>
      <span class="muted" id="team-run-meta">{{ runMeta }}</span>
      <span class="sp-flex" />

      <!-- 中间段两视图切换（互斥；角色输出在浮层抽屉里，点名片打开） -->
      <div class="ck-views" role="tablist">
        <button type="button" class="ck-vbtn" :class="{ on: view === 'timeline' }"
                @click="view = 'timeline'">实时时间线</button>
        <button type="button" class="ck-vbtn" :class="{ on: view === 'items' }"
                @click="view = 'items'">工作项 <span class="ck-vnum" id="team-plan-summary">{{ planSummary }}</span></button>
      </div>

      <label class="filter-wrap ck-filter" :class="{ hidden: view !== 'timeline' }">
        <span class="muted">只看</span>
        <el-select id="team-filter" :model-value="filter"
                   @update:model-value="setFilter" placeholder="全部">
          <el-option value="all" label="全部" data-value="all" />
          <el-option v-for="a in filterOptions" :key="a.id" :value="a.id"
                     :label="a.emoji ? a.emoji + ' ' + a.name : a.name" :data-value="a.id" />
        </el-select>
      </label>
      <el-button text circle class="log-expand" :class="{ hidden: !(hasLog && view === 'timeline') }"
                 id="btn-team-expand" title="放大查看" @click="openLog">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" /><line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" /></svg>
      </el-button>
    </div>

    <!-- ① 看板段：四个角色一眼读分工与进度；点一张弹浮层抽屉看它的逐字输出 -->
    <div id="team-board-panel" class="ck-board">
      <div id="team-agents" class="team-agents">
        <div v-if="!agents.length" class="empty">还没有作业。点上方「开始作业」后，四个角色各占一张名片：先一眼读分工与进度，再点名片看它的逐字输出。</div>
        <TeamAgentCard v-for="a in agents" :key="a.id" :agent="a"
                       :status-text="AGENT_STATUS[a.status] || a.status || ''"
                       :picked="drawerAgent === a.id"
                       @select="openAgent" />
      </div>
    </div>

    <!-- ② 中间段：工作项 / 时间线 二选一；时间线占满剩余高度 -->
    <div class="ck-mid">
      <!-- 工作项（含复核结论）：决策官拆出来的活 + 复核官收口结论 -->
      <div id="team-plan-panel" class="ck-view ck-items" :class="{ hidden: view !== 'items' }">
        <div id="team-plan" class="team-plan">
          <div v-if="!(plan.items || []).length" class="empty">还没有作业。点上方「开始作业」，决策官会先把活拆成工作项。</div>
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
      </div>

      <!-- 实时时间线：占满中间段剩余高度（封顶已在样式里按作用域解除） -->
      <div id="team-timeline-panel" class="ck-view ck-log" :class="{ hidden: view !== 'timeline' }">
        <div class="log-wrap">
          <!-- ⚠️ #team-log 与 #run-log 同一套 CSS 契约：空态必须真正 :empty，
               占位提示才由 :empty::before 的 attr(data-placeholder) 显示出来。 -->
          <pre id="team-log" class="log" ref="logEl"
               data-placeholder="开始作业后，这里按时间顺序显示每个子 Agent 的动作、人工介入与阶段结论（大模型的逐字思考/输出点角色名片看）"
          ><span v-if="visibleLogLines.length" class="team-lines"><span v-for="l in visibleLogLines" :key="l.seq" class="live-line" :class="l.kind"><span class="t">{{ l.t }}</span><span>{{ l.text }}</span></span></span></pre>
        </div>
      </div>
    </div>

    <!-- ③ 人工介入（常驻卡底）：输入框永远在，插话不用先找面板 -->
    <div id="team-intervene-panel" class="ck-intervene">
      <div class="run-form ck-intervene-form">
        <label class="field-inline">
          <span>发给</span>
          <el-select id="team-intervene-agent" v-model="interveneAgent">
            <el-option value="*" label="全部角色" data-value="*" />
            <el-option value="planner" label="🧭 决策官" data-value="planner" />
            <el-option value="coder" label="⌨️ 编码工程师" data-value="coder" />
            <el-option value="tester" label="🧪 测试工程师" data-value="tester" />
            <el-option value="reviewer" label="🔍 复核官" data-value="reviewer" />
          </el-select>
        </label>
        <label class="field-inline grow">
          <span>指令内容</span>
          <el-input type="text" id="team-intervene-text" v-model="interveneText"
                    placeholder="例如：不要新建工具文件，直接改 src/utils/request.ts；迁移顺序先把 api 层做完再做页面"
                    @keydown.enter.exact.prevent="sendMessage()" />
        </label>
        <el-button type="primary" id="btn-team-send" :disabled="!canIntervene" @click="sendMessage()">发送</el-button>
      </div>
      <div class="ck-hint-row">
        <span class="muted" id="team-intervene-hint">{{ interveneHint }}</span>
        <span class="muted">指令在该角色<strong>下一步开始时</strong>注入；被签收后显示「已由 XX 收到」。</span>
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
    </div>

    <!-- ④ 角色输出浮层抽屉（第八轮恢复）：点名片打开，盖在运行台上，
         关掉即回时间线 —— 看输出和看现场不再互斥。Teleport 到 body 脱离
         页签 grid（面板 overflow:hidden 会把 absolute 浮层裁掉）；
         hidden 契约：始终渲染，只切 .hidden。 -->
    <Teleport to="body">
      <div id="team-drawer" :class="{ hidden: !drawerAgent }">
        <div class="team-drawer-mask" @click="closeDrawer"></div>
        <aside class="team-drawer-box" role="dialog" aria-modal="true"
               :aria-label="drawerAgentObj ? agentName(drawerAgentObj) + ' 的逐字输出' : '角色输出'">
          <div class="team-drawer-head">
            <span v-if="drawerAgentObj" class="team-dot" :style="{ background: drawerAgentObj.color || 'var(--primary)' }" />
            <b>{{ drawerAgentObj ? drawerAgentObj.emoji + ' ' + agentName(drawerAgentObj) : '角色输出' }}</b>
            <span v-if="drawerAgentObj" class="muted">{{ drawerAgentObj.stage || '' }} · {{ drawerAgentObj.current || '待命中' }}</span>
            <span class="sp-flex" />
            <el-button size="small" id="btn-team-target-agent" @click="pickInterveneTarget">把指令发给它</el-button>
            <el-button text id="team-drawer-close" @click="closeDrawer">关闭</el-button>
          </div>
          <div class="team-terms">
            <TeamAgentTerminal v-for="a in agents" :key="a.id" :ref="setTermRef(a.id)"
                               :class="{ hidden: a.id !== drawerAgent }"
                               :agent="a" :stream="stream[a.id]"
                               :status-text="AGENT_STATUS[a.status] || a.status || ''" />
          </div>
        </aside>
      </div>
    </Teleport>
  </section>
</template>
