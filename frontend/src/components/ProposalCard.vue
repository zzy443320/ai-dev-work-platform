<script setup>
// 单张提案卡。旧版 app.js:201 `proposalCard` 的等价复刻。
//
// 注意 .pcard-meta 里的每个 <span> 之间**没有空格**（旧版是模板串直接相邻拼接），
// 所以这里所有 span 都紧挨着写，不换行、不加空格。
import { computed } from 'vue'
import { relTime } from '../utils/format.js'
import { gateBadge, proposalTitle, statusBadge } from '../utils/labels.js'

const props = defineProps({
  p: { type: Object, required: true },
})

const emit = defineEmits(['open'])

const files = computed(() => props.p.files || [])
const errCount = computed(() => props.p.error_count || 0)
const warnCount = computed(() => props.p.warning_count || 0)
const gateFailed = computed(() => props.p.gate_failed || [])
const st = computed(() => statusBadge(props.p.status))
const gb = computed(() => gateBadge(props.p.gate_level, props.p.gate_ok))
const title = computed(() => proposalTitle(props.p))
const timeText = computed(() => relTime(props.p.updated || props.p.created))
</script>

<template>
  <div class="pcard" :class="`pc-${p.status || 'pending'}`" @click="emit('open', p.id)">
    <div class="pcard-top">
      <span :class="st.cls">{{ st.text }}</span>
      <span :class="gb.cls">{{ gb.text }}</span>
      <span class="pcard-id">{{ p.defect_id || p.id || '' }}</span>
      <span class="pcard-time">{{ timeText }}</span>
    </div>
    <div class="pcard-title">{{ title }}</div>
    <div class="pcard-meta"><span>{{ p.category || '—' }}</span><span>{{ p.priority || '—' }}</span><span>AI {{ p.ai_mode || '—' }}</span><span>{{ files.length }} 个文件</span><span class="dstat add">+{{ p.added || 0 }}</span><span class="dstat del">−{{ p.removed || 0 }}</span><span v-if="p.locate_empty" class="cnt warn">⚠ 未定位到文件</span><span v-if="errCount" class="cnt bad">{{ errCount }} 补丁错误</span><span v-if="warnCount" class="cnt warn">{{ warnCount }} 告警</span><span v-if="gateFailed.length" class="cnt bad">闸门失败: {{ gateFailed.join(', ') }}</span><span v-if="p.sha" class="sha">{{ String(p.sha).slice(0, 8) }}</span></div>
    <div class="pcard-root" v-if="p.root_cause">{{ p.root_cause }}</div>
  </div>
</template>
