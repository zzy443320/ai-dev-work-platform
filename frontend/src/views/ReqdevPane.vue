<script setup>
// 需求开发页签（#pane-reqdev）：Figma 设计稿 → 代码。
// 旧版 index.html:535-591 + app.js:1873-1921（setFigmaMode / fetchFigma）的等价迁移。
//
// ⚠️ 必须解构 useTasks()：<script setup> 只对顶层绑定做模板自动解包，
// `const t = useTasks()` 再到模板里写 `t.figmaUrl` 拿到的是 ref 对象。
//
// #figma-tree 是 v-html（.log-title / .info-note / .log-html 三段结构），
// 空态靠 style.css 的 `#figma-tree:empty::before` 显示 data-placeholder ——
// 所以空的时候 <pre> 里一个子节点都不能有，拉取失败时才退回纯文本。
//
// 参数形态（2026-10-13 改版）：七条参数含两段长文本 → schema 分档判给
// **常驻参数轨**（rail），字段定义与联动（取稿通道切「浏览器打开」时收起 Token）
// 都在 /api/params/schema 里声明。取代的是原来「摘要 chip + 浮层抽屉」：
// 抽屉开着会把主列挤到多列断点以下，而设计稿结构恰恰需要宽度。
//
// 控件 id 一律沿用改版前契约（#req-figma-url / #req-figma-mode /
// #req-figma-token-field / #req-desc / #req-context / #req-notes / #req-framework），
// 分段按钮保持原生 .seg-btn[data-mode]：界面用例直接点它。
import { useTasks } from '../composables/useTasks.js'
import { useParamModel } from '../composables/useParamModel.js'
import ParamLayout from '../components/ParamLayout.vue'

const {
  figmaMode, fetching, figmaTree, figmaError, fetchFigma,
  figmaUrl, figmaToken, reqFramework, reqDesc, reqContext, reqNotes,
  running, runTask,
} = useTasks()

const params = useParamModel(
  { figmaUrl, figmaMode, figmaToken, reqDesc, reqContext, reqFramework, reqNotes })
</script>

<template>
  <ParamLayout name="reqdev" :model="params">
    <template #actions>
      <el-button class="btn-ghost" id="btn-figma" :disabled="fetching"
                 title="只读拉取 Figma 设计稿结构，供 AI 参考" @click="fetchFigma()">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 8 12 3 17 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>
        {{ fetching ? '拉取中…' : '拉取设计稿' }}
      </el-button>
      <el-button type="primary" class="btn-primary" id="btn-reqdev" :disabled="running"
                 @click="runTask('reqdev')">生成代码</el-button>
    </template>

    <template #notes>
      <div class="info-note" id="req-figma-hint">Figma 官方接口只认 Personal Access Token（没有账号密码登录通道）。<br>没配 Token 也可以切到「浏览器打开」：用本机浏览器（含已登录的 Figma 会话）打开设计稿，只能拿到页面可见文案，<strong>拿不到图层结构</strong>，还原度会明显偏低。</div>
    </template>

    <!-- ── 主区：过程（设计稿结构）+ 说明；产物在 .col-main 里的全局产出物面板 ── -->
    <section class="panel" id="req-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15l-5-5L5 21" /></svg>
          需求开发 · Figma 设计稿转代码
        </h2>
      </div>

      <div class="req-src">
        <div class="src-caption">
          <span class="src-title">设计稿结构（拉取后即为 AI 的输入依据）</span>
          <span class="src-state" :class="{ hidden: !fetching }">拉取中…</span>
          <span class="src-state ok" :class="{ hidden: fetching || !figmaTree }">已拿到</span>
          <span class="src-state bad" :class="{ hidden: fetching || !figmaError }">拉取失败</span>
        </div>
        <!-- 空态走 :empty 占位；失败时退回纯文本 -->
        <pre v-if="figmaError" id="figma-tree" class="log">{{ figmaError }}</pre>
        <pre v-else id="figma-tree" class="log" :data-placeholder="'还没有设计稿结构。在右侧参数轨填 Figma 链接后点「拉取设计稿」，或直接生成（AI 只用需求描述）。'" v-html="figmaTree" />
      </div>

      <div class="info-note">「拉取设计稿」只读调用 Figma 官方接口，不写任何东西；<br>「生成代码」产出「产出物」（文件 + 方案），确认后在产出物详情里点「采纳」才写入工作区（不 commit、不 push）。</div>
    </section>
  </ParamLayout>
</template>
