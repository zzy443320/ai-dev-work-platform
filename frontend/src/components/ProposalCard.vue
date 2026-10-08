<script setup>
// 单张提案卡。旧版 app.js:201 `proposalCard` 的等价复刻。
//
// 注意 .pcard-meta 里的每个 <span> 之间**没有空格**（旧版是模板串直接相邻拼接），
// 所以这里所有 span 都紧挨着写，不换行、不加空格。
import { computed } from 'vue'
import { relTime } from '../utils/format.js'
import { agentLabel, agentTone, gateBadge, proposalTitle, statusBadge } from '../utils/labels.js'

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

// 状态 / 闸门小胶囊迁移到 <el-tag>：用 el-tag 的 type 接管原来 .st-* / .gate-* 的语义色，
// 文案仍由 labels.js 产出（换框架不换口径）。状态字典与 labels.js 保持一致。
const STATUS_TAG_TYPE = {
  pending: 'warning',
  gate_failed: 'danger',
  invalid: 'info',
  applied: 'success',
  apply_failed: 'danger',
  rejected: 'info',
}
const statusType = computed(() => {
  const s = props.p.status === 'proposed' ? 'pending' : (props.p.status || 'pending')
  return STATUS_TAG_TYPE[s] || 'info'
})
const gateType = computed(() => {
  if (props.p.gate_ok === false) return 'danger'
  const map = { explicit: 'success', package_json: 'primary', degraded: 'warning', sandbox: 'success', none: 'info' }
  return map[props.p.gate_level] || 'info'
})

// 修复循环结论胶囊：列表里先告诉人「这份补丁真跑过没有」，再决定要不要点进去看轨迹。
// 读的是列表摘要字段（ProposalStore.summary 已带 agent_*），不额外拉整份提案。
const agentText = computed(() => (props.p.agent_conclusion
  ? agentLabel(props.p.agent_conclusion).split('（')[0] : ''))
const agentType = computed(() => {
  const tone = agentTone(props.p.agent_conclusion)
  return tone === 'good' ? 'success' : tone === 'bad' ? 'danger' : 'warning'
})
const agentTitle = computed(() => {
  const p = props.p
  if (!p.agent_conclusion) return ''
  return `${agentLabel(p.agent_conclusion)} · ${p.agent_rounds || 0} 轮 / `
    + `${p.agent_attempts || 0} 次候选补丁`
    + (p.sandbox_available ? '' : ' · 沙箱不可用')
})
</script>

<template>
  <!-- 卡片外层迁移到 el-card（shadow="never" 贴近原 .pcard 观感），保留 .pcard 钩子与
       状态类；body 内边距归零，避免与 .pcard 自带 padding 叠加成双重内缩。 -->
  <el-card
    shadow="never"
    class="pcard"
    :class="`pc-${p.status || 'pending'}`"
    :body-style="{ padding: '0' }"
    @click="emit('open', p.id)"
  >
    <div class="pcard-top">
      <el-tag :type="statusType" effect="light" round>{{ st.text }}</el-tag>
      <el-tag :type="gateType" effect="light" round>{{ gb.text }}</el-tag>
      <el-tag v-if="agentText" :type="agentType" effect="plain" round :title="agentTitle">{{ agentText }}</el-tag>
      <span class="pcard-id">{{ p.defect_id || p.id || '' }}</span>
      <span class="pcard-time">{{ timeText }}</span>
    </div>
    <div class="pcard-title">{{ title }}</div>
    <div class="pcard-meta"><span>{{ p.category || '—' }}</span><span>{{ p.priority || '—' }}</span><span>AI {{ p.ai_mode || '—' }}</span><span>{{ files.length }} 个文件</span><span class="dstat add">+{{ p.added || 0 }}</span><span class="dstat del">−{{ p.removed || 0 }}</span><span v-if="p.locate_empty" class="cnt warn">⚠ 未定位到文件</span><span v-if="errCount" class="cnt bad">{{ errCount }} 补丁错误</span><span v-if="warnCount" class="cnt warn">{{ warnCount }} 告警</span><span v-if="gateFailed.length" class="cnt bad">闸门失败: {{ gateFailed.join(', ') }}</span><span v-if="p.sha" class="sha">{{ String(p.sha).slice(0, 8) }}</span></div>
    <div class="pcard-root" v-if="p.root_cause">{{ p.root_cause }}</div>
  </el-card>
</template>
