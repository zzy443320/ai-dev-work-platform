<script setup>
// 接口联调页签（#pane-apidebug）：接口文档 → 对接方案。
// 旧版 index.html:593-635 的等价迁移。表单字段全部走 useTasks()（必须解构）。
//
// 布局（2026-09-30 第二次改版）：主区只留「参数摘要 + 产物引导 + 说明」，
// 接口文档与全部对接参数搬进右侧参数抽屉（见 components/ParamDrawer.vue）。
// 这个模块本身没有中间过程区（产物就是下方 .col-main 里的产出物面板），
// 所以主区刻意保持轻 —— 原来那张 12 行的 JSON 文本域占掉半屏，
// 把产出物挤到首屏以下，而它一天里只有填的时候会被看。
//
// 迁移说明：文本/密码 input 与 textarea 换成 el-input，技术栈下拉换成 el-select，
// 按钮换成 el-button。v-model 路径、字段 id 与按钮 id 一律不改（界面用例按 id 定位）。
import { computed } from 'vue'
import { useTasks } from '../composables/useTasks.js'
import { useModuleParams } from '../composables/useModuleParams.js'
import ParamBar from '../components/ParamBar.vue'
import ParamDrawer from '../components/ParamDrawer.vue'

const {
  apiDoc, apiBase, apiFramework, apiContext, apiNotes,
  running, runTask,
} = useTasks()

const { close: closeParams } = useModuleParams()

const summary = computed(() => {
  const doc = (apiDoc.value || '').trim()
  return [
    { label: '接口文档', value: doc ? `${doc.length} 字` : '未填', tone: doc ? '' : 'todo' },
    { label: 'Base URL', value: (apiBase.value || '').trim() || '未填（可选）' },
    { label: '技术栈', value: apiFramework.value || '未选' },
    { label: '请求封装参考', value: (apiContext.value || '').trim() ? '已提供' : '未提供' },
  ]
})

/** 提交后收起抽屉：要看的是产出的对接方案，不是刚填完的表单 */
function onRun() {
  closeParams('apidebug')
  runTask('apidebug')
}
</script>

<template>
  <section class="panel" id="api-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>
        接口联调 · 对接方案生成
      </h2>
    </div>

    <ParamBar name="apidebug" action="参数" :items="summary" />

    <div class="info-note">产出：对接步骤、需要新建/修改的文件（请求封装 + 类型定义 + 调用代码）、mock 数据、联调 checklist。<br>全部先落成下方「产出物」，人工采纳才写工作区（不 commit、不 push）。</div>
  </section>

  <ParamDrawer name="apidebug" title="联调参数"
               hint="接口文档是这份表单里唯一真正需要宽度的输入，其余都是可选项。">
    <label class="field">
      <span>接口文档（Swagger/OpenAPI JSON、Markdown、curl 示例均可）</span>
      <el-input id="api-doc" type="textarea" :rows="10" v-model="apiDoc" placeholder='{"paths": {"/api/v1/list": {...}}} 或直接粘贴后端给的文档' />
    </label>
    <label class="field">
      <span>后端 Base URL（可选）</span>
      <el-input id="api-base" v-model="apiBase" placeholder="http://10.0.0.1:8080" />
    </label>
    <label class="field">
      <span>技术栈</span>
      <el-select id="api-framework" v-model="apiFramework">
        <el-option value="React + TypeScript" label="React + TypeScript" />
        <el-option value="Vue 3 + TypeScript" label="Vue 3 + TypeScript" />
        <el-option value="React + JavaScript" label="React + JavaScript" />
        <el-option value="Vue 2 + JavaScript" label="Vue 2 + JavaScript" />
        <el-option value="小程序原生" label="小程序原生" />
        <el-option value="uni-app" label="uni-app" />
      </el-select>
    </label>
    <label class="field">
      <span>现有请求封装 / 相关页面代码（可选，生成代码要和它兼容）</span>
      <el-input id="api-context" type="textarea" :rows="6" v-model="apiContext" />
    </label>
    <label class="field">
      <span>其他约定（可选）</span>
      <el-input id="api-notes" v-model="apiNotes" placeholder="鉴权方式、错误码约定、埋点要求等" />
    </label>

    <template #footer>
      <el-button type="primary" class="btn-primary" id="btn-apidebug" :disabled="running" @click="onRun">生成联调方案</el-button>
    </template>
  </ParamDrawer>
</template>
