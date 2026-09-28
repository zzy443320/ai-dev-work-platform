<script setup>
// 会话 chip 列表（横向可滚）。旧版 app.js:4046 `chatRenderSessions` 的等价复刻。
// 顺序由后端按 updated 倒序给出，前端不重排。
import { relTime } from '../utils/format.js'

defineProps({
  sessions: { type: Array, default: () => [] },
  current: { type: String, default: '' },
  error: { type: String, default: '' },
})

const emit = defineEmits(['open'])
</script>

<template>
  <div class="chat-sessions" id="chat-sessions">
    <span v-if="error" class="muted">会话列表加载失败：{{ error }}</span>
    <span v-else-if="!sessions.length" class="muted">还没有会话 —— 直接在下面提问会自动建一个。</span>
    <button v-for="s in sessions" :key="s.id" class="chat-chip"
            :class="{ active: s.id === current }"
            :title="s.title" @click="emit('open', s.id)">
      <span class="chat-chip-title">{{ s.title || '新会话' }}</span>
      <span class="chat-chip-meta">{{ s.count || 0 }} 条 · {{ relTime(s.updated) }}</span>
    </button>
  </div>
</template>
