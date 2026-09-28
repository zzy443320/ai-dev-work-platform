<script setup>
// 单条问答消息。旧版 app.js:4127 `chatMsgHtml` + app.js:3985 `chatMsgAtts` 的等价复刻。
//
// 为什么用 v-html 而不是拆成模板：正文是 markdown 渲染结果（表格、代码块、
// 有序列表都要保留原有结构），旧版就是这么注入的；拆成 Vue 模板反而会改变
// DOM 结构，让 #chat-stream 的文本快照对不上。转义已在 renderMarkdown 里
// 逐处做过（escapeHtml），注入面等同于旧版，没有放宽。
import { computed } from 'vue'
import { escapeHtml, fmtSize, relTime, renderMarkdown } from '../utils/format.js'

const props = defineProps({
  msg: { type: Object, required: true },
})

const isUser = computed(() => props.msg.role === 'user')
const meta = computed(() => props.msg.meta || {})
const tools = computed(() => meta.value.tools || [])
const atts = computed(() => (isUser.value ? (meta.value.attachments || []) : []))
const artifactFiles = computed(() => meta.value.artifactFiles || [])

/** 用户消息按纯文本显示（换行保留），助手消息走 markdown。与旧版同序：
 *  旧版是 `escapeHtml(content).replace(/\n/g,'<br>')`，这里先转义再补 <br>，
 *  注入面与旧版一致 —— 用户输入永远不会被当 HTML 解析。 */
const bodyHtml = computed(() => {
  if (isUser.value) {
    return escapeHtml(props.msg.content ?? '').replace(/\n/g, '<br>')
  }
  return renderMarkdown(props.msg.content || '')
})

const timeText = computed(() => relTime(props.msg.ts || ''))

/** 附件尺寸文案，模板里要用两次（title 与可见文本） */
function attTitle(a) {
  return `${a.name} · ${fmtSize(a.size)}`
}

const emit = defineEmits(['open-artifact'])
</script>

<template>
  <div class="chat-msg" :class="isUser ? 'me' : 'ai'">
    <div class="chat-role">{{ isUser ? '你' : '助手' }}<span class="chat-time">{{ timeText }}</span></div>

    <!-- 用户消息里的附件：图片显示缩略图，点开看原图；文本只显示文件名 -->
    <div class="chat-msg-atts" v-if="atts.length">
      <a v-for="(a, i) in atts" :key="i" class="chat-msg-att"
         :href="a.url" target="_blank" rel="noopener" :title="attTitle(a)">
        <img v-if="a.kind === 'image'" class="chat-msg-thumb" :src="a.url"
             :alt="a.name" loading="lazy">
        <span v-else class="chat-msg-fileicon">文件</span>
        <span class="chat-msg-attname">{{ a.name }}</span>
      </a>
    </div>

    <div class="chat-body" v-html="bodyHtml" />

    <!-- 查过仓库几次（助手消息才有）。
         注意：下面这个 <li> 的空白是刻意的 —— 旧版用模板串拼出
         `<code>名字</code> <span class="chat-args">…</span> <span class="muted">…</span>`，
         段间各有一个空格。Vue 模板会吃掉换行缩进，所以这里显式写 &#32;。
         同理 summary 的「查了仓库 N 次」也必须是一行，不能拆行。 -->
    <details class="chat-think" v-if="tools.length && !isUser">
      <summary v-text="`查了仓库 ${tools.length} 次`" />
      <ul>
        <li v-for="(t, i) in tools" :key="i"><code>{{ t.name || '' }}</code>&#32;<span
          class="chat-args">{{ JSON.stringify(t.arguments || {}) }}</span>&#32;<span
          class="muted">{{ t.chars || 0 }} 字符</span></li>
      </ul>
    </details>

    <!-- 改动提案卡片 -->
    <div class="chat-art" v-if="meta.artifact && !isUser">
      <div class="chat-art-head">已生成改动提案：{{ meta.artifactTitle || '' }}</div>
      <div class="chat-art-files">
        <!-- 旧版是 .map().join('')，文件名之间没有空白；Vue 模板会插入换行，
             故整段写成一行 -->
        <code v-for="(f, i) in artifactFiles" :key="i">{{ f }}</code>
      </div>
      <div class="chat-art-note">{{ artifactFiles.length }} 个文件 · 还没有写入仓库，采纳才会动工作区</div>
      <button class="btn-primary btn-sm" @click="emit('open-artifact', meta.artifact)">查看 / 采纳</button>
    </div>

    <div class="chat-err" v-if="meta.error && !isUser">{{ meta.error }}</div>
  </div>
</template>
