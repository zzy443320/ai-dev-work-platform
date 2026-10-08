<!--
  配置抽屉（#settings-panel）—— 2026-09-30 改为**只读摘要 + 入口**。

  改版原因：原先侧栏里塞了全部 cfg-* 编辑控件（仓库/ONES/Figma/闸门/页面验证），
  330px 宽挤不下、改完还容易漏点保存。现在全部编辑控件搬进独立的「配置」页签
  （ConfigPane.vue），这里只展示**最关键的几项**（仓库路径与分支、当前模型与
  模式、ONES / Figma 接入状态），并给一个「打开配置模块」的入口按钮。

  形态：三栏经典布局去掉之后，两种布局（侧边工作台 / 顶部导航）下这块都是
  **右侧抽屉**（.col-side position:fixed），由顶栏齿轮或这里的收起按钮开合，
  不再有「常驻右栏 + 竖排展开条」那一态。

  契约：id 沿用旧版（#settings-panel / #settings-fold）；摘要项用 `#cfg-sum-*` 新 id。
  这里**没有任何可编辑控件**——用例会断言 #settings-panel 里不存在 #cfg-repo-path
  这类输入框。状态仍来自 useSettings / useHealth（模块级单例），与配置模块同一份数据。
-->
<script setup>
import { computed } from 'vue'
import { useSettings } from '../composables/useSettings.js'
import { useHealth } from '../composables/useHealth.js'
import { useConfigDrawer } from '../composables/useConfigDrawer.js'
import { shortPath } from '../utils/format.js'

const { settings, aiSummaryHtml } = useSettings()
const { health } = useHealth()
// 抽屉开合是模块级单例：顶栏的 #sidebar-toggle 与这里的 #settings-fold 共用同一份。
const { hide: closeDrawer } = useConfigDrawer()

/** 仓库路径与分支（只读展示，路径不存在/分支不一致要能一眼看出来） */
const repoPath = computed(() => ((settings.value || {}).repo || {}).path || '')
const repoBranch = computed(() => ((settings.value || {}).repo || {}).branch || '')
const healthRepoOk = computed(() => !!(health.value || {}).repo_ok)
/** 工作区当前分支与期望分支：不一致时采纳会被拒，必须显形 */
const branchCurrent = computed(() => {
  const h = health.value || {}
  return h.current_branch || ((h.preflight || {}).current_branch) || ''
})
const branchMismatch = computed(() =>
  !!branchCurrent.value && !!repoBranch.value && branchCurrent.value !== repoBranch.value)

/** 接入状态：只报「已配置 / 未配置」，不显示任何凭据内容 */
const onesConfigured = computed(() => {
  const s = settings.value || {}
  return !!(s.has_token || s.has_password || ((s.ones || {}).base_url))
})
const figmaConfigured = computed(() => !!(settings.value || {}).has_figma_token)

/** 点入口跳到「配置」页签（window.switchTab 是迁移期兼容层，用例也用它）。
 *  顺手关掉抽屉：不关的话跳过去之后抽屉还盖在右半边，看着像「点了没反应」。 */
function goConfig() {
  closeDrawer()
  if (window.switchTab) window.switchTab('config')
}
</script>

<template>
  <aside class="col-side">
    <section class="panel" id="settings-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>
          配置
          <span class="head-note">只读摘要 · 改配置去「配置」页签</span>
        </h2>
        <!-- id 沿用旧版的 settings-fold；语义从「收起常驻栏」变成「关闭抽屉」，
             图标也跟着换成向右的箭头（推进去 = 收起来） -->
        <button class="fold-btn" id="settings-fold" title="关闭配置抽屉" @click="closeDrawer">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>
        </button>
      </div>

      <div class="settings-group">
        <div class="group-title">代码仓库</div>
        <div class="pm-line">
          <b>路径</b>
          <span id="cfg-sum-repo-path" :title="repoPath">{{ repoPath ? shortPath(repoPath) : '未配置' }}</span>
        </div>
        <div class="pm-line">
          <b>分支</b>
          <span id="cfg-sum-branch">{{ repoBranch || '未配置' }}</span>
        </div>
        <!-- 只在真有问题时提示：路径不可用 / 工作区分支与期望不一致 -->
        <div class="repo-hint bad" id="cfg-sum-repo-hint" :class="{ hidden: healthRepoOk && !branchMismatch }">
          <template v-if="!healthRepoOk">仓库路径不可用，去「配置」页签检查。</template>
          <template v-else-if="branchMismatch">当前分支 {{ branchCurrent }} ≠ 期望 {{ repoBranch }}，采纳会被拒。</template>
        </div>
      </div>

      <div class="settings-group" id="cfg-sum-ai-group">
        <div class="group-title">大模型</div>
        <div class="ai-summary" id="cfg-sum-ai" v-html="aiSummaryHtml"></div>
      </div>

      <div class="settings-group">
        <div class="group-title">接入状态</div>
        <div class="pm-line">
          <b>ONES</b>
          <span id="cfg-sum-ones">
            <span class="sum-dot" :class="onesConfigured ? 'ok' : 'off'" />{{ onesConfigured ? '已配置' : '未配置' }}
          </span>
        </div>
        <div class="pm-line">
          <b>Figma</b>
          <span id="cfg-sum-figma">
            <span class="sum-dot" :class="figmaConfigured ? 'ok' : 'off'" />{{ figmaConfigured ? '已配置' : '未配置' }}
          </span>
        </div>
      </div>

      <div class="settings-actions">
        <el-button type="primary" class="btn-primary" id="btn-open-config" @click="goConfig">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>
          打开配置模块
        </el-button>
      </div>
      <p class="settings-note">这里只展示关键配置，全部项（含 ONES / Figma / 闸门 / 页面验证）在<b>「配置」页签</b>里编辑。</p>
    </section>
  </aside>
</template>
