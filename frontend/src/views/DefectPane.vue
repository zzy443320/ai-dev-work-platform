<script setup>
// 缺陷修复页签（#pane-defect）。旧版 index.html:235-351 的骨架 +
// app.js 里 run/probe/proposals/stats/kb 五段逻辑的等价迁移。
//
// 这个页签有四个面板（顺序与 id 都是既有用例锁定的，见 check_panel_fold.py）：
//   #run-panel      运行流水线（表单 + 日志）
//   #proposal-panel 待审批提案（筛选 + 分页）
//   #stats-panel    分类统计
//   #kb-panel       知识库（模式 / 缺陷卡双层视图）
//
// 与问答页签一样**必须解构 useDefect()**：`<script setup>` 只对顶层绑定做模板
// 自动解包，写 `const d = useDefect()` 再在模板里用 `d.mineOnly` 会拿到 ref 对象。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useDefect, PROP_PAGE_SIZE } from '../composables/useDefect.js'
import { useArtifacts } from '../composables/useArtifacts.js'
import { useFold } from '../composables/useFold.js'
import { useRunLease } from '../composables/useRunLease.js'
import { useRunModal } from '../composables/useRunModal.js'
// 检视器与弹窗共用一份详情状态（见 useProposalModal 顶部注释）。这里只取
// 「选中 / 放大」两个动作，判定逻辑一律留在 composable 里，不在页签内复制一套。
import { useProposalModal } from '../composables/useProposalModal.js'
import { useSettings } from '../composables/useSettings.js'
import { useParamModel } from '../composables/useParamModel.js'
import { api } from '../api/client.js'
import StageRing from '../components/StageRing.vue'
import ParamStrip from '../components/ParamStrip.vue'
import ParamLocked from '../components/ParamLocked.vue'
import RunLog from '../components/RunLog.vue'
import RunLeaseBar from '../components/RunLeaseBar.vue'
import ArtifactsPanel from '../components/ArtifactsPanel.vue'
import ProposalCard from '../components/ProposalCard.vue'
import ProposalPager from '../components/ProposalPager.vue'
import KbCard from '../components/KbCard.vue'

const {
  // 表单
  mineOnly, skipVerify, limit, defectId,
  // 流水线
  running, stageStates, lines, live, hasLive, replay,
  resultHtml, logTitle, start, currentTarget, startedAt,
  // 提案
  proposals, proposalsError, propFilter, pageItems, pageSlice,
  totalPages, curPage, pagerInfo, pagerNums, proposalBadge, openCount,
  loadProposals, setPropFilter, gotoPropPage,
  // 统计
  statsError, statsMeta, statCards, loadStats,
  // 知识库
  kbView, kbHint, kbCount, kbItems, kbError, loadKb, setKbView,
  // 刷新
  ensure,
} = useDefect()

const { isCollapsed, toggleFold } = useFold()
const { openRunModal } = useRunModal()
const { pinnedId, selectInline } = useProposalModal()
// 护盾 chip 读闸门配置的降级态（form 是模块级单例，App 启动时已 loadSettings）。
const { form: settingsForm } = useSettings()

/**
 * 点队列卡片 = 直接开提案弹窗（2026-10-15 改版）。
 *
 * 原先是「点行 → 钉到右侧检视器 → 再点放大才进弹窗」两步，右侧一整列还常是空态。
 * 弹窗里 diff / 采纳 / 拒绝 / 返工 / 后端交接全都有，检视器没有独占能力，
 * 所以直接开弹窗，检视器整列删除，队列那一步高亮照样留着（一眼看出在审哪一份）。
 */
function onPickRow(id) {
  selectInline(id)
  emit('open-proposal', id)
}

// 服务端占用（可能是外部脚本 / 别的标签页 / 刷新前留下的线程）。本地 `running`
// 只知道自己这个标签页发起的运行，两者取并集才是「现在能不能点运行」。
const { busy: leaseBusy, refresh: refreshLease } = useRunLease()
const canStart = computed(() => !running.value && !leaseBusy.value)
// 复现用例面板：修复循环在沙箱里跑红→绿的那份用例会挂成 type=repro 的产出物等人采纳。
// useArtifacts 是单例，本页签内只此一个面板实例（App.vue 的全局面板在 defect 页签是隐藏的，
// 因为 ARTIFACT_TAB_LABEL 不含 defect），所以必须自己带 id-suffix 防止与其它页签串 id。
const { openArtifact, loadArtifacts } = useArtifacts()

/**
 * 运行参数（2026-10-13 参数形态改版）：四条参数、无长文本 → **就地控件条**。
 * 定义来自 /api/params/schema；控件 id 沿用 #opt-mine / #opt-skip-verify /
 * #opt-limit / #opt-defect，读写都落回 useDefect 的那四个 ref。
 */
const params = useParamModel({ mineOnly, skipVerify, limit, defectId })

/** 本批配置定格（理由见 ParamLocked 文件头）：点运行的那一刻抓快照，跑动中只读展示 */
const batch = ref([])
function snapshotBatch() {
  batch.value = [
    { label: '工单范围', value: mineOnly.value ? '只看我负责的' : '全部工单' },
    { label: '最多处理', value: `${limit.value ?? '—'} 个` },
    { label: '页面验收', value: skipVerify.value ? '跳过截图' : '会截图验收' },
    { label: '工单', value: (defectId.value || '').trim() ? '指定 UUID' : '拉最近列表' },
  ]
}

/** 运行 / 试运行都会往主区写日志 */
async function onStart(kind) {
  snapshotBatch()
  await start(kind)
  // 跑完（无论成败）都回读一次占用：被停止时线程还在阶段边界收尾，
  // 界面上那条「仍在运行」必须跟着真实租约走，不能靠本地标记猜。
  await refreshLease()
  // 跑完再拉一次复现用例：新产生的 repro 产出物要立刻出现在面板里，
  // 不能等用户切页签（App.vue 的按需加载不含 defect）
  if (kind === 'run') await loadArtifacts('repro')
}

/**
 * 放大查看当前「运行结果」日志（旧版 `openRunModal()`：把 #run-log 的 innerHTML
 * 搬进 #runmodal-body）。日志区折叠时按钮仍要点得到，所以直接读 DOM 而不是状态。
 */
function openDefectRunModal() {
  const el = document.getElementById('run-log')
  openRunModal('运行结果', el ? el.innerHTML : '')
}

const emit = defineEmits(['open-proposal', 'open-kb-card', 'open-kb-pattern',
  'expand-log', 'open-artifact'])

/** 仓库预检横幅：仓库不干净 / 分支不一致时提示「现在采纳会被拒」 */
const repoBanner = ref('')
/** 知识库视图切换按钮的选项（模板里遍历，避免两处硬编码） */
const KB_VIEWS = [
  { key: 'pattern', label: '模式' },
  { key: 'defect', label: '缺陷卡片' },
]

/** 面板右上角计数。旧版是「N 个模式」/「N 个缺陷卡片」，跟着视图走 */
const cardCount = computed(() =>
  `${kbCount.value} ${kbView.value === 'pattern' ? '个模式' : '个缺陷卡片'}`)

async function refreshAll() {
  await Promise.all([loadProposals(), loadStats(), loadKb(), loadRepoBanner()])
}

async function loadRepoBanner() {
  try {
    const h = await api.health()
    const problems = (h.repo_problems || []).filter(Boolean)
    repoBanner.value = problems.length
      ? `<b>仓库预检未通过：现在点「采纳」会被后端直接拒绝。</b>`
        + `<ul>${problems.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>`
        + '<span class="muted">切到配置的分支之后，再回来采纳；提案本身不会失效。<br>'
        + '工作区有未提交改动不影响采纳。</span>'
      : ''
  } catch (e) {
    repoBanner.value = ''
  }
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

/** 提案列表面板角标样式（有开放项时高亮） */
const badgeAlert = computed(() => openCount.value > 0)

/** 名牌标题：defect 事件写入的当前工单；没有就如实说等待/待运行 */
const missionTitle = computed(() => {
  const t = currentTarget.value
  if (!t) return running.value ? '已启动 · 等待第一张工单…' : '待运行 · 本轮目标会显示在这里'
  return t.title || t.id || '（无标题工单）'
})
const doneCount = computed(() => stageStates.value.filter((s) => s === 'done').length)

/** 名牌计时：running 时每秒走；停了定格在最后值，标签变「上轮耗时」——只报真实经过的时间 */
const elapsed = ref(0)
let tickTimer = 0
const elapsedText = computed(() => {
  const s = Math.floor(elapsed.value / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
})
function stopTick() { if (tickTimer) { window.clearInterval(tickTimer); tickTimer = 0 } }
watch(running, (on) => {
  stopTick()
  if (on) {
    elapsed.value = Date.now() - startedAt.value
    tickTimer = window.setInterval(() => { elapsed.value = Date.now() - startedAt.value }, 1000)
  } else if (startedAt.value) {
    elapsed.value = Date.now() - startedAt.value
  }
}, { immediate: true })
onBeforeUnmount(stopTick)

/** 今日裁决：proposals 全量按状态聚合（pageSlice 只是当前页，不能拿它数） */
const verdict = computed(() => {
  const v = { applied: 0, rejected: 0, pending: 0 }
  for (const p of proposals.value) {
    if (p.status === 'applied') v.applied++
    else if (p.status === 'rejected') v.rejected++
    else if (p.status === 'pending' || p.status === 'gate_failed') v.pending++
  }
  return v
})

onMounted(async () => {
  // 占用先读一次：进页面就该立刻知道「有人在跑」，不等 3 秒轮询的下一拍
  refreshLease()
  await ensure()
  await loadRepoBanner()
  await loadArtifacts('repro')
  // 主舞台底部的分类指标带（stats-panel 通栏化后就是它的数据源），幂等 GET
  loadStats()
})
</script>

<template>
  <!-- 驾驶舱主舞台 v2：名牌行（含运行动作）+ 数字列|环形盘|竖排阶段列表 + 护盾 chips。
       顶部横细轨取消 —— 阶段信息只说一遍，#stage-flow 的 DOM 与状态类原样搬进
       竖排列表（契约靠 desk.css 重排保住）。每个数字都挂真实信号：
       currentTarget 来自 defect 事件，计时来自 startedAt，环来自 stageStates；
       取不到数的卡位（判读图/先例/返工/预算）宁缺毋滥，不放。 -->
  <section class="defect-hero" id="defect-hero">
    <div class="dh-mission">
      <div class="dh-badge" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2l1.9 3.8M16 2l-1.9 3.8M9 7h6a3 3 0 0 1 3 3v1h2v3h-2v2a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3v-2H4v-3h2v-1a3 3 0 0 1 3-3Z" /></svg>
      </div>
      <div class="dh-title-wrap">
        <h3 class="dh-title">{{ missionTitle }}</h3>
        <div class="dh-meta">
          <span v-if="currentTarget" class="tgt mono">本轮 {{ currentTarget.index }}/{{ currentTarget.total }}</span>
          <span class="dh-state" :class="running ? 'is-run' : ''"><span class="dh-dot" aria-hidden="true" />{{ running ? '运行中' : '待命' }}</span>
          <span class="dh-hint2">补丁沙箱真跑验收才成提案 · 采纳才写工作区</span>
        </div>
      </div>
      <div class="dh-timer" v-if="startedAt">
        <div class="big mono">{{ elapsedText }}</div>
        <div class="lab">{{ running ? '本轮耗时' : '上轮耗时' }}</div>
      </div>
      <div class="dh-ops">
        <!-- 运行 / 试运行已下移到参数块底部（填完参数视线下移就是按钮），
             名牌行只留不依赖参数的刷新。 -->
        <el-button id="btn-defect-refresh" @click="refreshAll()"
                   title="重新拉取提案 / 统计 / 知识库列表">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.3" /><polyline points="21 3 21 9 15 9" /></svg>
          刷新
        </el-button>
      </div>
    </div>

    <!-- 驾驶舱主区：左 = 阶段环 / 阶段列表（过程），右 = 启动参数 + 运行按钮（输入）。
         参数与「运行」必须同处一块 —— 之前参数在下方折叠面板里、按钮在页面最顶上，
         填完参数要往上找按钮；控制台也没跟操作在一张卡里，点完运行得往下滚。 -->
    <div class="dh-body">
      <div class="dh-stage">
        <div class="dh-nums">
          <div class="nu"><div class="v mono">{{ doneCount }}<em>/5</em></div><div class="l">阶段完成</div></div>
          <div class="nu"><div class="v mono">{{ openCount }}</div><div class="l">待审提案</div></div>
        </div>
        <StageRing :states="stageStates" :running="running" :size="260" />
        <div class="dh-stagelist"><slot name="stage-flow" /></div>
      </div>

      <div class="dh-params">
        <!-- 参数形态（2026-10-13）：四条参数走**就地控件条**（定义在 /api/params/schema），
             控件 id #opt-mine / #opt-skip-verify / #opt-limit / #opt-defect 一律不动。
             跑动中定格成只读（ParamLocked），值改了对这一批无效，说清楚而不是禁用。 -->
        <ParamLocked v-if="running" class="plock-wide" title="运行参数" :items="batch"
                     note="流水线按启动时的参数推进，跑动中改这些值对这一批无效；本轮结束后表单恢复可编辑。" />
        <ParamStrip v-else name="defect" :model="params" />
        <div class="dh-runops">
          <el-button type="primary" id="btn-run" :disabled="!canStart" @click="onStart('run')"
                     :title="canStart ? '' : '槽位被占用，见左列运行塔台（可点「停止」）'">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3" /></svg>
            运行
          </el-button>
          <el-button id="btn-probe" :disabled="!canStart" @click="onStart('probe')"
                  :title="canStart ? '只拉取 ONES 工单并 AI 定位问题，不生成提案、不进审批、不写任何文件' : '槽位被占用，见左列运行塔台（可点「停止」）'">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
            试运行
          </el-button>
        </div>
        <div class="info-note dh-note" id="dry-warn">「试运行」只拉取 ONES 工单并 AI 定位问题（不生成提案、不进审批、不写文件），用来独立验证连通性和定位效果。<br>「运行」走完整流水线产出提案（补丁 + diff + 闸门结果 + 截图），同样不改动目标仓库，人工采纳才写工作区（不 commit、不 push）。<br>运行过程中可实时看到每个阶段的进展，以及大模型的思考与输出。</div>
      </div>
    </div>

    <!-- 控制台并入驾驶舱：点完运行，日志就在同一张卡里滚（IDE 底部面板那个形态）。
         #run-log 的 420px 限高与空态占位由 style.css 锁着；折叠只收内容不删 DOM，
         所以 :empty::before 那套占位逻辑不受影响。 -->
    <div class="console dh-console" :class="{ 'console-collapsed': isCollapsed('run-console') }">
      <div class="console-bar">
        <button type="button" class="cb-toggle" id="btn-console-toggle"
                :title="isCollapsed('run-console') ? '展开运行日志' : '收起运行日志'"
                @click="toggleFold('run-console')">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
               stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"
               :style="{ transform: isCollapsed('run-console') ? 'rotate(-90deg)' : 'none' }">
            <polyline points="6 9 12 15 18 9" />
          </svg>
          <span>AGENT 直播 · 运行日志</span>
        </button>
        <span class="cb-hint muted">{{ isCollapsed('run-console') ? '已收起 · 流水线仍在推进 · ⌃` 开合' : '实时阶段进展与大模型输出 · ⌃` 收起' }}</span>
      </div>
      <RunLog
        :lines="lines" :live="live" :has-live="hasLive" :replay="replay"
        :result-html="resultHtml" :running="running" :title="logTitle"
        :on-open="openRunModal"
        @open-proposal="(id) => emit('open-proposal', id)"
        @expand="(p) => emit('expand-log', p)"
      />
    </div>

    <!-- 护盾 chip 读真实闸门配置：降级态一眼可见，不用点进配置页确认。
         复现用例 chip 是引导不是状态断言 —— 数据在 repro 面板里，不假装有数。 -->
    <div class="dh-chips">
      <span class="dh-chip" :class="settingsForm.gate.degraded ? 'is-warn' : 'is-ok'">
        护盾 · {{ settingsForm.gate.degraded ? '降级为语法检查' : '闸门真跑' }}
      </span>
      <span class="dh-chip">复现用例 · 沙箱先跑红再修绿，挂在本页下方待你采纳</span>
    </div>
  </section>

  <section class="panel collapsible" id="proposal-panel"
           :class="{ collapsed: isCollapsed('proposal-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>
        待审批提案
        <span class="count-badge" :class="{ alert: badgeAlert, hidden: !proposals.length }"
              id="proposal-count">{{ proposalBadge }}</span>
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('proposal-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <label class="filter-wrap">
        <span class="muted">筛选</span>
        <el-select id="proposal-filter" :model-value="propFilter"
                   @update:model-value="setPropFilter" placeholder="全部">
          <el-option value="" label="全部" data-value="" />
          <el-option value="pending" label="待审批" data-value="pending" />
          <el-option value="applied" label="已采纳" data-value="applied" />
          <el-option value="rejected" label="已拒绝" data-value="rejected" />
          <el-option value="invalid" label="无法生成补丁" data-value="invalid" />
        </el-select>
      </label>
    </div>
    <!-- 占用条（v2 移到塔台顶部，常驻模式）：槽位被别的来源（外部脚本 / 另一个
         标签页）占着时这里是「是谁、到哪了、能不能停」；空闲时显示待命卡，
         塔台顶部不再忽有忽无。 -->
    <RunLeaseBar always />
    <div class="block-banner" :class="{ hidden: !repoBanner }" id="repo-banner"
         v-html="repoBanner" />
    <!-- 队列（master）：卡片排成一列密行而不是一屏三张的网格 ——
         审提案的动作是「逐份过」，网格逼着人来回找下一张。
         ⚠️ 卡片必须留在 #proposals 里（用例数 #proposals .pcard 的条数与 .pcard-id 顺序）。 -->
    <div id="proposals" class="prop-queue">
      <div v-if="proposalsError" class="empty">提案加载失败: {{ proposalsError }}</div>
      <div v-else-if="!proposals.length" class="empty">还没有提案。运行流水线后会在这里等你审阅采纳。</div>
      <div v-else-if="!pageItems.length" class="empty">当前筛选下没有提案。</div>
      <template v-else>
        <ProposalCard v-for="p in pageSlice" :key="p.id" :p="p"
                      :active="pinnedId === p.id"
                      @open="onPickRow" />
        <ProposalPager :info="pagerInfo" :nums="pagerNums" :current="curPage"
                       :total-pages="totalPages" :page-size="PROP_PAGE_SIZE"
                       @goto="gotoPropPage" />
      </template>
    </div>
    <p class="panel-note">运行流水线不会写目标仓库，只产出提案；在这里点「采纳」只会把改动写入工作区（不 commit、不 push），由你确认后自行提交。</p>
  </section>

  <!-- 今日裁决常驻摘要：没有待审时审判台也不空（数字来自 proposals 全量聚合）。 -->
  <div class="verdict" id="today-verdict">
    <div class="vt">今日裁决 · {{ verdict.applied + verdict.rejected + verdict.pending }} 份已处理</div>
    <div class="vrow">
      <span class="g"><b class="mono">{{ verdict.applied }}</b>采纳</span>
      <span class="r"><b class="mono">{{ verdict.rejected }}</b>拒绝</span>
      <span class="w"><b class="mono">{{ verdict.pending }}</b>待审</span>
    </div>
    <p class="vlast">「采纳」只写入工作区（不 commit、不 push），由你确认后自行提交。</p>
  </div>

  <!-- 复现用例：agentic 修复循环在沙箱里跑红→绿的那份用例（type=repro 的产出物）。
       提案补丁不支持新建文件，所以它单独挂在这里，点采纳才会进仓库。
       面板自己就是 <section class="panel">，不要再套一层（同 id 结构会串，
       且 check_panel_fold 量的是各面板的 top）。 -->
  <ArtifactsPanel label="复现用例" :visible="true" id-suffix="repro"
                  empty-hint="还没有复现用例。运行流水线后，模型若写出了能先把缺陷跑红的用例，会挂在这里等你采纳。"
                  @open="openArtifact" />

  <section class="panel collapsible" id="stats-panel"
           :class="{ collapsed: isCollapsed('stats-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3v18h18" /><rect x="7" y="10" width="3" height="8" /><rect x="12" y="6" width="3" height="12" /><rect x="17" y="13" width="3" height="5" /></svg>
        分类统计
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('stats-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <span class="muted" id="stats-count">{{ statsMeta }}</span>
    </div>
    <div id="stats" class="stats-grid">
      <div v-if="statsError" class="empty">加载失败: {{ statsError }}</div>
      <div v-else-if="!statCards.length" class="empty">暂无数据</div>
      <div v-for="c in statCards" :key="c.cat" class="stat-card">
        <div class="stat-cat">{{ c.cat }}</div>
        <div class="stat-total">{{ c.total }}</div>
        <div class="stat-bar"><i :style="{ width: c.rate + '%' }" /></div>
        <div class="stat-fixed">已采纳 {{ c.applied }} / 待审 {{ c.pending }} / 已拒绝 {{ c.rejected }}<span class="stat-rate">采纳率 {{ c.rate }}%</span></div>
      </div>
    </div>
  </section>

  <section class="panel collapsible" id="kb-panel"
           :class="{ collapsed: isCollapsed('kb-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4a2 2 0 0 0-2-2H6.5A2.5 2.5 0 0 0 4 4.5v15Z" /><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" /></svg>
        知识库
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('kb-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <span class="muted" id="card-count">{{ cardCount }}</span>
    </div>
    <div class="kb-views">
      <div class="seg" id="kb-views">
        <button v-for="v in KB_VIEWS" :key="v.key" class="seg-btn"
                :class="{ active: kbView === v.key }" :data-kbview="v.key"
                @click="setKbView(v.key)">{{ v.label }}</button>
      </div>
      <span class="muted" id="kb-hint">{{ kbHint }}</span>
    </div>
    <div id="cards" class="cards-grid">
      <div v-if="kbError" class="empty">加载失败: {{ kbError }}</div>
      <div v-else-if="!kbItems.length" class="empty">暂无知识卡片</div>
      <KbCard v-for="item in kbItems" :key="item.id" :item="item" :view="kbView"
              @open-card="(c) => emit('open-kb-card', c)"
              @open-pattern="(id) => emit('open-kb-pattern', id)"
              @open-proposal="(id) => emit('open-proposal', id)" />
    </div>
  </section>

  <!-- ── 右侧参数抽屉：一次性输入 + 三个动作 ──
       「刷新」也留在这里（和运行/试运行同组）：它刷的是提案/统计/知识库三份列表，
       用的时机就是「准备跑之前先看看现状」。面板头不放按钮是为了不改动
       .panel-head 那套 h2 + fold + 筛选的既有排布（提案面板就长那样）。 -->
</template>
