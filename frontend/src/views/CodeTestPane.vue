<script setup>
// 代码测试页签（#pane-codetest）：目标文件 → 单元测试。
// 旧版 index.html:637-672 + app.js:1924-1940（loadTestFile）的等价迁移。
//
// 参数形态（2026-10-13 改版）：三条参数、无长文本 → **就地控件条**（strip）。
// 字段定义、范围、默认值、说明文字全部来自 /api/params/schema（web/params_schema.py），
// 模板里不再手写「标签 + 输入框」，也不再有右侧浮层抽屉与摘要 chip 条。
// 控件 id 沿用改版前契约（#test-file / #test-framework / #test-focus），
// 界面用例照旧按 id 点填。
//
// #test-preview 与 #figma-tree 同套契约：空态走 style.css 的
// `#test-preview:empty::before`（data-placeholder），所以空的时候
// <pre> 内部不能有子节点；读取失败才退回纯文本。
import { useTasks } from '../composables/useTasks.js'
import { useParamModel } from '../composables/useParamModel.js'
import ParamLayout from '../components/ParamLayout.vue'

const {
  testFile, testFramework, testFocus, testLoading, testPreview, testError, loadTestFile,
  running, runTask,
} = useTasks()

/** 交给渲染器的那三个 ref：读写都落回原 ref，payload 组装逻辑一行没改 */
const params = useParamModel({ testFile, testFramework, testFocus })
</script>

<template>
  <ParamLayout name="codetest" :model="params">
    <template #actions>
      <el-button class="btn-ghost" id="btn-test-load" :disabled="testLoading"
                 title="只读预览目标文件内容" @click="loadTestFile()">{{ testLoading ? '读取中…' : '预览文件' }}</el-button>
      <el-button type="primary" class="btn-primary" id="btn-codetest" :disabled="running" @click="runTask('codetest')">生成测试代码</el-button>
    </template>

    <section class="panel" id="test-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>
          代码测试 · 单元测试生成
        </h2>
      </div>

      <!-- 过程：目标文件只读视窗 -->
      <div class="src-caption">
        <span class="src-title">目标文件内容（只读，AI 的分析依据）</span>
        <span class="src-state" :class="{ hidden: !testLoading }">读取中…</span>
        <span class="src-state ok" :class="{ hidden: testLoading || !testPreview }">已读取</span>
        <span class="src-state bad" :class="{ hidden: testLoading || !testError }">读取失败</span>
      </div>
      <pre v-if="testError" id="test-preview" class="log">{{ testError }}</pre>
      <pre v-else id="test-preview" class="log" :data-placeholder="'还没有文件内容。在上方参数条填目标文件路径后点「预览文件」，或直接生成（后端会自己读文件）。'" v-html="testPreview" />

      <div class="info-note">后端会读取仓库里的目标文件（只读）交给 AI 分析；<br>生成的测试文件先落成下方「产出物」，人工采纳才写入工作区（不 commit、不 push）。</div>
    </section>
  </ParamLayout>
</template>
