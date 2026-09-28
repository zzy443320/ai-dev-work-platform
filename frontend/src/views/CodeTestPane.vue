<script setup>
// 代码测试页签（#pane-codetest）：目标文件 → 单元测试。
// 旧版 index.html:637-672 + app.js:1924-1940（loadTestFile）的等价迁移。
//
// #test-preview 与 #figma-tree 同套契约：空态走 style.css 的
// `#test-preview:empty::before`（data-placeholder），所以空的时候
// <pre> 内部不能有子节点；读取失败才退回纯文本。
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
        <input type="text" id="test-file" v-model="testFile" placeholder="src/components/OrderList/index.tsx">
      </label>
      <label class="field-inline">
        <span>测试框架</span>
        <select id="test-framework" v-model="testFramework">
          <option>vitest</option>
          <option>vitest + Testing Library</option>
          <option>jest</option>
          <option>jest + Testing Library</option>
          <option>cypress 组件测试</option>
        </select>
      </label>
      <button class="btn-ghost" id="btn-test-load" :disabled="testLoading"
              title="只读预览目标文件内容" @click="loadTestFile()">{{ testLoading ? '读取中…' : '预览文件' }}</button>
    </div>
    <label class="field">
      <span>重点关注（可选）</span>
      <textarea id="test-focus" rows="2" v-model="testFocus" placeholder="例如：分页逻辑、空数据渲染、提交失败后的状态回滚" />
    </label>
    <div class="run-form">
      <button class="btn-primary" id="btn-codetest" :disabled="running" @click="runTask('codetest')">生成测试代码</button>
    </div>
    <pre v-if="testError" id="test-preview" class="log">{{ testError }}</pre>
    <pre v-else id="test-preview" class="log" :data-placeholder="'点「预览文件」查看目标文件内容，确认路径无误'" v-html="testPreview" />
    <div class="info-note">后端会读取仓库里的目标文件（只读）交给 AI 分析；<br>生成的测试文件先落成「产出物」，人工采纳才写入工作区。</div>
  </section>
</template>
