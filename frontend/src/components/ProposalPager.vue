<script setup>
// 提案分页条。旧版 app.js:185 `renderPropPager` 的等价复刻。
import { computed } from 'vue'

const props = defineProps({
  info: { type: Object, required: true },   // { total, from, to }
  nums: { type: Array, default: () => [] }, // [1, '…', 3, 4, 5, '…', 9]
  current: { type: Number, default: 1 },
  totalPages: { type: Number, default: 1 },
})

const emit = defineEmits(['goto'])

const canPrev = computed(() => props.current > 1)
const canNext = computed(() => props.current < props.totalPages)
</script>

<template>
  <div class="prop-pager">
    <span class="pg-info">共 {{ info.total }} 条 · 第 {{ info.from }}-{{ info.to }} 条</span>
    <div class="pg-btns">
      <button class="pg-num" title="上一页" :disabled="!canPrev"
              @click="emit('goto', current - 1)">‹</button>
      <template v-for="(n, i) in nums" :key="i">
        <span v-if="n === '…'" class="pg-dots">…</span>
        <button v-else class="pg-num" :class="{ active: n === current }"
                @click="emit('goto', n)">{{ n }}</button>
      </template>
      <button class="pg-num" title="下一页" :disabled="!canNext"
              @click="emit('goto', current + 1)">›</button>
    </div>
  </div>
</template>
