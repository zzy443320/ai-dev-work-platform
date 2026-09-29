<script setup>
// 代码测试页签（#pane-codetest）：目标文件 → 单元测试。
// 旧版 index.html:637-672 + app.js:1924-1940（loadTestFile）的等价迁移。
//
// #test-preview 与 #figma-tree 同套契约：空态走 style.css 的
// `#test-preview:empty::before`（data-placeholder），所以空的时候
// <pre> 内部不能有子节点；读取失败才退回纯文本。
//
// 迁移说明：文本 input 与 textarea 换成 el-input，测试框架下拉换成 el-select，
// 按钮换成 el-button。v-model 路径与字段名一律不改。
import { useTasks } from '../composables/useTasks.js'

const {
  testFile, testFramework, testFocus, testLoading, testPreview, testError, loadTestFile,
  running, runTask,
} = useTasks()
</script>

<template>
  <section class="panel" id="test-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>
        代码测试 · 单元测试生成
      </h2>
    </div>
    <div class="run-form">
      <label class="field-inline grow">
        <span>目标文件路径（相对仓库根）</span>
        <el-input id="test-file" v-model="testFile" placeholder="src/components/OrderList/index.tsx" />
      </label>
      <label class="field-inline">
        <span>测试框架</span>
        <el-select id="test-framework" v-model="testFramework">
          <el-option value="vitest" label="vitest" />
          <el-option value="vitest + Testing Library" label="vitest + Testing Library" />
          <el-option value="jest" label="jest" />
          <el-option value="jest + Testing Library" label="jest + Testing Library" />
          <el-option value="cypress 组件测试" label="cypress 组件测试" />
        </el-select>
      </label>
      <el-button class="btn-ghost" id="btn-test-load" :disabled="testLoading"
              title="只读预览目标文件内容" @click="loadTestFile()">{{ testLoading ? '读取中…' : '预览文件' }}</el-button>
    </div>
    <label class="field">
      <span>重点关注（可选）</span>
      <el-input id="test-focus" type="textarea" :rows="2" v-model="testFocus" placeholder="例如：分页逻辑、空数据渲染、提交失败后的状态回滚" />
    </label>
    <div class="run-form">
      <el-button type="primary" class="btn-primary" id="btn-codetest" :disabled="running" @click="runTask('codetest')">生成测试代码</el-button>
    </div>
    <pre v-if="testError" id="test-preview" class="log">{{ testError }}</pre>
    <pre v-else id="test-preview" class="log" :data-placeholder="'点「预览文件」查看目标文件内容，确认路径无误'" v-html="testPreview" />
    <div class="info-note">后端会读取仓库里的目标文件（只读）交给 AI 分析；<br>生成的测试文件先落成「产出物」，人工采纳才写入工作区。</div>
  </section>
</template>
