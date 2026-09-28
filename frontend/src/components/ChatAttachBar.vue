<script setup>
// 待发送附件（chip 行）。旧版 app.js:3863 `chatRenderAtts` 的等价复刻。
//
// 三种 chip：图片带缩略图、文本带后缀角标、上传中带转圈。
// 旧版把 hint 的显隐也算在这块里（有附件就隐藏提示语），这里用父组件的 v-if 表达。
import { fmtSize } from '../utils/format.js'

defineProps({
  atts: { type: Array, default: () => [] },
  uploading: { type: Number, default: 0 },
})

const emit = defineEmits(['remove'])

/** 文本附件的角标：取后缀前 4 个字符，没有后缀就显示 txt。旧版同口径 */
function ext(name) {
  return (String(name || '').split('.').pop() || 'txt').slice(0, 4)
}
</script>

<template>
  <span v-for="(a, i) in atts" :key="a.id || i" class="chat-att-chip"
        :title="`${a.name} · ${fmtSize(a.size)}`">
    <img v-if="a.kind === 'image'" class="chat-att-thumb" :src="a.url"
         :alt="a.name" loading="lazy">
    <span v-else class="chat-att-icon">{{ ext(a.name) }}</span>
    <span class="chat-att-name">{{ a.name }}</span>
    <span class="chat-att-size muted">{{ fmtSize(a.size) }}</span>
    <button type="button" class="chat-att-del" title="移除这个附件"
            @click="emit('remove', i)">✕</button>
  </span>

  <span v-if="uploading" class="chat-att-chip uploading">
    <span class="chat-att-spin" />
    <span class="chat-att-name">正在上传 {{ uploading }} 个文件…</span>
  </span>
</template>
