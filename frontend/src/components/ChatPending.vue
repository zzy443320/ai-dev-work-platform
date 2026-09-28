<script setup>
// 生成中的那条助手消息（草稿）。旧版 app.js:4184 `chatAddPending` +
// app.js:4198 `chatRenderPending` 的等价复刻。
//
// 旧版是「先往 #chat-stream 里 append 一个元素，后面靠 querySelector 就地改它」。
// Vue 里改成一条由状态驱动的虚拟消息：形态与已落盘的消息一致，
// 只是多一个闪烁光标、且折叠块按内容出现。
import { computed } from 'vue'
import { renderMarkdown } from '../utils/format.js'

const props = defineProps({
  think: { type: String, default: '' },
  out: { type: String, default: '' },
  tools: { type: Array, default: () => [] },
})

const hasThink = computed(() => !!props.think)
const hasTools = computed(() => props.tools.length > 0)
/** 和旧版一致：正文为空时显示省略号，而不是留空白 */
const bodyHtml = computed(() => (props.out ? renderMarkdown(props.out) : ''))
const hasBody = computed(() => !!props.out)
</script>

<template>
  <div class="chat-msg ai pending">
    <div class="chat-role">助手<span class="chat-time">生成中…</span></div>

    <details class="chat-think" :class="{ hidden: !hasThink }">
      <summary>思考过程</summary>
      <pre>{{ think }}</pre>
    </details>

    <details class="chat-think chat-live-tools" :class="{ hidden: !hasTools }">
      <summary>查仓库</summary>
      <ul>
        <!-- 与旧版逐字一致：`<code>名字</code> {json}`，中间一个空格 -->
        <li v-for="(t, i) in tools" :key="i"><code>{{ t.name || '' }}</code>&#32;{{ JSON.stringify(t.arguments || {}) }}</li>
      </ul>
    </details>

    <div class="chat-body">
      <template v-if="hasBody">
        <span v-html="bodyHtml" /><span class="chat-caret" />
      </template>
      <span v-else class="muted">…</span>
    </div>
  </div>
</template>
