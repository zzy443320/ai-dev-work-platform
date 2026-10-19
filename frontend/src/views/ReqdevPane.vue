<script setup>
// Figma 转代码页签（#pane-reqdev）：Figma 设计稿 → 代码。
// 2026-10-19 改名：原「需求开发」其实只做设计稿还原，需求分诊/接口数据类改动
// 已挪到新的「需求开发」（#pane-req）。**内部 name 仍是 reqdev**：产出物 type、
// 技能范围、界面用例与 #req-* 控件 id 都绑着它，改标识会牵动历史数据。
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
import { nextTick, ref, watch } from 'vue'
import { useTasks } from '../composables/useTasks.js'
import { useParamModel } from '../composables/useParamModel.js'
import { useToast } from '../composables/useToast.js'
import { useReqHandoff } from '../composables/useReqHandoff.js'
import ParamLayout from '../components/ParamLayout.vue'
import LiveBrowserPanel from '../components/LiveBrowserPanel.vue'

const {
  figmaMode, fetching, figmaTree, figmaError, fetchFigma, appendFigmaInspection,
  figmaUrl, figmaToken, reqFramework, reqDesc, reqContext, reqNotes,
  running, runTask,
} = useTasks()
const { toast } = useToast()

// 需求开发页判出「需要设计稿」时会把需求原文交接过来（见 useReqHandoff）。
// 只覆盖需求描述，不动用户已经填好的 Figma 链接与代码参考；用掉即清。
const { draft: handoff } = useReqHandoff()
watch(handoff, (d) => {
  if (!d) return
  if (d.requirement) reqDesc.value = d.requirement
  if (d.notes) reqNotes.value = d.notes
  handoff.value = null
}, { immediate: true })

const params = useParamModel(
  { figmaUrl, figmaMode, figmaToken, reqDesc, reqContext, reqFramework, reqNotes })

// ---- 内嵌看稿（2026-10-14）：受控浏览器的"直播 + 遥控" ----
// 会话归 LiveBrowserPanel 自己管（切页签后端会话保留，回来 refreshStatus 续画面）；
// 这里只负责「用参数轨里的 Figma 链接开会话」与「点选读到的属性落回设计稿结构」。
const liveOn = ref(false)
const livePanel = ref(null)
async function openLive() {
  const url = figmaUrl.value.trim()
  if (!url) {
    toast('先在右侧参数轨填 Figma 设计稿链接', 'warn')
    return
  }
  liveOn.value = true
  await nextTick()
  livePanel.value && livePanel.value.open(url)
}
function closeLive() {
  liveOn.value = false
}
function onInspected(r) {
  const text = (r && r.panel || []).join('\n')
  if (text) appendFigmaInspection(`页面「${r.title || ''}」\n${text}`)
}
</script>

<template>
  <ParamLayout name="reqdev" :model="params">
    <template #actions>
      <el-button class="btn-ghost" id="btn-figma" :disabled="fetching"
                 title="只读拉取 Figma 设计稿结构，供 AI 参考" @click="fetchFigma()">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 8 12 3 17 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>
        {{ fetching ? '拉取中…' : '拉取设计稿' }}
      </el-button>
      <el-button class="btn-ghost" id="btn-figma-live" title="受控浏览器里打开设计稿：可登录、可点选节点读属性（配置页可存 Figma 账号密码）"
                 @click="openLive">内嵌看稿</el-button>
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
          Figma 转代码 · 设计稿还原实现
        </h2>
      </div>

      <!-- 内嵌看稿：受控浏览器的直播画面 + 点选读属性；切回「设计稿结构」看累积结果 -->
      <div class="src-caption" v-if="liveOn">
        <span class="src-title">内嵌看稿（点选节点 → 属性面板文本自动落到「设计稿结构」）</span>
        <span class="src-state ok">会话独立于下方结构区，可随时切回</span>
      </div>
      <LiveBrowserPanel v-if="liveOn" ref="livePanel" @inspected="onInspected" @stopped="closeLive" />
      <div v-else class="req-src">
        <div class="src-caption">
          <span class="src-title">设计稿结构（拉取后即为 AI 的输入依据）</span>
          <span class="src-state" :class="{ hidden: !fetching }">拉取中…</span>
          <span class="src-state ok" :class="{ hidden: fetching || !figmaTree }">已拿到</span>
          <span class="src-state bad" :class="{ hidden: fetching || !figmaError }">拉取失败</span>
        </div>
        <!-- 空态走 :empty 占位；失败时退回纯文本 -->
        <pre v-if="figmaError" id="figma-tree" class="log">{{ figmaError }}</pre>
        <pre v-else id="figma-tree" class="log" :data-placeholder="'还没有设计稿结构。在右侧参数轨填 Figma 链接后点「拉取设计稿」，或点「内嵌看稿」直接在受控浏览器里看稿、点选节点读属性。'" v-html="figmaTree" />
      </div>

      <div class="info-note">「拉取设计稿」只读调用 Figma 官方接口，不写任何东西；<br>「生成代码」产出「产出物」（文件 + 方案），确认后在产出物详情里点「采纳」才写入工作区（不 commit、不 push）。</div>
    </section>
  </ParamLayout>
</template>
