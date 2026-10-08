<script setup>
// 代码测试页签（#pane-codetest）：目标文件 → 单元测试。
// 旧版 index.html:637-672 + app.js:1924-1940（loadTestFile）的等价迁移。
//
// #test-preview 与 #figma-tree 同套契约：空态走 style.css 的
// `#test-preview:empty::before`（data-placeholder），所以空的时候
// <pre> 内部不能有子节点；读取失败才退回纯文本。
//
// 布局（2026-09-30 第二次改版）：主区只留「参数摘要 + 目标文件预览（过程）+ 说明」，
// 路径/框架/重点关注搬进右侧参数抽屉。预览是 AI 的输入依据，属于过程，留在主区，
// 而且它需要宽度 —— 原来右侧参数栏把这块只读视窗压到半屏，正好反过来才对。
//
// 迁移说明：文本 input 与 textarea 换成 el-input，测试框架下拉换成 el-select，
// 按钮换成 el-button。v-model 路径、字段 id 与按钮 id 一律不改。
import { computed } from 'vue'
import { useTasks } from '../composables/useTasks.js'
import { useModuleParams } from '../composables/useModuleParams.js'
import ParamBar from '../components/ParamBar.vue'
import ParamDrawer from '../components/ParamDrawer.vue'

const {
  testFile, testFramework, testFocus, testLoading, testPreview, testError, loadTestFile,
  running, runTask,
} = useTasks()

const { close: closeParams } = useModuleParams()

const summary = computed(() => {
  const f = (testFile.value || '').trim()
  return [
    { label: '目标文件', value: f || '未填', tone: f ? '' : 'todo' },
    { label: '框架', value: testFramework.value || '未选' },
    { label: '重点关注', value: (testFocus.value || '').trim() ? '已填写' : '未填（可选）' },
  ]
})

/** 预览与生成都会产出要看的东西，先收抽屉再执行 */
function onLoad() {
  closeParams('codetest')
  loadTestFile()
}
function onRun() {
  closeParams('codetest')
  runTask('codetest')
}
</script>

<template>
  <section class="panel" id="test-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>
        代码测试 · 单元测试生成
      </h2>
    </div>

    <ParamBar name="codetest" action="参数" :items="summary" />

    <!-- 过程：目标文件只读视窗 -->
    <div class="src-caption">
      <span class="src-title">目标文件内容（只读，AI 的分析依据）</span>
      <span class="src-state" :class="{ hidden: !testLoading }">读取中…</span>
      <span class="src-state ok" :class="{ hidden: testLoading || !testPreview }">已读取</span>
      <span class="src-state bad" :class="{ hidden: testLoading || !testError }">读取失败</span>
    </div>
    <pre v-if="testError" id="test-preview" class="log">{{ testError }}</pre>
    <pre v-else id="test-preview" class="log" :data-placeholder="'还没有文件内容。点右侧「修改参数」填目标文件路径后预览，或直接生成（后端会自己读文件）。'" v-html="testPreview" />

    <div class="info-note">后端会读取仓库里的目标文件（只读）交给 AI 分析；<br>生成的测试文件先落成下方「产出物」，人工采纳才写入工作区（不 commit、不 push）。</div>
  </section>

  <ParamDrawer name="codetest" title="测试参数"
               hint="路径相对仓库根；「预览文件」只读，不改任何东西。">
    <label class="field">
      <span>目标文件路径（相对仓库根）</span>
      <el-input id="test-file" v-model="testFile" placeholder="src/components/OrderList/index.tsx" />
    </label>
    <label class="field">
      <span>测试框架</span>
      <el-select id="test-framework" v-model="testFramework">
        <el-option value="vitest" label="vitest" />
        <el-option value="vitest + Testing Library" label="vitest + Testing Library" />
        <el-option value="jest" label="jest" />
        <el-option value="jest + Testing Library" label="jest + Testing Library" />
        <el-option value="cypress 组件测试" label="cypress 组件测试" />
      </el-select>
    </label>
    <label class="field">
      <span>重点关注（可选）</span>
      <el-input id="test-focus" type="textarea" :rows="4" v-model="testFocus" placeholder="例如：分页逻辑、空数据渲染、提交失败后的状态回滚" />
    </label>

    <template #footer>
      <el-button class="btn-ghost" id="btn-test-load" :disabled="testLoading"
                 title="只读预览目标文件内容" @click="onLoad">{{ testLoading ? '读取中…' : '预览文件' }}</el-button>
      <el-button type="primary" class="btn-primary" id="btn-codetest" :disabled="running" @click="onRun">生成测试代码</el-button>
    </template>
  </ParamDrawer>
</template>
