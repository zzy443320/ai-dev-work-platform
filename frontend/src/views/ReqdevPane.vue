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
import { useTasks } from '../composables/useTasks.js'
import { useFold } from '../composables/useFold.js'

const {
  figmaMode, setFigmaMode, fetching, figmaTree, figmaError, fetchFigma,
  figmaUrl, figmaToken, reqFramework, reqDesc, reqContext, reqNotes,
  running, runTask,
} = useTasks()

const { isCollapsed, toggleFold } = useFold()
</script>

<template>
  <section class="panel" id="req-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15l-5-5L5 21" /></svg>
        需求开发 · Figma 设计稿转代码
      </h2>
    </div>
    <div class="run-form">
      <label class="field-inline grow">
        <span>Figma 设计稿链接（可带 node-id，定位到具体画板）</span>
        <input type="text" id="req-figma-url" v-model="figmaUrl" placeholder="https://www.figma.com/design/xxxx/文件名?node-id=12-34">
      </label>
      <div class="seg" id="req-figma-mode">
        <button type="button" class="seg-btn" :class="{ active: figmaMode === 'token' }"
                data-mode="token" @click="setFigmaMode('token')">Token</button>
        <button type="button" class="seg-btn" :class="{ active: figmaMode === 'browser' }"
                data-mode="browser" @click="setFigmaMode('browser')">浏览器打开</button>
      </div>
      <label class="field-inline" id="req-figma-token-field" :class="{ hidden: figmaMode === 'browser' }">
        <span>Figma Token（留空=用已保存）</span>
        <input type="password" id="req-figma-token" v-model="figmaToken" placeholder="留空则用设置里的" autocomplete="off">
      </label>
      <label class="field-inline">
        <span>技术栈</span>
        <select id="req-framework" v-model="reqFramework">
          <option>React + TypeScript</option>
          <option>Vue 3 + TypeScript</option>
          <option>React + JavaScript</option>
          <option>Vue 2 + JavaScript</option>
          <option>小程序原生</option>
          <option>uni-app</option>
        </select>
      </label>
      <button class="btn-ghost" id="btn-figma" :disabled="fetching"
              title="只读拉取 Figma 设计稿结构，供 AI 参考" @click="fetchFigma()">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>
        {{ fetching ? '拉取中…' : '拉取设计稿' }}
      </button>
    </div>
    <div class="info-note" id="req-figma-hint">Figma 官方接口只认 Personal Access Token（没有账号密码登录通道）。<br>没配 Token 也可以切到「浏览器打开」：用本机浏览器（含已登录的 Figma 会话）打开设计稿，只能拿到页面可见文案，<strong>拿不到图层结构</strong>，还原度会明显偏低。</div>
    <!-- 空态走 :empty 占位；失败时退回纯文本 -->
    <pre v-if="figmaError" id="figma-tree" class="log">{{ figmaError }}</pre>
    <pre v-else id="figma-tree" class="log" :data-placeholder="'拉取后的设计稿结构会显示在这里（缩进表示层级，含尺寸/颜色/文案）'" v-html="figmaTree" />
    <label class="field">
      <span>需求描述</span>
      <textarea id="req-desc" rows="3" v-model="reqDesc" placeholder="这个页面/组件要做什么：交互、数据来源、状态与边界情况" />
    </label>
    <label class="field">
      <span>现有代码参考（可选，粘贴相关组件/封装，让生成的代码风格一致）</span>
      <textarea id="req-context" rows="3" spellcheck="false" v-model="reqContext" />
    </label>
    <label class="field">
      <span>其他约定（可选）</span>
      <input type="text" id="req-notes" v-model="reqNotes" placeholder="命名规范、目录位置、UI 组件库等">
    </label>
    <div class="run-form">
      <button class="btn-primary" id="btn-reqdev" :disabled="running" @click="runTask('reqdev')">生成代码</button>
    </div>
    <div class="info-note">「拉取设计稿」只读调用 Figma 官方接口，不写任何东西；<br>「生成代码」产出「产出物」（文件 + 方案），确认后在产出物详情里点「采纳」才写入工作区（不 commit、不 push）。</div>
  </section>
</template>
