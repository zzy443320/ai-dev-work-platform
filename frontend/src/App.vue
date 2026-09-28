<script setup>
// 应用外壳：顶栏 + 页签 + 页签容器 + 右侧面板 + 弹窗 + 提示条。
//
// 结构对齐旧版 index.html 的骨架（.topbar / #tabs / .layout / .tabpane /
// #artifact-panel / 各 modal / #toast），class 名与 id 都沿用，
// 这样 style.css 与既有 Playwright 用例的选择器可以直接命中。
import { ref, computed, watch, onMounted } from 'vue'
import AppTabs from './components/AppTabs.vue'
import StatsPane from './views/StatsPane.vue'
import ChatPane from './views/ChatPane.vue'
import DefectPane from './views/DefectPane.vue'
import TeamPane from './views/TeamPane.vue'
import ReqdevPane from './views/ReqdevPane.vue'
import ApiDebugPane from './views/ApiDebugPane.vue'
import CodeTestPane from './views/CodeTestPane.vue'
import ExtensionsPane from './views/ExtensionsPane.vue'
import SettingsPanel from './views/SettingsPanel.vue'
import ArtifactsPanel from './components/ArtifactsPanel.vue'
import ArtifactModal from './components/ArtifactModal.vue'
import ProposalModal from './components/ProposalModal.vue'
import KbModal from './components/KbModal.vue'
import RunModal from './components/RunModal.vue'
import AiModal from './components/AiModal.vue'
import ChangelogModal from './components/ChangelogModal.vue'
import { TABS, DEFAULT_TAB, ARTIFACT_TAB_LABEL, STAGE_FLOW_TAB } from './components/tabs.js'
import { useToast } from './composables/useToast.js'
import { useTheme } from './composables/useTheme.js'
import { useRunStatus } from './composables/useRunStatus.js'
import { useHealth } from './composables/useHealth.js'
import { useArtifacts } from './composables/useArtifacts.js'
import { useDefect } from './composables/useDefect.js'
import { useSettings } from './composables/useSettings.js'
import { useProposalModal } from './composables/useProposalModal.js'
import { useKbModal } from './composables/useKbModal.js'
import { useRunModal } from './composables/useRunModal.js'
import { useChangelog } from './composables/useChangelog.js'

const { message: toastMsg, kind: toastKind, visible: toastVisible, toast } = useToast()
const { theme, toggle: toggleTheme } = useTheme()
const { text: runStatusText, kind: runStatusKind, setRunStatus } = useRunStatus()
// 健康检查是模块级单例 —— 顶栏读它，三个任务页签和产出物面板写它
const { health, healthError, loadHealth } = useHealth()
const { openArtifact, loadArtifacts } = useArtifacts()
// 缺陷页签的数据：切到该页签时要重拉（流水线刚产出的提案得立刻可见）。
// stageStates 由这里传给 #stage-flow —— 步骤条与真实结果联动（旧版 setStagesStates 的等价物）。
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
const showArtifactPanel = computed(() => !!artifactLabel.value)

function switchTab(name) {
  if (!TABS.some((t) => t.name === name)) return
  activeTab.value = name
}

// 迁移期兼容：界面用例直接调 window.switchTab('chat')
window.switchTab = switchTab
window.toggleTheme = toggleTheme

onMounted(() => {
  loadHealth()
  loadSettings()
  initChangelog()
})

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
const healthText = computed(() => {
  if (healthError.value) return `无法连接后端：${healthError.value}`
  if (!health.value) return '连接中…'
  const h = health.value
  return `AI: ${h.ai_mode || 'unknown'} · 仓库: ${h.repo_ok ? '已配置' : '未配置'}`
})
</script>

<template>
  <div class="bg-glow" />

  <header class="topbar">
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
    <div class="top-actions">
      <div class="status-pill" :class="statusClass" id="status">{{ statusText }}</div>
      <button class="icon-btn" id="changelog-btn" title="更新日志" @click="openChangelog">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 12a9 9 0 1 0 2.6-6.4" /><polyline points="3 4 3 9 8 9" /><path d="M12 7.5v5l3.5 2" />
        </svg>
        <span class="ch-dot" id="changelog-dot" aria-hidden="true" :class="{ hidden: !hasUnread }" />
      </button>
      <button class="icon-btn" id="theme-toggle" title="切换主题" @click="toggleTheme">
        <svg v-if="theme === 'dark'" viewBox="0 0 24 24" width="16" height="16" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
        </svg>
        <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      </button>
    </div>
  </header>

  <AppTabs v-model="activeTab" />

  <!-- 步骤条：5 步的 class 由 useDefect 的 stageStates 驱动（active/done/warn/fail/off，
       见 useDefect.js 的 stagesRunning/stagesFromRunResults/stagesFromProbeResults）。
       迁移期这里曾是写死的静态标记，导致 .stage.active/.done 等样式永远不生效。 -->
  <div v-show="showStageFlow" class="stage-flow" id="stage-flow">
    <div class="stage" :class="stageStates[0]"><span class="stage-num">1</span><div class="stage-name">拉取</div><div class="stage-desc">ONES 工单</div></div>
    <div class="stage-arrow" />
    <div class="stage" :class="stageStates[1]"><span class="stage-num">2</span><div class="stage-name">定位</div><div class="stage-desc">git grep + AI</div></div>
    <div class="stage-arrow" />
    <div class="stage" :class="stageStates[2]"><span class="stage-num">3</span><div class="stage-name">生成提案</div><div class="stage-desc">SEARCH-REPLACE</div></div>
    <div class="stage-arrow" />
    <div class="stage" :class="stageStates[3]"><span class="stage-num">4</span><div class="stage-name">闸门</div><div class="stage-desc">命令或降级</div></div>
    <div class="stage-arrow" />
    <div class="stage" :class="stageStates[4]"><span class="stage-num">5</span><div class="stage-name">沉淀</div><div class="stage-desc">知识库卡片</div></div>
  </div>

  <main class="layout">
    <div class="col-main">
      <!-- 页签容器：与旧版一致的 id / class，界面用例的 switchTab 断言可直接命中。
           已迁完的页签挂真实组件，未迁完的保留占位。 -->
      <div
        v-for="t in TABS"
        :key="t.name"
        class="tabpane"
        :class="{ active: activeTab === t.name }"
        :id="`pane-${t.name}`"
      >
        <StatsPane v-if="t.name === 'stats'" />
        <ChatPane v-else-if="t.name === 'chat'" />
        <DefectPane
          v-else-if="t.name === 'defect'"
          @open-proposal="openProposal"
          @open-kb-card="({ category, id }) => openCard(category, id)"
          @open-kb-pattern="openPattern"
          @open-artifact="onOpenArtifact"
          @expand-log="expandRunLog"
        />
        <TeamPane v-else-if="t.name === 'team'" />
        <ReqdevPane v-else-if="t.name === 'reqdev'" />
        <ApiDebugPane v-else-if="t.name === 'apidebug'" />
        <CodeTestPane v-else-if="t.name === 'codetest'" />
        <ExtensionsPane v-else-if="t.name === 'extensions'" />
        <div v-else class="empty">「{{ t.label }}」页签已接入 Vue 外壳，内容迁移中。</div>
      </div>

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

  <div id="toast" class="toast" :class="[toastKind, { hidden: !toastVisible }]">{{ toastMsg }}</div>
</template>
