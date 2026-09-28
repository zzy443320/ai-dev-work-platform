// 长任务作业页签（多子 Agent 协作）的状态与副作用。
// 旧版 app.js:3136-3820 的等价迁移。
//
// 这个页签的状态比其它页签都「活」：一次作业会同时推进四份互相关联的状态 ——
//   1. agents      四个子 Agent 的卡片（状态 / 当前动作 / 进度 / 产出文件 / 最新备注）
//   2. plan        决策官拆出的工作项，状态随 item_status 事件逐条推进
//   3. events      实时时间线（按 agent 筛选）
//   4. interventions 人工介入指令及其签收状态
// 旧版用一堆全局函数各改一部分，更新顺序靠调用者记；这里收成一个 composable，
// 保证「一个事件 → 哪些状态跟着变」只写一遍，视图组件只负责画。
//
// ⚠️ 逐字输出（agent_delta）不放在这里：它是纯增量、频率极高，走 SSE 回调直接
// 写卡片里的 <pre> 更省（旧版 teamStreamDelta 就是这么干的）。这里只保留
// `stream` 缓冲区，用于卡片重渲染后回填。
import { computed, ref } from 'vue'
import { api } from '../api/client.js'
import { sseConsume } from './useSse.js'
import { useToast } from './useToast.js'
import { setRunStatus } from './useRunStatus.js'

/** 后端 /api/team/roles 不可用时的兜底。旧版 app.js:3142 —— 逐条照搬 */
export const TEAM_ROLE_FALLBACK = [
  { id: 'planner', name: '决策官', emoji: '🧭', color: '#6366f1', stage: '拆解',
    duty: '把长任务拆成可独立验收的工作项，定方案、定顺序、定验收标准，并在中途纠偏重规划。' },
  { id: 'coder', name: '编码工程师', emoji: '⌨️', color: '#0ea5e9', stage: '实现',
    duty: '按方案逐个工作项写代码，输出可直接落盘的完整文件内容。' },
  { id: 'tester', name: '测试工程师', emoji: '🧪', color: '#14b8a6', stage: '验证',
    duty: '为每个工作项的产出写测试、指出未覆盖的边界与回归风险。' },
  { id: 'reviewer', name: '复核官', emoji: '🔍', color: '#f59e0b', stage: '收口',
    duty: '通盘审查一致性、遗漏与风险，给出结论与必须修的清单。' },
]

/** 参与角色开关的固定四项。旧版 runTeam() 里硬编码的 `['planner','coder','tester','reviewer']` */
export const TEAM_ROLE_KEYS = ['planner', 'coder', 'tester', 'reviewer']

/** 技术栈下拉项。旧版 index.html:377 */
export const TEAM_FRAMEWORKS = [
  'React + TypeScript', 'Vue 3 + TypeScript', 'React + JavaScript',
  'Vue 2 + JavaScript', '小程序原生', 'uni-app',
]

function hms() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 作业状态。旧版 teamNewState() —— 只保留真实用到的字段，外加 Vue 侧需要的 id 计数器 */
export function teamNewState() {
  return {
    runId: '', running: false, paused: false, replaying: false,
    agents: [], plan: { items: [] }, review: null, events: [], interventions: [],
    stream: {}, filter: 'all', artifact: '', title: '',
  }
}

export function useTeam() {
  const { toast } = useToast()

  // ---- 角色定义（首次进入页签时拉一次，失败用兜底）----
  const roles = ref([])

  // ---- 表单 ----
  const title = ref('')
  const scope = ref('')
  const framework = ref(TEAM_FRAMEWORKS[0])
  const rounds = ref(2)
  const task = ref('')
  const notes = ref('')
  /** 四个角色开关。planner 恒为 true（没有决策官就无法拆解任务） */
  const roleOn = ref({ planner: true, coder: true, tester: true, reviewer: true })

  // ---- 作业状态（一份聚合对象，与旧版 TEAM 同构）----
  const state = ref(teamNewState())
  const agents = ref([])
  const plan = ref({ items: [] })
  const review = ref(null)
  const events = ref([])
  const interventions = ref([])
  const filter = ref('all')
  const artifact = ref('')
  /** 逐字输出缓冲区：agent id -> {think, out}。卡片重渲染后回填用 */
  const stream = ref({})

  const running = computed(() => state.value.running)
  const paused = computed(() => state.value.paused)
  const replaying = computed(() => state.value.replaying)
  const runId = computed(() => state.value.runId)

  /** 面板头部的运行信息。旧版 setTeamRunMeta */
  const runMeta = ref('')
  /** 介入提示。旧版 setTeamControls 里的 #team-intervene-hint */
  const interveneHint = computed(() => {
    if (running.value) return paused.value ? '已暂停 · 等你的指令' : '运行中 · 可随时介入'
    return replaying.value ? '历史回放 · 只读' : '作业未运行'
  })

  /** 工作项完成度。旧版 teamRenderPlanSummary */
  const planSummary = computed(() => {
    const items = plan.value.items || []
    if (!items.length) return ''
    const done = items.filter((i) => i.status === 'done').length
    return `${done}/${items.length} 项完成`
  })

  function role(id) {
    return roles.value.find((r) => r.id === id) || { id, name: id, emoji: '' }
  }

  // ------------------------------------------------------------- 实时时间线
  // 行结构：{t, text, kind, agent, seq}。seq 递增，Vue 用它做 key（同一毫秒多行也稳定）。
  const logLines = ref([])
  let logSeq = 0

  /** 时间线是否已经有内容 —— 决定放大按钮显隐（旧版 openTeamLog 里的判空） */
  const hasLog = computed(() => logLines.value.length > 0)

  function logLine(text, kind = 'dim', agent = '*') {
    logLines.value.push({ seq: ++logSeq, t: hms(), text: String(text ?? ''), kind, agent: agent || '*' })
  }

  /** 切换筛选：只改 filter，模板按 agent 决定显隐（旧版是直接切 DOM class） */
  function setFilter(v) {
    filter.value = v || 'all'
  }

  /** 当前筛选下可见的行 */
  const visibleLogLines = computed(() => {
    const f = filter.value
    if (f === 'all') return logLines.value
    return logLines.value.filter((l) => l.agent === f)
  })

  /** 时间线筛选下拉的选项（按已出现的角色 + 初始四角色）。旧版 teamSyncFilterOptions */
  const filterOptions = computed(() => {
    const seen = new Map()
    ;(agents.value || []).forEach((a) => seen.set(a.id, a))
    logLines.value.forEach((l) => {
      if (l.agent === '*' || seen.has(l.agent)) return
      const r = role(l.agent)
      if (r && r.id) seen.set(r.id, { id: r.id, name: r.name, emoji: r.emoji })
    })
    return Array.from(seen.values())
  })

  // ------------------------------------------------------------- 事件分发的文案
  // 旧版 teamLogEvent —— 把一条事件翻译成一行（或多行）时间线文本。
  // 返回 [{text, kind, agent}]，由 logEvent 统一落地。
  function eventLines(evt) {
    const out = []
    const who = evt.agent ? role(evt.agent) : null
    const tag = who ? `${who.emoji} ${who.name}` : ''
    const ag = evt.agent || '*'
    const t = evt.type
    const push = (text, kind, agent) => out.push({ text, kind, agent: agent || ag })

    if (t === 'run_start') {
      const run = evt.run || {}
      push(`▶ 作业开始：${run.title || ''}（${run.id || ''}）`, 'head', '*')
      ;(run.agents || []).forEach((a) =>
        push(`  参与角色：${a.emoji || ''} ${a.name} —— ${a.duty || ''}`, 'dim', '*'))
      return out
    }
    if (t === 'log') {
      const lv = evt.level
      push(evt.text || '', lv === 'bad' ? 'bad' : lv === 'warn' ? 'warn' : lv === 'human' ? 'head' : 'dim', ag)
      return out
    }
    if (t === 'agent_status') {
      const st = AGENT_STATUS[evt.status] || evt.status
      push(`${tag} ${st}：${evt.current || ''}`,
        evt.status === 'error' ? 'bad' : evt.status === 'done' ? 'ok' : 'dim', ag)
      return out
    }
    if (t === 'plan') {
      const items = (evt.plan || {}).items || []
      push(`🧭 决策官${evt.revised ? '重新' : ''}拆出 ${items.length} 个工作项`
        + (evt.reason ? `（${evt.reason}）` : ''), 'head', 'planner')
      items.forEach((it) =>
        push(`  ${it.id} ${it.title}${(it.files || []).length ? `（${it.files.length} 个文件）` : ''}`,
          'dim', 'planner'))
      return out
    }
    if (t === 'item_status') {
      const cls = evt.status === 'failed' ? 'bad' : evt.status === 'done' ? 'ok'
        : evt.status === 'skipped' ? 'warn' : 'dim'
      push(`工作项 ${evt.item} ${ITEM_STATUS[evt.status] || evt.status}${evt.note ? `：${evt.note}` : ''}`,
        cls, evt.status === 'doing' || evt.status === 'done' ? 'coder' : ag)
      return out
    }
    if (t === 'item_files') {
      const files = (evt.files || []).map((f) => f.path)
      const cases = (evt.cases || []).length
      push(`${tag} 产出 ${files.length} 个文件${files.length ? '：' + files.join('、') : ''}`
        + (cases ? `；涉及 ${cases} 个用例` : '')
        + ((evt.gaps || []).length ? `；存疑 ${evt.gaps.length} 处` : ''), 'ok', ag)
      return out
    }
    if (t === 'review') {
      push(`🔍 复核结论：${VERDICT_TEXT[evt.verdict] || evt.verdict}`
        + (evt.summary ? ` —— ${evt.summary}` : ''), evt.verdict === 'block' ? 'bad' : 'ok', 'reviewer')
      ;(evt.must_fix || []).forEach((m) =>
        push(`  必须修：${m.item || ''} ${m.why || ''}`, 'warn', 'reviewer'))
      return out
    }
    if (t === 'artifact') {
      push(`📦 已生成产出物 ${evt.id}（${evt.file_count} 个文件）——去「产出物」里审 diff 后采纳`, 'head', ag)
      return out
    }
    if (t === 'intervention') {
      const target = evt.agent === '*' ? '全部角色' : `${role(evt.agent).name}`
      push(`✍ 人工 → ${target}：${evt.text || INTERVENE_TEXT[evt.kind] || evt.kind}`, 'head', evt.agent)
      return out
    }
    if (t === 'intervention_ack') {
      push(`✔ ${tag} 已收到该指令，将在下一步生效`, 'ok', ag)
      return out
    }
    if (t === 'run_status') {
      push(evt.detail || `状态 → ${RUN_STATUS[evt.status] || evt.status}`,
        evt.status === 'paused' ? 'warn' : 'ok', ag)
      return out
    }
    if (t === 'run_finish') {
      push(`■ 作业结束：${RUN_STATUS[evt.status] || evt.status}`
        + (evt.summary ? ` —— ${evt.summary}` : ''), evt.status === 'done' ? 'ok' : 'warn', ag)
      return out
    }
    if (t === 'fatal') { push(`✗ ${evt.error || '作业异常'}`, 'bad', ag); return out }
    if (t === 'done') {
      push(`■ 产出 ${evt.payload?.file_count ?? 0} 个文件`
        + `（${RUN_STATUS[evt.payload?.status] || evt.payload?.status || ''}）`, 'ok', ag)
    }
    return out
  }

  /** 把一条事件落成时间线行（可能多行）。旧版 teamLogEvent 的入口 */
  function logEvent(evt) {
    eventLines(evt).forEach((l) => logLine(l.text, l.kind, l.agent))
  }

  // ------------------------------------------------------------- 看板
  function makeAgents() {
    return roles.value.map((r) => ({
      id: r.id, name: r.name, emoji: r.emoji, color: r.color, stage: r.stage, duty: r.duty,
      status: 'idle', current: '待命中', progress: { done: 0, total: 0 }, files: [], notes: [],
    }))
  }

  function upsertAgent(evt) {
    const ag = (agents.value || []).find((x) => x.id === evt.agent)
    if (!ag) return
    if (evt.status) ag.status = evt.status
    if (evt.current !== undefined) ag.current = evt.current
    if (evt.progress && (evt.progress.total || !ag.progress.total)) ag.progress = evt.progress
    if (evt.note) ag.notes = (ag.notes || []).slice(-4).concat([evt.note])
    if (Array.isArray(evt.files)) ag.files = evt.files.slice()
    // 数组内对象的字段是原地改的，ref 的浅层比较不会触发；用一次整体赋值强制刷新
    agents.value = agents.value.slice()
  }

  /** 逐字输出增量。只更新缓冲区；DOM 里的 <pre> 由 TeamAgentCard 直接追加（高频，不走响应式） */
  function streamDelta(id, kind, text) {
    if (!text) return
    const st = stream.value[id] || (stream.value[id] = { think: '', out: '' })
    if (kind === 'reasoning') st.think += text
    else st.out += text
  }

  // ------------------------------------------------------------- 事件分发
  // 旧版 teamOnEvent —— 一个事件改哪几份状态，全写在这里。
  function onEvent(evt) {
    const t = evt.type
    if (t === 'run_id') {
      state.value.runId = evt.run_id || ''
      runMeta.value = `运行中 · ${state.value.runId}`
      return
    }
    if (t === 'agent_delta') { streamDelta(evt.agent, evt.kind, evt.text); return }
    if (t === 'fatal') throw new Error(evt.error || '作业异常')

    events.value.push(evt)
    if (t === 'run_start') {
      const run = evt.run || {}
      state.value.runId = run.id || state.value.runId
      state.value.title = run.title || ''
      if (Array.isArray(run.agents) && run.agents.length) agents.value = run.agents
    } else if (t === 'agent_status') {
      upsertAgent(evt)
    } else if (t === 'plan') {
      plan.value = evt.plan || { items: [] }
    } else if (t === 'item_status') {
      const it = (plan.value.items || []).find((x) => x.id === evt.item)
      if (it) { it.status = evt.status; if (evt.note) it.note = evt.note }
      plan.value = { ...plan.value, items: (plan.value.items || []).slice() }
    } else if (t === 'item_files') {
      const ag = (agents.value || []).find((x) => x.id === evt.agent)
      if (ag) {
        const have = new Set(ag.files || [])
        ;(evt.files || []).forEach((f) => { if (f && f.path) have.add(f.path) })
        ag.files = Array.from(have)
        if (ag.status !== 'error') ag.status = 'working'
        agents.value = agents.value.slice()
      }
    } else if (t === 'review') {
      review.value = evt
    } else if (t === 'artifact') {
      artifact.value = evt.id || ''
    } else if (t === 'intervention') {
      interventions.value.push(evt)
      if (evt.kind === 'pause') state.value.paused = true
      if (evt.kind === 'resume') state.value.paused = false
    } else if (t === 'intervention_ack') {
      const m = interventions.value.find((x) => x.seq === evt.seq)
      if (m) m.consumed_by = evt.agent
      interventions.value = interventions.value.slice()
    } else if (t === 'run_status') {
      state.value.paused = evt.status === 'paused'
    }
    logEvent(evt)
  }

  // ------------------------------------------------------------- 启动作业
  async function start() {
    if (state.value.running) {
      toast('已有作业在跑，等它结束或先终止', 'warn')
      return
    }
    const text = task.value.trim()
    if (!text) { toast('请先填写任务描述', 'err'); return }
    const chosen = TEAM_ROLE_KEYS.filter((r) => roleOn.value[r])

    // 复位全部状态（旧版是 `TEAM = teamNewState()` 后逐项填）
    state.value = teamNewState()
    state.value.running = true
    state.value.title = title.value.trim() || text.slice(0, 40)
    agents.value = makeAgents()
    plan.value = { items: [] }
    review.value = null
    events.value = []
    interventions.value = []
    stream.value = {}
    artifact.value = ''
    filter.value = 'all'
    logLines.value = []
    runMeta.value = '启动中…'
    setRunStatus('长任务作业运行中…', 'running')

    const body = {
      title: title.value.trim(),
      task: text,
      scope: scope.value.trim(),
      framework: framework.value,
      notes: notes.value.trim(),
      roles: chosen,
      max_rounds: Number(rounds.value) || 0,
    }
    try {
      const doneEvt = await sseConsume('/api/team/stream', body, onEvent)
      if (!doneEvt) throw new Error('连接中断，未收到作业结果（作业可能还在服务端跑，稍后在历史里看）')
      const p = doneEvt.payload || {}
      state.value.running = false
      state.value.paused = false
      artifact.value = p.artifact || ''
      logLine(`作业${RUN_STATUS[p.status] || p.status}：共 ${p.file_count || 0} 个文件、`
        + `${p.item_total || 0} 个工作项`, p.artifact ? 'ok' : 'warn')
      setRunStatus(`作业${RUN_STATUS[p.status] || p.status} · 产出 ${p.file_count || 0} 个文件`,
        p.artifact ? 'ok' : 'error')
      toast(p.artifact ? '作业完成，产出物已生成，去下方审阅后采纳'
        : `作业${RUN_STATUS[p.status] || p.status}，但没有产出任何文件`, p.artifact ? 'ok' : 'warn')
    } catch (e) {
      state.value.running = false
      logLine(`✗ ${String((e && e.message) || e)}`, 'bad')
      setRunStatus('长任务作业失败', 'error')
      toast(`作业失败: ${e}`, 'err')
    } finally {
      state.value.running = false
      state.value.paused = false
      runMeta.value = state.value.runId ? `已结束 · ${state.value.runId}` : ''
      await Promise.all([loadRuns()])
    }
    return { artifact: artifact.value }
  }

  // ------------------------------------------------------------- 人工介入
  async function intervene(kind, opts = {}) {
    if (!state.value.runId) { toast('还没有运行中的作业', 'warn'); return }
    if (!state.value.running && !state.value.replaying) { toast('作业已结束，指令送不进去', 'warn'); return }
    if (!state.value.running) { toast('这是历史记录的回放，指令送不进去', 'warn'); return }
    try {
      const data = await api.teamIntervene(state.value.runId, {
        kind, agent: opts.agent || '*', text: opts.text || '', item: opts.item || '',
      })
      if (kind === 'pause') state.value.paused = true
      if (kind === 'resume') state.value.paused = false
      toast(data.detail || '指令已送达', kind === 'pause' || kind === 'stop' ? 'warn' : 'ok')
    } catch (e) {
      toast(`指令未送达: ${e}`, 'err')
    }
  }

  const interveneAgent = ref('*')
  const interveneText = ref('')

  async function sendMessage() {
    const text = interveneText.value.trim()
    if (!text) { toast('请先输入要传达的内容', 'err'); return }
    await intervene('message', { agent: interveneAgent.value || '*', text })
    interveneText.value = ''
  }

  async function askReplan() {
    const text = interveneText.value.trim()
    await intervene('replan', { agent: 'planner', text })
    if (text) interveneText.value = ''
  }

  // ------------------------------------------------------------- 历史作业
  const runs = ref([])
  const runsError = ref('')

  async function loadRuns() {
    try {
      runs.value = (await api.teamRuns()) || []
      runsError.value = ''
    } catch (e) {
      runsError.value = e && e.message ? e.message : String(e)
    }
  }

  /** 打开历史作业（只读回放）。旧版 teamReplay */
  async function openRun(id) {
    try {
      const run = await api.teamRun(id)
      replay(run)
      toast('已载入这次作业的记录（只读回放）', 'ok')
    } catch (e) {
      toast(`打开失败: ${e}`, 'err')
    }
  }

  function replay(run) {
    const wasLive = state.value.running
    state.value = teamNewState()
    state.value.runId = run.id || ''
    state.value.title = run.title || ''
    state.value.replaying = true
    agents.value = (run.agents || []).map((a) => ({
      ...a, status: a.status || 'idle', files: a.files || [], notes: a.notes || [],
    }))
    plan.value = run.plan || { items: [] }
    review.value = run.review || null
    interventions.value = run.interventions || []
    artifact.value = run.artifact || ''
    events.value = run.events || []
    stream.value = {}
    logLines.value = []
    // 回放时用历史里的 paused 状态恢复按钮语义
    state.value.paused = !!(run.live && run.live.paused) && !!run.running
    state.value.running = !!run.running && !wasLive
    events.value.forEach(logEvent)
    const items = plan.value.items || []
    runMeta.value = `${run.id || ''} · ${RUN_STATUS[run.status] || run.status}`
      + (items.length ? ` · ${items.filter((i) => i.status === 'done').length}/${items.length} 项` : '')
    if (run.running) {
      logLine('⚠ 这次作业其实还在服务端运行，但当前页面没有连着它的实时流：'
        + '进度会在下面的记录里滞后显示，等它结束后再点开一次就能看到完整结果。', 'warn')
      // 回放一个「还在跑」的旧作业：顶栏也标运行中，与旧版一致
      setRunStatus(`长任务作业运行中 · ${run.id || ''}`, 'running')
    }
  }

  // ------------------------------------------------------------- 初始化
  /** 拉角色定义。失败回落到兜底，保证看板永远有卡可画 */
  async function loadRoles() {
    if (roles.value.length) return
    try {
      const d = await api.teamRoles()
      roles.value = ((d && d.roles) || []).filter((r) => r && r.id)
    } catch (e) {
      roles.value = []
    }
    if (!roles.value.length) roles.value = TEAM_ROLE_FALLBACK.slice()
  }

  /** 首次进入页签：拉角色 + 铺看板骨架 + 拉历史。旧版 teamInit */
  async function ensure() {
    await loadRoles()
    if (!agents.value.length && !state.value.replaying) {
      agents.value = makeAgents()
    }
    await loadRuns()
  }

  return {
    // 角色
    roles, roleText: role,
    // 表单
    title, scope, framework, rounds, task, notes, roleOn,
    // 作业状态
    running, paused, replaying, runId, agents, plan, review, events,
    interventions, filter, artifact, stream, runMeta, interveneHint, planSummary,
    // 时间线
    logLines, visibleLogLines, hasLog, filterOptions, setFilter, logLine, logEvent,
    // 动作
    start, intervene, sendMessage, askReplan, interveneAgent, interveneText,
    // 历史
    runs, runsError, loadRuns, openRun, replay, loadRoles,
    // 初始化
    ensure,
  }
}

// ---------------------------------------------------------------- 文案字典
// 旧版 app.js:3152-3166。**必须逐字一致**：这些字符串会进时间线，
// 也会用在历史回放和界面用例的断言里。
export const AGENT_STATUS = {
  idle: '待命', working: '执行中', done: '已完成', error: '出错', waiting: '等待人工',
}
export const RUN_STATUS = {
  planning: '规划中', running: '运行中', paused: '已暂停',
  done: '已完成', failed: '失败', aborted: '已终止',
}
export const ITEM_STATUS = {
  todo: '待做', doing: '进行中', done: '已完成', failed: '失败', skipped: '已跳过',
}
export const VERDICT_TEXT = { pass: '通过', pass_with_risks: '通过（有风险）', block: '需返工' }
export const INTERVENE_TEXT = {
  message: '人工指令', replan: '要求重规划', skip: '跳过工作项',
  pause: '暂停', resume: '继续', stop: '终止',
}
