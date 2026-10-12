// 缺陷修复页签的状态与副作用。旧版 app.js:1366-1822（run/probe）+ 67-345
// （proposals）+ 699-879（stats / 知识库）的等价迁移。
//
// 这个页签有四块彼此独立的数据（流水线日志 / 提案 / 分类统计 / 知识库），
// 旧版是四个全局函数各拉各的、跑完流水线再一起刷新。这里保留同样的边界：
// 每块一个 loader，`refreshAfterRun()` 负责「跑完该刷哪几块」。
import { computed, ref, watch } from 'vue'
import { api } from '../api/client.js'
import { sseConsume } from './useSse.js'
import { useToast } from './useToast.js'
import { useRunLease } from './useRunLease.js'
import { setRunStatus } from './useRunStatus.js'
import { STATUS_ORDER, isOpenStatus } from '../utils/labels.js'

// 步骤条的状态推导是纯函数，住在 utils/stages.js（可在 Node 里直调断言）。
// 这里既 import 进来自己用，又再 export 一次：既有 import 路径与界面用例的取法都不变。
// （写成 `export {…} from` 不会产生本地绑定，本模块内部就调不到那几个函数了。）
import {
  stageAdvance, stagesFromLease, stagesFromProbeResults, stagesFromRunResults,
  stagesIdle, stagesRunning,
} from '../utils/stages.js'
export {
  STAGE_STATES, LIVE_STAGE_IDX, stagesIdle, stagesRunning, stagesFromLease,
  stagesFromRunResults, stagesFromProbeResults,
} from '../utils/stages.js'

/** 待审批提案每页条数（2 列 × 4 行）。旧版 app.js:156 */
export const PROP_PAGE_SIZE = 8

/** 知识库视图的 localStorage 键（与旧版同源） */
const KB_VIEW_KEY = 'kb-view'

/** 分页页码序列（超过 9 页时中间折叠成 …）。旧版 app.js:171 */
export function pageNumbers(current, total) {
  if (total <= 9) return Array.from({ length: total }, (_, i) => i + 1)
  const out = [1]
  const s = Math.max(2, current - 1)
  const e = Math.min(total - 1, current + 1)
  if (s > 2) out.push('…')
  for (let i = s; i <= e; i++) out.push(i)
  if (e < total - 1) out.push('…')
  out.push(total)
  return out
}

function hms() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function _createDefectState() {
  const { toast } = useToast()

  // ---- 表单 ----
  const mineOnly = ref(true)
  const skipVerify = ref(false)
  const limit = ref(4)
  const defectId = ref('')

  // ---- 流水线 ----
  const running = ref(false)
  /** 运行方式是 'run' 还是 'probe'，决定日志标题 */
  const runKind = ref('run')
  const stageStates = ref(stagesIdle())
  /** 日志行：{t, text, kind}，kind ∈ dim/ok/warn/bad/head */
  const lines = ref([])
  /** 大模型实时输出：{think, out}，运行中用它渲染，结束后回放 */
  const live = ref({ think: '', out: '' })
  const hasLive = ref(false)
  /** 结束后回放的按工单分组缓冲 */
  const replay = ref([])
  /** 结构化结果 HTML（旧版 renderRunResults / renderProbeResults 的产物） */
  const resultHtml = ref('')

  const logTitle = computed(() => (runKind.value === 'probe' ? '试运行结果' : '运行结果'))
  const hasLog = computed(() => !!resultHtml.value || lines.value.length > 0 || replay.value.length > 0)

  function resetLog(kind) {
    runKind.value = kind
    lines.value = []
    live.value = { think: '', out: '' }
    hasLive.value = false
    replay.value = []
    resultHtml.value = ''
  }

  function pushLine(text, kind = 'dim') {
    lines.value.push({ t: hms(), text: String(text ?? ''), kind })
  }

  function resetLive() {
    live.value = { think: '', out: '' }
  }

  /**
   * 直接把一份结果挂上去（不走 SSE）。
   * 用途只有两个：自检用例灌数据；将来「历史运行记录」回看时复用。
   * 业务路径永远走 `start()`。
   */
  function setResult(data) {
    resetLog('run')
    resultHtml.value = renderRunResults(data)
    stageStates.value = stagesFromRunResults(data)
    if (data && data.error) setRunStatus('失败', 'error')
    else setRunStatus(`产出 ${(data && data.count) || 0} 个提案`, 'ok')
  }

  /** 清空结果，退回 #run-log 的空态（:empty 占位提示才会出现） */
  function clearResult() {
    resetLog('run')
    stageStates.value = stagesIdle()
  }

  function liveAI(kind, text) {
    if (!text) return
    hasLive.value = true
    if (kind === 'reasoning') live.value.think += text
    else live.value.out += text
  }

  /** 步骤条推进。旧版 app.js:1502 */
  function liveStage(evt) {
    // 推导规则本身在 utils/stages.js 里（有 Node 直调断言），这里只负责写回状态
    stageStates.value = stageAdvance(stageStates.value, evt.stage, evt.status)
  }

  /**
   * 跑一次流式流水线。
   * @param {'run'|'probe'} kind
   */
  async function start(kind) {
    if (running.value) return
    resetLog(kind)
    running.value = true
    setRunStatus(kind === 'probe' ? '试运行中…' : '运行中…', 'running')
    stageStates.value = stagesRunning()

    const buffers = {} // defectId -> {think, out}，结束后作为回放渲染
    let currentDefect = '（等待工单）'
    let fatal = ''

    const onEvent = (evt) => {
      if (!evt) return
      if (evt.type === 'stage') {
        pushLine(evt.detail || '', evt.status === 'start' ? 'dim'
          : evt.status === 'fail' ? 'bad' : evt.status === 'warn' ? 'warn' : 'ok')
        liveStage(evt)
      } else if (evt.type === 'defect') {
        currentDefect = evt.id || `#${evt.index}`
        pushLine(`▶ 工单 ${currentDefect}（${evt.index}/${evt.total}）：${evt.title}`, 'head')
        resetLive()
        stageStates.value = kind === 'probe'
          ? ['done', 'active', 'off', 'off', 'off']
          : ['done', 'active', 'idle', 'idle', 'idle']
      } else if (evt.type === 'ai_delta') {
        const buf = buffers[currentDefect] || (buffers[currentDefect] = { think: '', out: '' })
        if (evt.kind === 'reasoning') buf.think += evt.text || ''
        else buf.out += evt.text || ''
        liveAI(evt.kind, evt.text || '')
      } else if (evt.type === 'fatal') {
        // 旧版是在 onEvent 里直接 throw，由外面的 catch 兜住；
        // 这里记下来，读完流之后再抛（避免在事件回调里抛断掉 reader）。
        fatal = evt.error || '流水线异常'
      }
    }

    const body = kind === 'probe'
      ? { limit: Number(limit.value) || 1, defect_id: defectId.value.trim() || null, mine_only: mineOnly.value }
      : {
          skip_verify: skipVerify.value, limit: Number(limit.value) || 1,
          defect_id: defectId.value.trim() || null, mine_only: mineOnly.value,
        }
    const url = kind === 'probe' ? '/api/probe/stream' : '/api/run/stream'

    let err = ''
    try {
      const done = await sseConsume(url, body, onEvent)
      if (fatal) throw new Error(fatal)
      if (!done) throw new Error(kind === 'probe' ? '连接中断，未收到完整结果' : '连接中断，未收到完整结果')
      const data = (done.payload) || {}
      if (data.error) throw new Error(data.error)
      replay.value = Object.entries(buffers)
        .filter(([, v]) => v.think || v.out)
        .map(([id, v]) => ({ id, ...v }))
      if (kind === 'probe') {
        resultHtml.value = renderProbeResults(data)
        stageStates.value = stagesFromProbeResults(data)
        if (data.source === 'error') {
          setRunStatus('ONES 拉取失败', 'error')
          toast('ONES 拉取失败，详见运行日志', 'err')
        } else if (data.count === 0) {
          setRunStatus('接口通了，但没拉到工单（见提示）', 'error')
          toast('ONES 接口正常但结果为空，详见运行日志的排查提示', 'warn')
        } else {
          setRunStatus(`试运行成功 · 拉到 ${data.count} 条真实工单`, 'ok')
        }
      } else {
        resultHtml.value = renderRunResults(data)
        setRunStatus(`产出 ${data.count} 个提案 · AI:${data.ai_mode}`, 'ok')
        stageStates.value = stagesFromRunResults(data)
      }
    } catch (e) {
      err = String((e && e.message) || e)
      setRunStatus(kind === 'probe' ? '试运行失败' : '失败', 'error')
      pushLine(`✗ ${err}`, 'bad')
      stageStates.value = kind === 'probe'
        ? ['fail', 'fail', 'off', 'off', 'off']
        : ['fail', 'off', 'off', 'off', 'off']
    } finally {
      running.value = false
    }

    // 跑完（无论成败）都刷新提案 / 统计 / 知识库 —— 旧版只在成功路径里刷，
    // 但失败时可能有部分工单已经落了卡，刷一下更准。
    await Promise.all([loadProposals(), loadStats(), loadKb()])
    return { ok: !err, error: err }
  }

  // ── 别人发起的运行也要让步骤条动起来 ───────────────────────────────
  // stageStates 原本只由**本标签页**的 start() 推进，所以外部脚本或另一个标签页
  // 跑批量时，占用条显示得明明白白，步骤条却整排静止——看起来像"没在干活"。
  // 租约每 3 秒读一次，够把呼吸灯与「第几步」续上；但本标签页自己在跑时**绝不抢**：
  // SSE 的事件流比快照细得多（每步 start/done 都有），用快照去覆盖它是降级。
  const { run: leaseRun } = useRunLease()
  let leaseDriving = false
  watch(leaseRun, (lease) => {
    if (running.value) { leaseDriving = false; return }
    const next = stagesFromLease(lease)
    if (next) {
      stageStates.value = next
      leaseDriving = true
      return
    }
    if (!leaseDriving) return
    // 外部运行结束：我们手里没有它的结果数据，退回空态而不是假装「五步全绿」。
    leaseDriving = false
    stageStates.value = stagesIdle()
    // 顺手把三份列表刷一次——外部运行刚落地的提案不该等人手动点「刷新」
    Promise.all([loadProposals(), loadStats(), loadKb()])
  })

  // ---------------------------------------------------------------- 提案
  const proposals = ref([])
  const proposalsError = ref('')
  const propFilter = ref('')
  const propPage = ref(1)

  const openCount = computed(() =>
    proposals.value.filter((p) => isOpenStatus(p.status) || p.status === 'apply_failed').length
  )
  /** 面板角标文案。旧版 app.js:116 `0 待审 / 3 条` */
  const proposalBadge = computed(() =>
    proposals.value.length ? `${openCount.value} 待审 / ${proposals.value.length} 条` : ''
  )

  /** 按当前筛选 + 时间倒序排好、切好页的提案。旧版 app.js:121 */
  const pageItems = computed(() => {
    const f = propFilter.value
    let list = proposals.value.slice()
    if (f === 'pending') list = list.filter((p) => isOpenStatus(p.status) || p.status === 'apply_failed')
    else if (f) list = list.filter((p) => p.status === f)
    // 排序：一律按时间倒序（更新时间优先、创建时间兜底）。
    // 曾经先按状态分组再按时间 —— 8 分钟前闸门未过的提案会被压到两天前的
    // pending 后面，「待审批」列表的时间看起来忽新忽旧。
    list.sort((a, b) => {
      const d = String(b.updated || b.created || '').localeCompare(String(a.updated || a.created || ''))
      if (d) return d
      return (STATUS_ORDER.indexOf(a.status) + 1 || 99) - (STATUS_ORDER.indexOf(b.status) + 1 || 99)
    })
    return list
  })

  const totalPages = computed(() => Math.max(1, Math.ceil(pageItems.value.length / PROP_PAGE_SIZE)))
  const curPage = computed(() => Math.min(Math.max(1, propPage.value), totalPages.value))
  const pageSlice = computed(() => {
    const start = (curPage.value - 1) * PROP_PAGE_SIZE
    return pageItems.value.slice(start, start + PROP_PAGE_SIZE)
  })
  const pagerInfo = computed(() => {
    const total = pageItems.value.length
    const start = (curPage.value - 1) * PROP_PAGE_SIZE
    return { total, from: start + 1, to: Math.min(total, start + PROP_PAGE_SIZE) }
  })
  const pagerNums = computed(() => pageNumbers(curPage.value, totalPages.value))

  async function loadProposals() {
    try {
      proposals.value = (await api.proposals()) || []
      proposalsError.value = ''
      if (propPage.value > totalPages.value) propPage.value = totalPages.value
    } catch (e) {
      proposalsError.value = e && e.message ? e.message : String(e)
    }
  }

  function setPropFilter(v) {
    propFilter.value = v
    propPage.value = 1 // 切换筛选回到第一页
  }

  function gotoPropPage(n) {
    propPage.value = Math.max(1, Math.min(n, totalPages.value))
  }

  /**
   * 灌 n 条同状态假提案（自检用）。
   * 对应旧版 `proposalsCache = [...]; renderProposals()` —— 注入后排序、分页、
   * 筛选都要走真实逻辑，否则测不出回归。updated 的秒数取 i，时间倒序即第 n 条最前。
   */
  function seedProposals(n) {
    proposals.value = Array.from({ length: n }, (_, i) => {
      const k = i + 1
      const ss = String(k % 60).padStart(2, '0')
      return {
        id: `X${k}`, defect_id: `DEF-${k}`, title: `测试提案 ${k}`,
        status: 'pending', gate_level: 'degraded', gate_ok: true,
        category: '逻辑', priority: `P${k % 3}`, ai_mode: 'openai',
        files: ['a.vue'], added: 1, removed: 0,
        updated: `2026-09-22T10:00:${ss}`, created: '2026-09-22T10:00:00',
        root_cause: `第 ${k} 条测试根因`,
      }
    })
    propPage.value = 1
  }

  // ---------------------------------------------------------------- 分类统计
  const stats = ref(null)
  const statsError = ref('')

  const statCats = computed(() => Object.entries((stats.value && stats.value.categories) || {}))

  /** 面板头部的口径文字。旧版 app.js:709 */
  const statsMeta = computed(() => {
    const s = stats.value
    if (!s) return ''
    const cats = Object.values(s.categories || {})
    const tot = cats.reduce((a, x) => a + (x.total || 0), 0)
    const applied = cats.reduce((a, x) => a + (x.applied || 0), 0)
    const cards = s.cards == null ? 0 : s.cards
    return `${cards} 张卡片 · 已采纳 ${applied}/${tot}`
      + (s.generated_at ? ` · 更新于 ${String(s.generated_at).slice(0, 16).replace('T', ' ')}` : '')
  })

  /** 每张分类卡的派生值（比率在模板里算会不好读） */
  const statCards = computed(() =>
    statCats.value.map(([cat, s]) => {
      const total = s.total || 0
      return {
        cat,
        total,
        rate: total ? Math.round(((s.applied || 0) / total) * 100) : 0,
        applied: s.applied ?? 0,
        pending: s.pending ?? 0,
        rejected: s.rejected ?? 0,
      }
    })
  )

  async function loadStats() {
    try {
      stats.value = await api.stats()
      statsError.value = ''
    } catch (e) {
      stats.value = null
      statsError.value = e && e.message ? e.message : String(e)
    }
  }

  // ---------------------------------------------------------------- 知识库
  const kbView = ref(localStorage.getItem(KB_VIEW_KEY) === 'defect' ? 'defect' : 'pattern')
  /** 后端还没重启时的降级说明（存在状态里而不是直接写字，避免并发把提示冲掉） */
  const kbFallback = ref('')
  const kbItems = ref([])
  const kbError = ref('')

  const kbHint = computed(() => kbFallback.value || (kbView.value === 'pattern'
    ? '同类缺陷的共性沉淀：复发次数越高，越说明该处缺护栏'
    : '一个工单一张卡：现象、根因、补丁与验证结果'))

  const kbCount = computed(() => {
    if (kbError.value) return '0'
    if (kbView.value === 'pattern') {
      const recurring = kbItems.value.filter((p) => (p.recurrence || 0) >= 2).length
      return `${kbItems.value.length} 个模式` + (recurring ? ` · ${recurring} 个已复发` : '')
    }
    return `${kbItems.value.length} 张卡片`
  })

  async function loadKb() {
    if (kbView.value === 'defect') return loadCards()
    return loadPatterns()
  }

  async function loadPatterns() {
    kbError.value = ''
    try {
      const pats = await api.patterns()
      if (!Array.isArray(pats)) throw new Error((pats && pats.error) || '返回异常')
      kbItems.value = pats
    } catch (e) {
      return kbFallbackTo(e)
    }
  }

  async function loadCards() {
    kbError.value = ''
    try {
      const cards = await api.cards()
      kbItems.value = Array.isArray(cards) ? cards : []
    } catch (e) {
      kbItems.value = []
      kbError.value = e && e.message ? e.message : String(e)
    }
  }

  /** 后端还没重启（没有 /api/patterns）时，知识库面板不该看起来是坏的：
   *  记忆体内回退到缺陷卡片视图并说明原因。**不写 localStorage**——
   *  重启后自动恢复成模式视图。旧版 app.js:815 */
  function kbFallbackTo(reason) {
    kbView.value = 'defect'
    kbFallback.value = `模式视图需重启后端（python -m web.server）后可用，当前为缺陷卡片视图（${reason}）`
    return loadCards()
  }

  function setKbView(v) {
    kbView.value = v === 'defect' ? 'defect' : 'pattern'
    kbFallback.value = ''
    try { localStorage.setItem(KB_VIEW_KEY, kbView.value) } catch (e) { /* 隐私模式 */ }
    return loadKb()
  }

  /** 首次进入页签时的加载。四个 loader 并发，谁也不挡谁 */
  async function ensure() {
    await Promise.all([loadProposals(), loadStats(), loadKb()])
  }

  return {
    // 表单
    mineOnly, skipVerify, limit, defectId,
    // 流水线
    running, runKind, stageStates, lines, live, hasLive, replay,
    resultHtml, logTitle, hasLog, start, setResult, clearResult,
    // 提案
    proposals, proposalsError, propFilter, propPage, pageItems, pageSlice,
    totalPages, curPage, pagerInfo, pagerNums, proposalBadge, openCount,
    loadProposals, setPropFilter, gotoPropPage, seedProposals,
    // 统计
    stats, statsError, statsMeta, statCards, loadStats,
    // 知识库
    kbView, kbHint, kbCount, kbItems, kbError, loadKb, setKbView,
    // 刷新
    ensure,
  }
}

// 模块级单例：DefectPane 与 ProposalModal（提案详情弹窗）必须共享同一套状态。
// 教训同 useArtifacts：工厂式 ref 会让每个调用方各拿一套，弹窗 decide 后
// reload 刷的是 B 套状态，页面上渲染的 A 套纹丝不动。
const sharedDefect = _createDefectState()

export function useDefect() {
  return sharedDefect
}

// ------------------------------------------------------------ 结果 HTML 渲染
// 这两段旧版返回的是 HTML 串（一堆转义拼接）。这里保持产出 HTML 串，
// 由组件用 v-html 挂上去 —— 理由和 ChatMessage 一样：结果是「一段带结构的
// 报告」，拆成 Vue 模板会把已有的样式钩子（.run-row / .run-iss）改掉。

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function badge(s) {
  const st = (s === 'proposed' || !s) ? 'pending' : s
  return `<span class="st st-${esc(st)}">${esc(statusTextOf(s))}</span>`
}

function statusTextOf(s) {
  const st = s || ''
  if (st === 'proposed') return '待审批'
  return ({ pending: '待审批', gate_failed: '闸门未通过', invalid: '无法生成补丁',
    applied: '已采纳', apply_failed: '采纳失败', rejected: '已拒绝' })[st] || st
}

function gbadge(level, ok) {
  const lv = level || 'none'
  const failed = ok === false ? ' · 未通过' : ''
  const text = ({ explicit: '显式命令', package_json: 'package.json 探测',
    degraded: '降级语法检查', sandbox: '沙箱真跑', none: '未校验' })[lv] || lv
  return `<span class="gate-badge gate-${esc(lv)}">闸门 ${esc(text)}${esc(failed)}</span>`
}

/** 修复循环结论胶囊：运行结果里就能看出「沙箱真跑绿」还是「一次成型的猜测」 */
const AGENT_BADGE = {
  verified: ['good', '沙箱已验证'],
  checks_pass: ['good', '验收全绿'],
  unverified: ['mid', '未经验证'],
  agent_disabled: ['mid', '一次成型'],
  not_converged: ['bad', '未收敛'],
  budget_exhausted: ['bad', '预算用尽'],
  no_patch: ['bad', '无补丁'],
  sandbox_unavailable: ['bad', '沙箱不可用'],
  error: ['bad', '循环异常'],
}

function abadge(x) {
  const hit = AGENT_BADGE[x.agent_conclusion]
  if (!hit) return ''
  const detail = ` · ${x.agent_rounds || 0} 轮 / ${x.agent_attempts || 0} 次补丁尝试`
  return `<span class="tag agent-${hit[0]}">修复 ${esc(hit[1])}${esc(detail)}</span>`
}

/** 旧版 app.js:1645 `renderRunResults` */
export function renderRunResults(data) {
  const results = (data && data.results) || []
  const head = `<div class="log-title">本次产出 ${results.length} 个提案。`
    + `${data.wrote_any_files === false ? '未写入目标仓库任何文件（wrote_any_files=false）。' : ''}`
    + ' 请在「待审批提案」里逐条审 diff 后再采纳。</div>'
  const rows = results.map((x) => {
    const files = (x.files || []).map((f) => `<code>${esc(f)}</code>`).join(' ')
      || '<i>无文件改动</i>'
    const errs = (x.errors || []).map((e) => `<li>${esc(e)}</li>`).join('')
    const warns = (x.warnings || []).map((w) => `<li>${esc(w)}</li>`).join('')
    return '<div class="run-row">'
      + `<div class="run-row-head"><b>${esc(x.id || '(无工单 ID)')}</b>`
      + `${badge(x.status)}${gbadge(x.gate_level, x.gate_ok)}${abadge(x)}`
      + `<span class="muted">${esc(x.category || '—')}</span>`
      + (x.proposal_id ? `<a class="link" data-proposal="${esc(x.proposal_id)}">查看提案</a>` : '')
      + '</div>'
      + `<div class="run-files">${files}</div>`
      + (x.agent_needs_human
        ? '<div class="run-warn">修复循环没能给出可信证据（见「查看提案 → 修复轨迹」里每一轮的真实报错）。'
          + '这条提案的补丁请当作「AI 的下一版尝试」来审，不要当成已验证的修复。</div>'
        : '')
      + (x.status === 'invalid'
        ? (looksNonFrontendLoose(x)
          ? '<div class="run-warn">模型判断根因不在前端仓库（见下方根因与证据链），未生成前端补丁——建议转后端/接口排查，或在工单补充后端日志后重跑。</div>'
          : '<div class="run-bad">未生成可应用补丁：模型没给出可用 SEARCH/REPLACE 块，这条提案没有 diff 可采纳，只会沉淀分析卡片。</div>')
        : '')
      + (errs ? `<ul class="run-iss bad">${errs}</ul>` : '')
      + (warns ? `<ul class="run-iss warn">${warns}</ul>` : '')
      + (x.verify_status ? `<div class="muted">页面验证：${esc(x.verify_status)}</div>` : '')
      + (x.root_cause ? `<div class="run-root">${esc(x.root_cause)}</div>` : '')
      + '</div>'
  }).join('')
  return `<div class="log-html">${head}${rows || '<div class="muted">没有产出任何提案。</div>'}</div>`
}

/** 结果行里的「非前端」判定：结果对象本身就是 analysis 的扁平化版本 */
function looksNonFrontendLoose(x) {
  const text = [x.root_cause, x.explanation].join(' ')
  if (x.block_count) return false
  if (typeof x.non_frontend === 'boolean') return x.non_frontend
  if (!text.trim()) return false
  const strong = ['非前端缺陷', '不修改任何前端文件', '不需要修改前端', '前端无需修改',
    '根因在后端', '问题在后端', '后端缺陷', '需改后端', '后端修复', '不属于前端', '超出前端范围']
  const weak = ['后端', '服务端', '接口层', '数据库', '未命中']
  let score = 0
  strong.forEach((s) => { if (text.indexOf(s) >= 0) score += 2 })
  weak.forEach((s) => { if (text.indexOf(s) >= 0) score += 1 })
  if (/\/api\//i.test(text) && text.indexOf('接口') >= 0) score += 1
  return score >= 3
}

/** 旧版 app.js:1776 `renderProbeResults` */
export function renderProbeResults(data) {
  const src = (data && data.source) || 'ones'
  const empty = !((data && data.results) || []).length
  const srcBadge = src === 'ones'
    ? (empty
      ? '<span class="ais-badge mock">接口正常 · 结果为空</span>'
      : '<span class="ais-badge live">真实 ONES 工单</span>')
    : '<span class="ais-badge mock">拉取失败</span>'
  const head = `<div class="log-title">试运行结果：只做了「拉取工单 + AI 定位」两步，`
    + `没有生成提案、没有进审批、没有写任何文件。${srcBadge}</div>`
    + (data.fetch_error
      ? `<ul class="run-iss bad"><li>ONES 拉取失败：${esc(data.fetch_error)}</li>`
        + '<li>请检查侧边栏的 ONES 地址 / Project UUID / Access Token（JWT 过期后需重新复制）。</li></ul>'
      : '')
    + (data.notes || []).map((n) => `<div class="probe-note">${esc(n)}</div>`).join('')
    + ((data.hints || []).length
      ? `<ul class="run-iss warn">${(data.hints || []).map((h) => `<li>${esc(h)}</li>`).join('')}</ul>`
      : '')
  const rows = ((data && data.results) || []).map((x) => {
    const d = x.defect || {}
    const a = x.analysis || null
    const files = ((a && a.suspect_files) || []).map((f) => `<code>${esc(f)}</code>`).join(' ')
    const kw = ((a && a.keywords) || []).map((k) => `<span class="probe-kw">${esc(k)}</span>`).join('')
    const desc = String(d.description || '').replace(/<[^>]+>/g, ' ').slice(0, 300)
    const meta = [
      d.number ? `#${d.number}` : '',
      d.issue_type || '',
      (d.assignee || {}).name ? `负责人: ${d.assignee.name}` : '',
    ].filter(Boolean).map((m) => `<span class="muted">${esc(m)}</span>`).join(' ')
    return '<div class="run-row">'
      + `<div class="run-row-head"><b>${esc(d.id || '(无工单 ID)')}</b>`
      + meta
      + `<span class="muted">${esc(d.status || '')}</span>`
      + (a ? `<span class="muted">${esc(a.category || '—')}</span>` : '')
      + '</div>'
      + `<div class="probe-title">${esc(d.title || '')}</div>`
      + (desc ? `<div class="probe-desc">${esc(desc)}</div>` : '')
      + (a && a.root_cause ? `<div class="run-root">${esc(a.root_cause)}</div>` : '')
      + (a && a.explanation ? `<div class="probe-desc">${esc(String(a.explanation).slice(0, 400))}</div>` : '')
      + (files ? `<div class="run-files">${files}</div>` : '')
      + (kw ? `<div class="probe-kws">${kw}</div>` : '')
      + (a && a.ai_error ? `<ul class="run-iss warn"><li>AI 提示：${esc(a.ai_error)}</li></ul>` : '')
      + (x.analysis_error ? `<ul class="run-iss bad"><li>定位失败：${esc(x.analysis_error)}</li></ul>` : '')
      + '</div>'
  }).join('')
  return `<div class="log-html">${head}${rows || '<div class="muted">没有拉到任何工单。</div>'}</div>`
}
