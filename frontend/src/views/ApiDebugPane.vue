<script setup>
// 接口联调页签（#pane-apidebug）：接口文档 → 对接方案。
// 旧版 index.html:593-635 的等价迁移。表单字段全部走 useTasks()（必须解构）。
//
// 迁移说明：文本/密码 input 与 textarea 换成 el-input，技术栈下拉换成 el-select，
// 按钮换成 el-button。v-model 路径与字段名一律不改。
import { useTasks } from '../composables/useTasks.js'

const {
  apiDoc, apiBase, apiFramework, apiContext, apiNotes,
  running, runTask,
} = useTasks()
</script>

<template>
  <section class="panel" id="api-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>
        接口联调 · 对接方案生成
      </h2>
    </div>
    <label class="field">
      <span>接口文档（Swagger/OpenAPI JSON、Markdown、curl 示例均可）</span>
      <el-input id="api-doc" type="textarea" :rows="7" v-model="apiDoc" placeholder='{"paths": {"/api/v1/list": {...}}} 或直接粘贴后端给的文档' />
    </label>
    <div class="run-form">
      <label class="field-inline grow">
        <span>后端 Base URL（可选）</span>
        <el-input id="api-base" v-model="apiBase" placeholder="http://10.0.0.1:8080" />
      </label>
      <label class="field-inline">
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
    </div>
    <label class="field">
      <span>现有请求封装 / 相关页面代码（可选，生成代码要和它兼容）</span>
      <el-input id="api-context" type="textarea" :rows="4" v-model="apiContext" />
    </label>
    <label class="field">
      <span>其他约定（可选）</span>
      <el-input id="api-notes" v-model="apiNotes" placeholder="鉴权方式、错误码约定、埋点要求等" />
    </label>
    <div class="run-form">
      <el-button type="primary" class="btn-primary" id="btn-apidebug" :disabled="running" @click="runTask('apidebug')">生成联调方案</el-button>
    </div>
    <div class="info-note">产出：对接步骤、需要新建/修改的文件（请求封装 + 类型定义 + 调用代码）、mock 数据、联调 checklist。<br>全部先落成「产出物」，人工采纳才写工作区。</div>
  </section>
</template>
