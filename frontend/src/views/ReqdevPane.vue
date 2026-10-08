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
// 布局（2026-09-30 第二次改版）：**主区只放过程与产物**（设计稿结构预览 + 下方的
// 产出物面板），全部输入表单搬进右侧参数抽屉（components/ParamDrawer.vue），主区顶部
// 留一条摘要条（components/ParamBar.vue）。抽屉默认打开、非模态，所以主区不会被盖住；
// 点「拉取设计稿」「生成代码」时主动收起抽屉，让过程和产物占满整宽。
//
// 迁移说明：文本/密码 input 与 textarea 换成 el-input，框架下拉换成 el-select，
// 按钮换成 el-button。Figma 取稿通道的 .seg 分段按钮保持原生：界面用例直接点击
// #req-figma-mode .seg-btn[data-mode='browser']，el-radio-button 改结构会让选择器失效。
import { computed } from 'vue'
import { useTasks } from '../composables/useTasks.js'
import { useModuleParams } from '../composables/useModuleParams.js'
import ParamBar from '../components/ParamBar.vue'
import ParamDrawer from '../components/ParamDrawer.vue'

const {
  figmaMode, setFigmaMode, fetching, figmaTree, figmaError, fetchFigma,
  figmaUrl, figmaToken, reqFramework, reqDesc, reqContext, reqNotes,
  running, runTask,
} = useTasks()

const { close: closeParams } = useModuleParams()

/** 摘要条上的 chip：值短、能一眼看出「这次要跑什么」；没填的用 tone='todo' 标出来 */
const summary = computed(() => {
  const url = (figmaUrl.value || '').trim()
  const desc = (reqDesc.value || '').trim()
  return [
    { label: '取稿', value: figmaMode.value === 'browser' ? '浏览器打开' : 'Token' },
    { label: '设计稿', value: url ? url.replace(/^https?:\/\//, '').slice(0, 28) : '未填', tone: url ? '' : 'todo' },
    { label: '需求描述', value: desc ? `${desc.length} 字` : '未填', tone: desc ? '' : 'todo' },
    { label: '技术栈', value: reqFramework.value || '未选' },
  ]
})

/** 两个动作都会产出要看的东西（结构 / 产出物），所以先收抽屉再执行 */
function onFetch() {
  closeParams('reqdev')
  fetchFigma()
}
function onRun() {
  closeParams('reqdev')
  runTask('reqdev')
}
</script>

<template>
  <!-- ── 主区：过程（设计稿结构）+ 说明；产物在 .col-main 里的全局产出物面板 ── -->
  <section class="panel" id="req-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="M21 15l-5-5L5 21" /></svg>
        需求开发 · Figma 设计稿转代码
      </h2>
    </div>

    <ParamBar name="reqdev" action="参数" :items="summary" />

    <div class="req-src">
      <div class="src-caption">
        <span class="src-title">设计稿结构（拉取后即为 AI 的输入依据）</span>
        <span class="src-state" :class="{ hidden: !fetching }">拉取中…</span>
        <span class="src-state ok" :class="{ hidden: fetching || !figmaTree }">已拿到</span>
        <span class="src-state bad" :class="{ hidden: fetching || !figmaError }">拉取失败</span>
      </div>
      <!-- 空态走 :empty 占位；失败时退回纯文本 -->
      <pre v-if="figmaError" id="figma-tree" class="log">{{ figmaError }}</pre>
      <pre v-else id="figma-tree" class="log" :data-placeholder="'还没有设计稿结构。点右侧「修改生成参数」填 Figma 链接后拉取，或直接生成（AI 只用需求描述）。'" v-html="figmaTree" />
    </div>

    <div class="info-note">「拉取设计稿」只读调用 Figma 官方接口，不写任何东西；<br>「生成代码」产出「产出物」（文件 + 方案），确认后在产出物详情里点「采纳」才写入工作区（不 commit、不 push）。</div>
  </section>

  <!-- ── 右侧参数抽屉：全部输入 + 两个动作 ── -->
  <ParamDrawer name="reqdev" title="生成参数"
               hint="Figma 取稿方式、需求描述与代码风格约定。填完直接跑，跑起来抽屉会自动收起。">
    <!-- 取稿通道（Token / 浏览器）跟着它改的是取稿方式，所以和 Token 放同一组 -->
    <label class="field">
      <span>Figma 设计稿链接</span>
      <el-input id="req-figma-url" v-model="figmaUrl"
                placeholder="https://www.figma.com/design/xxxx/文件名?node-id=12-34" />
    </label>
    <div class="field">
      <span>取稿通道</span>
      <!-- 分段按钮保持原生：page.click('#req-figma-mode .seg-btn[data-mode=...]') -->
      <div class="seg" id="req-figma-mode">
        <button type="button" class="seg-btn" :class="{ active: figmaMode === 'token' }"
                data-mode="token" @click="setFigmaMode('token')">Token</button>
        <button type="button" class="seg-btn" :class="{ active: figmaMode === 'browser' }"
                data-mode="browser" @click="setFigmaMode('browser')">浏览器打开</button>
      </div>
    </div>
    <label class="field" id="req-figma-token-field" :class="{ hidden: figmaMode === 'browser' }">
      <span>Figma Token（留空=用已保存）</span>
      <el-input id="req-figma-token" type="password" v-model="figmaToken" placeholder="留空则用设置里的" autocomplete="off" />
    </label>
    <div class="info-note" id="req-figma-hint">Figma 官方接口只认 Personal Access Token（没有账号密码登录通道）。<br>没配 Token 也可以切到「浏览器打开」：用本机浏览器（含已登录的 Figma 会话）打开设计稿，只能拿到页面可见文案，<strong>拿不到图层结构</strong>，还原度会明显偏低。</div>

    <label class="field">
      <span>需求描述</span>
      <el-input id="req-desc" type="textarea" :rows="5" v-model="reqDesc" placeholder="这个页面/组件要做什么：交互、数据来源、状态与边界情况" />
    </label>
    <label class="field">
      <span>现有代码参考（可选，粘贴相关组件/封装，让生成的代码风格一致）</span>
      <el-input id="req-context" type="textarea" :rows="5" v-model="reqContext" />
    </label>
    <label class="field">
      <span>其他约定（可选）</span>
      <el-input id="req-notes" v-model="reqNotes" placeholder="命名规范、目录位置、UI 组件库等" />
    </label>
    <label class="field">
      <span>技术栈</span>
      <el-select id="req-framework" v-model="reqFramework">
        <el-option value="React + TypeScript" label="React + TypeScript" />
        <el-option value="Vue 3 + TypeScript" label="Vue 3 + TypeScript" />
        <el-option value="React + JavaScript" label="React + JavaScript" />
        <el-option value="Vue 2 + JavaScript" label="Vue 2 + JavaScript" />
        <el-option value="小程序原生" label="小程序原生" />
        <el-option value="uni-app" label="uni-app" />
      </el-select>
    </label>

    <template #footer>
      <el-button class="btn-ghost" id="btn-figma" :disabled="fetching"
                 title="只读拉取 Figma 设计稿结构，供 AI 参考" @click="onFetch">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 8 12 3 17 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>
        {{ fetching ? '拉取中…' : '拉取设计稿' }}
      </el-button>
      <el-button type="primary" class="btn-primary" id="btn-reqdev" :disabled="running" @click="onRun">生成代码</el-button>
    </template>
  </ParamDrawer>
</template>
