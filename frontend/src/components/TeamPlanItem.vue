<script setup>
// 单条工作项。旧版 app.js:3297 `teamRenderPlan` 里的 item 模板复刻。
import { computed } from 'vue'

const props = defineProps({
  item: { type: Object, required: true },
  /** TEAM_ITEM_STATUS[status] */
  statusText: { type: String, default: '' },
})

const files = computed(() => props.item.files || [])

function shortPath(p) {
  const parts = String(p || '').split('/')
  return parts.length <= 2 ? String(p || '') : parts.slice(-2).join('/')
}
</script>

<template>
  <div class="team-item" :class="item.status || 'todo'">
    <div class="team-item-head">
      <span class="team-item-id">{{ item.id || '' }}</span>
      <span class="team-item-title">{{ item.title || '' }}</span>
      <span class="team-item-st">{{ statusText }}</span>
    </div>
    <div v-if="item.detail" class="team-item-detail">{{ item.detail }}</div>
    <div v-if="files.length" class="team-item-files">
      <span v-for="f in files" :key="f" class="team-file-chip">{{ shortPath(f) }}</span>
    </div>
    <div v-if="item.acceptance" class="team-item-acc">验收：{{ item.acceptance }}</div>
    <div v-if="item.note" class="team-item-note">{{ item.note }}</div>
  </div>
</template>
