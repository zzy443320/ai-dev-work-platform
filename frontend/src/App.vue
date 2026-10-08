<script setup>
// 应用外壳：顶栏 + 导航（左侧分组 / 顶栏页签二选一）+ 页签容器 + 配置抽屉 + 弹窗 + 提示。
//
// 两种整体布局（useLayout，localStorage 记忆）：side 侧边工作台（默认）/ top 顶部导航。
// 两种布局下右侧配置栏都是浮层抽屉（齿轮开合，见 useConfigDrawer），主列独占整宽；
// 原来第三种「三栏经典」（常驻右栏 + 主列页签头）已于 2026-09-30 删除 —— 常驻右栏要给
// 它单独限高内滚才能不把页面顶出滚动条，跟其他模块的整页自然流是两套高度语言。
//
// 组件化收口后的状态：外壳自身的原语已全部换成 Element Plus ——
//   自绘按钮 → el-button / 自绘页签 → el-tabs / 自绘步骤条 → el-steps /
//   自绘状态药丸 → el-tag / 自绘 #toast → ElMessage（见 composables/useToast.js）。
//
// **保留的两处契约**（刻意，别当成遗留垃圾删掉）：
//   1. `#tabs` / `#pane-<name>` / `window.switchTab` —— tests/ 下 12 个界面用例
//      直接调 `switchTab('chat')` 并按 id 找 pane；`#tabs > .el-tabs__header` 虽然
//      两种布局下都被 CSS 藏起来了（导航在左侧或顶栏），DOM 仍在，用例照旧读它；
//   2. 步骤条的每一步仍带 `class="stage"` 与状态类 —— 用例断言
//      `#stage-flow .stage` 恰好 5 个、且第 1 步带 done。
// 这两处换成纯 Element 选择器也能测，但改成 `.el-*` 要同时改 12 个用例，
// 收益不抵风险，所以用「Element 结构 + 稳定 id/class」的方式并存。
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { Clock, Sunny, Moon, Setting } from '@element-plus/icons-vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import StatsPane from './views/StatsPane.vue'
import ChatPane from './views/ChatPane.vue'
import DefectPane from './views/DefectPane.vue'
import TeamPane from './views/TeamPane.vue'
import ReqdevPane from './views/ReqdevPane.vue'
import ApiDebugPane from './views/ApiDebugPane.vue'
import CodeTestPane from './views/CodeTestPane.vue'
import ExtensionsPane from './views/ExtensionsPane.vue'
// 配置：2026-09-30 独立成「配置」页签（ConfigPane）；SettingsPanel 只剩只读摘要 + 入口
import ConfigPane from './views/ConfigPane.vue'
import SettingsPanel from './views/SettingsPanel.vue'
import ArtifactsPanel from './components/ArtifactsPanel.vue'
import ArtifactModal from './components/ArtifactModal.vue'
import ProposalModal from './components/ProposalModal.vue'
import KbModal from './components/KbModal.vue'
import RunModal from './components/RunModal.vue'
import AiModal from './components/AiModal.vue'
import ChangelogModal from './components/ChangelogModal.vue'
import { TABS, DEFAULT_TAB, ARTIFACT_TAB_LABEL, STAGE_FLOW_TAB, FILL_TABS, ICON_PATHS } from './components/tabs.js'
import { useTheme } from './composables/useTheme.js'
import { useConfigDrawer } from './composables/useConfigDrawer.js'
import { useModuleParams } from './composables/useModuleParams.js'
import { useLayout } from './composables/useLayout.js'
import { useTabStrip } from './composables/useTabStrip.js'
import { useRunStatus } from './composables/useRunStatus.js'
import { useHealth } from './composables/useHealth.js'
import { useArtifacts } from './composables/useArtifacts.js'
import { useDefect } from './composables/useDefect.js'
import { useSettings } from './composables/useSettings.js'
import { useProposalModal } from './composables/useProposalModal.js'
import { useKbModal } from './composables/useKbModal.js'
import { useRunModal } from './composables/useRunModal.js'
import { useChangelog } from './composables/useChangelog.js'

const { theme, style, styles, toggle: toggleTheme, setStyle } = useTheme()
// 整体布局模式：side(默认侧边工作台) / top(顶部导航)。两种布局下右侧配置栏都是抽屉。
const { mode: layoutMode, setMode: setLayoutMode, layouts: layoutOptions } = useLayout()
// 配置抽屉的开合（顶栏齿轮与抽屉内收起按钮共用同一份状态，见 useConfigDrawer）
const { open: configOpen, toggle: toggleConfigDrawer } = useConfigDrawer()
// 六个任务型模块的参数抽屉开合（Esc 在外壳统一收，见下面 onEsc）
const { anyOpenFor, closeFor: closeParamsFor } = useModuleParams()
// 换布局时关掉抽屉：抽屉是浮层，跨布局保持开合状态只会让人找不到内容在哪
watch(layoutMode, () => { configOpen.value = false })
const settingsTooltip = computed(() =>
  (configOpen.value ? '关闭配置抽屉' : '打开配置抽屉'))
const { text: runStatusText, kind: runStatusKind } = useRunStatus()
// 健康检查是模块级单例 —— 顶栏读它，三个任务页签和产出物面板写它
const { health, healthError, loadHealth } = useHealth()
const { openArtifact, loadArtifacts } = useArtifacts()
// 缺陷页签的数据：切到该页签时要重拉（流水线刚产出的提案得立刻可见）。
// stageStates 由这里传给 #stage-flow —— 步骤条与真实结果联动。
// 与 DefectPane 共享同一套状态：useDefect 是模块级单例。
const { ensure, stageStates } = useDefect()

// 配置（侧栏 + 大模型弹窗）、三个详情弹窗与更新日志各自是模块级单例
const { loadSettings } = useSettings()
const { openProposal } = useProposalModal()
const { openCard, openPattern } = useKbModal()
const { openRunModal } = useRunModal()
const { hasUnread, init: initChangelog, open: openChangelog } = useChangelog()

const activeTab = ref(DEFAULT_TAB)
/** #aimodal 的显隐（旧版 openAiModal/closeAiModal 只切 hidden class） */
const aiModalVisible = ref(false)
function openAiModal() { aiModalVisible.value = true }
function closeAiModal() { aiModalVisible.value = false }
// SettingsPanel 里的「大模型设置」按钮走这里（旧版是内联 onclick="openAiModal()"）
window.openAiModal = openAiModal

// 切页签的副作用，从旧版 switchTab 的散落判断收拢成两处响应式派生
const showStageFlow = computed(() => activeTab.value === STAGE_FLOW_TAB)
const artifactLabel = computed(() => ARTIFACT_TAB_LABEL[activeTab.value] || '')
// 问答页签**不挂**这份全局产出物面板：它自己把面板收进了右侧栏（见 ChatPane），
// 两边同时挂会出现两个 #artifact-panel（同 id 重复），点击/断言都会串。
const showArtifactPanel = computed(
  () => !!artifactLabel.value && activeTab.value !== 'chat')
// 只有 FILL_TABS（问答）让主列的 #tabs 行走 1fr 吃满剩余高度；其余页签按内容自然流。
// 名单化是刻意的：产出物面板在 .col-main 里是 #tabs 的兄弟节点，#tabs 一被拉满，
// 模块面板和产出物面板中间就空出一整块（大屏上几百像素），像模块间距失控。
const paneFill = computed(() => FILL_TABS.includes(activeTab.value))
// 当前模块的参数抽屉开着 → 主列让出右侧空间（非模态，见 style.css 的 .params-open）。
// 用 anyOpenFor 而不是按页签名直接查：一个页签里可能有多个抽屉（扩展能力 = 技能 / MCP），
// key 形如 `页签名:槽位`，归属关系由 useModuleParams 统一判。
const paramsOpen = computed(() => anyOpenFor(activeTab.value))

function switchTab(name) {
  if (!TABS.some((t) => t.name === name)) return
  activeTab.value = name
}

// 迁移期兼容：界面用例直接调 window.switchTab('chat') / window.setThemeStyle('teal')
window.switchTab = switchTab
window.toggleTheme = toggleTheme
window.setThemeStyle = setStyle

// ── 顶部导航布局（top）的页签条 ────────────────────────────────────
// 9 个页签在 1440 及以下视口装不下，所以容器横向滚动、两端各浮一枚箭头。
// 尺寸测量与滚动逻辑在 composables/useTabStrip.js（那里写了三条「改之前先读」的
// 实现约束：箭头为什麼做浮层、显隐条件、活动页签为何要按箭头宽度内缩）。
// 这里只留模板要用的那几个名字。
const { tabStripEl, tabScrollable, tabAtStart, tabAtEnd, scrollTabs, syncTabScroll } =
  useTabStrip({ activeTab, layoutMode })

// ── 侧边工作台（side 布局）的分组导航 ──────────────────────────────
// 页签名/图标仍以 TABS 为唯一事实来源，这里只做分组投影；点项走同一个
// switchTab（window.switchTab 契约不受影响）。
const NAV_GROUPS = [
  { label: '总览', items: ['stats'] },
  { label: '协作', items: ['chat'] },
  { label: '交付', items: ['defect', 'team'] },
  { label: '工具箱', items: ['reqdev', 'apidebug', 'codetest', 'extensions'] },
  { label: '系统', items: ['config'] },
]
const tabBy = Object.fromEntries(TABS.map((t) => [t.name, t]))

// ── 顶部 5 步流程条 ────────────────────────────────────────────────
// 步骤状态由 useDefect 的 stageStates 驱动（active/done/warn/fail/off/idle）。
// 这里翻译成 el-step 认识的状态；同时把原始标签当 class 挂上去 ——
// 用例断言的是 `.stage` 上的 done/warn/fail 字样，Element 自己的
// is-finish / is-error 命名对不上，保留原文最稳。
const STAGES = [
  { name: '拉取', desc: 'ONES 工单' },
  { name: '定位', desc: 'git grep + AI' },
  { name: '生成提案', desc: 'SEARCH-REPLACE' },
  { name: '闸门', desc: '命令或降级' },
  { name: '沉淀', desc: '知识库卡片' },
]
const STEP_STATUS = {
  active: 'process',
  done: 'finish',
  fail: 'error',
  // el-step 只有 wait/process/finish/error/success 五种；warn 与 off 都落到 wait，
  // 靠 .stage.warn / .stage.off 的样式区分。
  warn: 'wait',
  off: 'wait',
  idle: 'wait',
}
/** stageStates 可能是 computed ref，也可能被换成 reactive 数组，两种都兜住 */
function stageState(i) {
  const s = stageStates && stageStates.value ? stageStates.value : stageStates
  return (s && s[i]) || 'idle'
}
function stageStatus(i) {
  return STEP_STATUS[stageState(i)] || 'wait'
}

// 配置抽屉与模块参数抽屉的 Esc 关闭：两类都是浮层，键盘用户也得能关掉
// （与问答两个浮层同一约定）。只绑外壳一处，不放进各组件 —— 它们不该关心键盘事件。
// 顺序：配置抽屉优先，其次当前页签那个模块的参数抽屉（切页签时只有活动 pane 可见，
// 但状态是全局的，所以按 activeTab 精确关一个，避免误关别的模块的开合状态）。
function onEsc(e) {
  if (e.key !== 'Escape') return
  if (configOpen.value) { configOpen.value = false; return }
  if (anyOpenFor(activeTab.value)) closeParamsFor(activeTab.value)
}

onMounted(() => {
  loadHealth()
  loadSettings()
  initChangelog()
  window.addEventListener('keydown', onEsc)
})

onBeforeUnmount(() => window.removeEventListener('keydown', onEsc))

// 页签切换后按需拉数据（旧版是在 switchTab 里直接调 loadArtifacts / ensure 等）。
// 产出物面板按当前页签的类型拉列表；缺陷修复页签旧版会直接 return，不动列表。
// 扩展能力页签的加载在 ExtensionsPane 自己的 onMounted 里（切进来即挂载即加载）。
//
// ⚠️ 缺陷页签**必须**在这里重拉一次：流水线/长任务可能刚产出新提案，
// 而缺陷页签首次挂载时列表还是空的（旧版 switchTab('defect') 会调 ensure()）。
// 漏掉这一步的表现是「刚跑完的提案在列表里看不到」，只有切走再切回才出来。
watch(activeTab, (name) => {
  if (ARTIFACT_TAB_LABEL[name]) loadArtifacts(name)
  if (name === 'defect') ensure()
}, { immediate: true })

// 弹窗里点卡片 / 问答回答里的「查看产出物」都走这里
function onOpenArtifact(id) {
  openArtifact(id)
}

/** 缺陷修复页签的「放大」：把运行日志区整块拷进 #runmodal（旧版 openRunModal） */
function expandRunLog(payload) {
  const p = payload || {}
  openRunModal(p.title || '运行结果', p.html || '')
}

// 顶栏状态药丸：三态合流 —— 健康检查的连接失败 > 流水线运行态 > 就绪。
// 旧版是各处直接写 $('#status')，谁后写谁生效；这里保留同样的优先级语义。
const statusText = computed(() => {
  if (healthError.value) return '连接失败'
  return runStatusText.value || '就绪'
})
const statusClass = computed(() => {
  if (healthError.value) return 'error'
  return runStatusKind.value
})
/** 状态 → el-tag 的语义色 */
const statusType = computed(() => {
  if (statusClass.value === 'error') return 'danger'
  if (statusClass.value === 'running') return 'warning'
  if (statusClass.value === 'ok') return 'success'
  return 'info'
})
const healthText = computed(() => {
  if (healthError.value) return `无法连接后端：${healthError.value}`
  if (!health.value) return '连接中…'
  const h = health.value
  return `AI: ${h.ai_mode || 'unknown'} · 仓库: ${h.repo_ok ? '已配置' : '未配置'}`
})
</script>

<template>
  <el-config-provider :locale="zhCn">
    <div class="bg-glow" />

    <header class="topbar" :class="`topbar-${layoutMode}`">
      <div class="brand">
        <div class="logo" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </div>
        <div class="title-block">
          <h1>ONES 前端研发助手</h1>
          <div class="subtitle" id="health">{{ healthText }}</div>
        </div>
      </div>

      <!-- 顶部导航布局（top）专用：页签搬到顶栏、紧贴品牌右侧（主列里的页签头
           由 .layout-top 的样式隐藏，pane 内容留在原位不动）。文字型导航形态：
           等宽均分整条空间，活动项用「文字宽度下划线」表示（下划线挂在 .tt-label
           上，所以宽度自然跟着字走，不是整格宽）。装不下时横向滚动，两端浮出箭头。
           hidden 契约：始终渲染 + .hidden，切布局不重建 DOM —— 滚动位置和用例
           选择器都是稳的。 -->
      <nav
        class="topbar-tabs"
        :class="[
          { hidden: layoutMode !== 'top' },
          { 'has-prev': tabScrollable && !tabAtStart, 'has-next': tabScrollable && !tabAtEnd },
        ]"
        aria-label="模块导航"
      >
        <div class="tt-strip" ref="tabStripEl" @scroll.passive="syncTabScroll">
          <button
            v-for="t in TABS"
            :key="t.name"
            type="button"
            class="tt-tab"
            :class="{ active: activeTab === t.name }"
            :aria-current="activeTab === t.name ? 'page' : 'false'"
            @click="switchTab(t.name)"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                 v-html="ICON_PATHS[t.icon]" />
            <span class="tt-label">{{ t.label }}</span>
          </button>
        </div>

        <button type="button" class="tt-arrow prev"
                :class="{ hidden: !tabScrollable || tabAtStart }"
                aria-label="向左滚动页签" @click="scrollTabs(-1)">
          <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor"
               stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9.8 3.6 5.4 8l4.4 4.4" />
          </svg>
        </button>
        <button type="button" class="tt-arrow next"
                :class="{ hidden: !tabScrollable || tabAtEnd }"
                aria-label="向右滚动页签" @click="scrollTabs(1)">
          <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor"
               stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6.2 3.6 10.6 8l-4.4 4.4" />
          </svg>
        </button>
      </nav>

      <div class="top-actions">
        <el-tag id="status" class="status-tag" :type="statusType" effect="light" round
                :class="{ 'is-pulsing': statusClass === 'running' }">{{ statusText }}</el-tag>

        <!-- 整体布局切换：侧边工作台 / 顶部导航，选中态高亮，localStorage 记忆（useLayout） -->
        <div class="layout-switch" role="group" aria-label="整体布局切换">
          <el-tooltip v-for="l in layoutOptions" :key="l.key" :content="l.label" placement="bottom">
            <button type="button" class="ls-btn" :class="{ active: layoutMode === l.key }"
                    :aria-pressed="layoutMode === l.key" @click="setLayoutMode(l.key)">
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
                   stroke-width="1.6" stroke-linecap="round">
                <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" />
                <line v-if="l.key === 'top'" x1="1.5" y1="6" x2="14.5" y2="6" />
                <line v-else x1="5.5" y1="2.5" x2="5.5" y2="13.5" />
              </svg>
            </button>
          </el-tooltip>
        </div>

        <!-- 风格切换：只换一个主色，Element 的派生色与项目变量一起重算（useTheme） -->
        <el-select
          id="style-picker"
          class="style-picker"
          size="small"
          :model-value="style"
          @change="setStyle"
        >
          <el-option v-for="s in styles" :key="s.key" :label="s.label" :value="s.key">
            <span class="style-dot" :style="{ background: s.primary }" />{{ s.label }}
          </el-option>
        </el-select>

        <el-tooltip :content="settingsTooltip" placement="bottom">
          <el-button id="sidebar-toggle" class="icon-btn" text circle @click="toggleConfigDrawer">
            <el-icon><Setting /></el-icon>
          </el-button>
        </el-tooltip>

        <el-tooltip content="更新日志" placement="bottom">
          <el-button id="changelog-btn" class="icon-btn" text circle @click="openChangelog">
            <el-icon><Clock /></el-icon>
            <span class="ch-dot" id="changelog-dot" aria-hidden="true" :class="{ hidden: !hasUnread }" />
          </el-button>
        </el-tooltip>

        <el-tooltip :content="theme === 'dark' ? '切换到浅色' : '切换到深色'" placement="bottom">
          <el-button id="theme-toggle" class="icon-btn" text circle @click="toggleTheme">
            <el-icon><Moon v-if="theme === 'dark'" /><Sunny v-else /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
    </header>

    <!-- 配置抽屉遮罩（点击空白关闭抽屉）。id 与 #chat-float-mask 对齐：界面用例要点它 -->
    <div class="config-mask" id="config-mask" v-show="configOpen" @click="configOpen = false" />

    <main class="layout" :class="[`layout-${layoutMode}`, { 'config-open': configOpen }]">
      <!-- 侧边工作台（side 布局）的分组导航：替代主列页签头。
           top 布局下整条 v-show 收掉，导航改由顶栏里的 .topbar-tabs 承担。 -->
      <nav class="side-nav" v-show="layoutMode === 'side'" aria-label="模块导航">
        <div v-for="g in NAV_GROUPS" :key="g.label" class="nav-group">
          <div class="nav-group-label">{{ g.label }}</div>
          <button v-for="name in g.items" :key="name" type="button" class="nav-item"
                  :class="{ active: activeTab === name }" @click="switchTab(name)">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                 v-html="ICON_PATHS[tabBy[name].icon]" />
            <span>{{ tabBy[name].label }}</span>
          </button>
        </div>
      </nav>

      <div class="col-main" :class="{ 'pane-fill': paneFill, 'params-open': paramsOpen }">
        <!-- 页签容器：id 沿用 #pane-<name>，与旧版一致。el-tab-pane 自己会用
             v-show 隐藏非活动页，这里额外挂 `active` 类是为了让既有断言
             （`el.classList.contains('active')`）与 style.css 的
             `.tabpane.active { display:grid; gap:18px }` 继续成立 ——
             面板间距有用例断言为 ≥12px，不能丢。
             两种布局都把 .el-tabs__header 藏了（导航在左侧或顶栏），但只藏头部：
             pane 全在 .el-tabs__content 里，整表 v-show 会把内容一起藏空。 -->
        <el-tabs id="tabs" v-model="activeTab">
          <el-tab-pane
            v-for="t in TABS"
            :key="t.name"
            :name="t.name"
            :id="`pane-${t.name}`"
            class="tabpane"
            :class="{ active: activeTab === t.name }"
          >
            <template #label>
              <span class="tab-label">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                     v-html="ICON_PATHS[t.icon]" />
                {{ t.label }}
              </span>
            </template>

            <StatsPane v-if="t.name === 'stats'" />
            <!-- ⚠️ @open-artifact 必须绑：问答消息卡里的「查看 / 采纳」靠它开弹窗。
                 漏绑的表现是点了没反应、控制台也没有任何报错（emit 进了虚空）。 -->
            <ChatPane v-else-if="t.name === 'chat'" @open-artifact="onOpenArtifact" />
            <DefectPane
              v-else-if="t.name === 'defect'"
              @open-proposal="openProposal"
              @open-kb-card="({ category, id }) => openCard(category, id)"
              @open-kb-pattern="openPattern"
              @open-artifact="onOpenArtifact"
              @expand-log="expandRunLog"
            >
              <!-- 步骤条：5 步的状态由 useDefect 的 stageStates 驱动（active/done/warn/
                   fail/off，见 useDefect.js 的 stagesRunning/stagesFromRunResults/
                   stagesFromProbeResults）。每步额外挂 `class="stage"` 与原始状态词，
                   界面用例按这两个断言（见文件头注释）。
                   位置：2026-09-30 从「顶栏下方」移进缺陷修复模块内部（DefectPane 的
                   #stage-flow 插槽）。原先它挂在 header 与 main.layout 之间、横跨整页
                   宽度（1600 视口下 1544px），把整块版面往下推 —— side 布局下左侧导航
                   也跟着被挤下去。v-show 留在 el-steps 自己身上（不是外层包一层），
                   因为 check_vue_shell_ui 读的就是 #stage-flow 自身的 computed display。 -->
              <template #stage-flow>
                <el-steps v-show="showStageFlow" id="stage-flow" class="stage-flow" align-center>
                  <el-step
                    v-for="(s, i) in STAGES"
                    :key="s.name"
                    class="stage"
                    :class="stageState(i)"
                    :title="s.name"
                    :description="s.desc"
                    :status="stageStatus(i)"
                  />
                </el-steps>
              </template>
            </DefectPane>
            <TeamPane v-else-if="t.name === 'team'" />
            <ReqdevPane v-else-if="t.name === 'reqdev'" />
            <ApiDebugPane v-else-if="t.name === 'apidebug'" />
            <CodeTestPane v-else-if="t.name === 'codetest'" />
            <ExtensionsPane v-else-if="t.name === 'extensions'" />
            <ConfigPane v-else-if="t.name === 'config'" />
            <el-empty v-else description="内容迁移中" />
          </el-tab-pane>
        </el-tabs>

        <ArtifactsPanel :label="artifactLabel" :visible="showArtifactPanel" @open="onOpenArtifact" />
      </div>

      <SettingsPanel />
    </main>

    <ArtifactModal />
    <ProposalModal />
    <KbModal />
    <RunModal />
    <AiModal :visible="aiModalVisible" @close="closeAiModal" />
    <ChangelogModal />
  </el-config-provider>
</template>
