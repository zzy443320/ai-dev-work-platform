<script setup>
// 接口联调页签（#pane-apidebug）：接口文档 → 对接方案。
// 旧版 index.html:593-635 的等价迁移。表单字段全部走 useTasks()（必须解构）。
//
// 参数形态（2026-10-13 改版）：接口文档是 rows=10 的长文本 → schema 分档判给
// **常驻参数轨**（rail）。它取代的是原来「摘要 chip + 右侧浮层抽屉」：抽屉一开
// 主列让位 436px、窄屏还会退成遮罩模态；轨是这一屏 grid 的真实第二列，长文档
// 拿到的是稳定宽度，产出物面板也不会被顶到首屏以下。
// 字段定义来自 /api/params/schema；控件 id 沿用 #api-doc / #api-base /
// #api-framework / #api-context / #api-notes，按钮 id 沿用 #btn-apidebug。
import { computed } from 'vue'
import { useTasks } from '../composables/useTasks.js'
import { useParamModel } from '../composables/useParamModel.js'
import ParamLayout from '../components/ParamLayout.vue'

const {
  apiDoc, apiBase, apiFramework, apiContext, apiNotes,
  running, runTask,
} = useTasks()

const params = useParamModel({ apiDoc, apiBase, apiFramework, apiContext, apiNotes })

/** 回显区标题上的真实计数 —— 只报已经有的数，不做任何加工。 */
const apiDocChars = computed(() => (apiDoc.value || '').trim().length)
</script>

<template>
  <ParamLayout name="apidebug" :model="params">
    <template #actions>
      <el-button type="primary" class="btn-primary" id="btn-apidebug" :disabled="running"
                 @click="runTask('apidebug')">生成联调方案</el-button>
    </template>

    <section class="panel" id="api-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>
          接口联调 · 对接方案生成
        </h2>
      </div>

      <!-- 舞台区：把「AI 要读的接口文档原文」回显成终端块，与需求开发的
           #figma-tree、代码测试的 #test-preview 同一套契约（空态走
           style.css 的 `#api-stage:empty::before` + data-placeholder）。
           为什么要有它：这个面板原先只有一行说明文字，右侧参数轨 780px 高，
           中间空出 673px —— 看着像版式坏了。回显原文不是装饰：贴进来的
           Swagger / curl 在 294px 宽的轨里根本读不完，这里才是能核对的地方。 -->
      <div class="api-stage">
        <div class="src-caption">
          <span class="src-title">接口文档原文（只读回显，AI 的分析依据）</span>
          <span class="src-state" :class="{ hidden: !running }">生成中…</span>
          <span class="src-state ok" :class="{ hidden: running || !apiDoc }">已贴入 {{ apiDocChars }} 字符</span>
        </div>
        <pre v-if="apiDoc" id="api-stage" class="log">{{ apiDoc }}</pre>
        <pre v-else id="api-stage" class="log"
             :data-placeholder="'还没有接口文档。在右侧「联调参数」贴 Swagger/OpenAPI JSON、Markdown 或 curl 示例，贴完这里会原样回显，点「生成联调方案」即以此为依据。'" />
      </div>

      <div class="info-note">产出：对接步骤、需要新建/修改的文件（请求封装 + 类型定义 + 调用代码）、mock 数据、联调 checklist。<br>全部先落成下方「产出物」，人工采纳才写工作区（不 commit、不 push）。</div>
    </section>
  </ParamLayout>
</template>
