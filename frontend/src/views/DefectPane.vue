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
import { computed, onMounted, ref } from 'vue'
import { useDefect, PROP_PAGE_SIZE } from '../composables/useDefect.js'
import { useArtifacts } from '../composables/useArtifacts.js'
import { useFold } from '../composables/useFold.js'
import { useRunModal } from '../composables/useRunModal.js'
import { useModuleParams } from '../composables/useModuleParams.js'
import { api } from '../api/client.js'
import ParamBar from '../components/ParamBar.vue'
import ParamDrawer from '../components/ParamDrawer.vue'
import RunLog from '../components/RunLog.vue'
import ArtifactsPanel from '../components/ArtifactsPanel.vue'
import ProposalCard from '../components/ProposalCard.vue'
import ProposalPager from '../components/ProposalPager.vue'
import KbCard from '../components/KbCard.vue'

const {
  // 表单
  mineOnly, skipVerify, limit, defectId,
  // 流水线
  running, stageStates, lines, live, hasLive, replay,
  resultHtml, logTitle, start,
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
const { close: closeParams } = useModuleParams()
// 复现用例面板：修复循环在沙箱里跑红→绿的那份用例会挂成 type=repro 的产出物等人采纳。
// useArtifacts 是单例，本页签内只此一个面板实例（App.vue 的全局面板在 defect 页签是隐藏的，
// 因为 ARTIFACT_TAB_LABEL 不含 defect），所以必须自己带 id-suffix 防止与其它页签串 id。
const { openArtifact, loadArtifacts } = useArtifacts()

/** 摘要条：跑之前一眼看清「这次要处理哪些工单、会不会截图」 */
const summary = computed(() => [
  { label: '范围', value: mineOnly.value ? '只看我负责的' : '全部工单' },
  { label: '页面验收', value: skipVerify.value ? '跳过截图' : '会截图验收' },
  { label: 'Limit', value: String(limit.value ?? '') || '未填', tone: limit.value ? '' : 'todo' },
  { label: '工单', value: (defectId.value || '').trim() ? '指定 UUID' : '拉最近列表' },
])

/** 运行 / 试运行都会往主区写日志，先收抽屉再跑，主区整宽看过程 */
async function onStart(kind) {
  closeParams('defect')
  await start(kind)
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

onMounted(async () => {
  await ensure()
  await loadRepoBanner()
  await loadArtifacts('repro')
})
</script>

<template>
  <!-- 流水线步骤条：由 App.vue 以 #stage-flow 具名插槽注入（id 与状态类都在
       那边绑好）。放在模块最顶部而不是全局头部 —— 它是**这个模块**的进度条，
       横在整页头部会把整个版面（含 side 布局的左侧导航）往下挤。 -->
  <slot name="stage-flow" />

  <section class="panel collapsible" id="run-panel" :class="{ collapsed: isCollapsed('run-panel') }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3" /></svg>
        运行流水线
      </h2>
      <button class="fold-btn" title="折叠 / 展开" @click="toggleFold('run-panel')">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
    </div>
    <!-- 主区只放过程与产物：参数摘要条 + 实时运行日志。工单范围、Limit、
         试运行/运行这些一次性输入搬进了右侧参数抽屉（见文件末尾的 ParamDrawer）。 -->
    <ParamBar name="defect" action="运行参数" :items="summary" />

    <RunLog
      :lines="lines" :live="live" :has-live="hasLive" :replay="replay"
      :result-html="resultHtml" :running="running" :title="logTitle"
      :on-open="openRunModal"
      @open-proposal="(id) => emit('open-proposal', id)"
      @expand="(p) => emit('expand-log', p)"
    />
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
    <div class="block-banner" :class="{ hidden: !repoBanner }" id="repo-banner"
         v-html="repoBanner" />
    <div id="proposals" class="proposal-grid">
      <div v-if="proposalsError" class="empty">提案加载失败: {{ proposalsError }}</div>
      <div v-else-if="!proposals.length" class="empty">还没有提案。运行流水线后会在这里等你审阅采纳。</div>
      <div v-else-if="!pageItems.length" class="empty">当前筛选下没有提案。</div>
      <template v-else>
        <ProposalCard v-for="p in pageSlice" :key="p.id" :p="p"
                      @open="(id) => emit('open-proposal', id)" />
        <ProposalPager :info="pagerInfo" :nums="pagerNums" :current="curPage"
                       :total-pages="totalPages" :page-size="PROP_PAGE_SIZE"
                       @goto="gotoPropPage" />
      </template>
    </div>
    <p class="panel-note">运行流水线不会写目标仓库，只产出提案；在这里点「采纳」只会把改动写入工作区（不 commit、不 push），由你确认后自行提交。</p>
  </section>

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
  <ParamDrawer name="defect" title="运行参数"
               hint="工单范围与运行方式。点「运行 / 试运行」后抽屉自动收起，主区就是实时日志。">
    <label class="switch">
      <input type="checkbox" id="opt-mine" v-model="mineOnly">
      <span class="track" /><span class="switch-label">只看我负责的</span>
    </label>
    <label class="switch">
      <input type="checkbox" id="opt-skip-verify" v-model="skipVerify">
      <span class="track" /><span class="switch-label">跳过页面截图</span>
    </label>
    <label class="field-inline">
      <span>Limit</span>
      <input type="number" id="opt-limit" v-model.number="limit" min="1" max="20">
    </label>
    <label class="field">
      <span>工单 UUID（留空 = 拉取最近列表）</span>
      <input type="text" id="opt-defect" v-model="defectId"
             placeholder="填 ONES 工单的 UUID；留空则拉最近工单列表">
    </label>
    <div class="info-note" id="dry-warn">说明：「试运行」只拉取 ONES 工单并 AI 定位问题（不生成提案、不进审批、不写文件），用来独立验证连通性和定位效果；<br>「运行」走完整流水线产出提案（补丁 + diff + 闸门结果 + 截图），同样不改动目标仓库，人工采纳才写工作区（不 commit、不 push）。<br>运行过程中可实时看到每个阶段的进展，以及大模型的思考与输出。</div>

    <template #footer>
      <el-button type="primary" id="btn-run" :disabled="running" @click="onStart('run')">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3" /></svg>
        运行
      </el-button>
      <el-button id="btn-probe" :disabled="running" @click="onStart('probe')"
              title="只拉取 ONES 工单并 AI 定位问题，不生成提案、不进审批、不写任何文件">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
        试运行
      </el-button>
      <el-button id="btn-defect-refresh" @click="refreshAll()"
                 title="重新拉取提案 / 统计 / 知识库列表">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.3" /><polyline points="21 3 21 9 15 9" /></svg>
        刷新
      </el-button>
    </template>
  </ParamDrawer>
</template>
