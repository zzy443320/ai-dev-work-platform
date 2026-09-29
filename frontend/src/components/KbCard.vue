<script setup>
// 知识库卡片（模式 / 缺陷卡两种形态）。
// 旧版 app.js:788（loadPatterns 里的 pattern 卡）与 app.js:860（loadCards）的等价复刻。
import { computed } from 'vue'
import { gateBadge, statusBadge } from '../utils/labels.js'

const props = defineProps({
  item: { type: Object, required: true },
  /** 'pattern' | 'defect' */
  view: { type: String, default: 'pattern' },
})

const emit = defineEmits(['open-card', 'open-pattern', 'open-proposal'])

/** 模式卡：复发次数 ≥2 视为「hot」（该处缺护栏的信号） */
const rep = computed(() => props.item.recurrence || 0)
const isHot = computed(() => rep.value >= 2)
const modShort = computed(() => (props.item.module || '').split('/').slice(-2).join('/'))

const st = computed(() => statusBadge(props.item.status))
const gb = computed(() => (props.item.gate_level ? gateBadge(props.item.gate_level, true) : null))
const cardTime = computed(() =>
  String((props.item.updated || props.item.created) || '').slice(0, 16).replace('T', ' ')
)

// 状态 / 分类小胶囊迁移到 <el-tag>（见 ProposalCard 的同款说明）。
const STATUS_TAG_TYPE = {
  pending: 'warning', gate_failed: 'danger', invalid: 'info',
  applied: 'success', apply_failed: 'danger', rejected: 'info',
}
const statusType = computed(() => {
  const s = props.item.status === 'proposed' ? 'pending' : (props.item.status || 'pending')
  return STATUS_TAG_TYPE[s] || 'info'
})
const gateType = computed(() => {
  const map = { explicit: 'success', package_json: 'primary', degraded: 'warning', none: 'info' }
  return map[props.item.gate_level] || 'info'
})

function openProposal(ev, id) {
  // 底部的「查看提案」链接：别让点击冒泡到卡片（旧版是 onclick + stopPropagation）
  if (ev) ev.stopPropagation()
  emit('open-proposal', id)
}

function open() {
  if (props.view === 'pattern') emit('open-pattern', props.item.id)
  else emit('open-card', { category: props.item.category, id: props.item.id })
}
</script>

<template>
  <!-- 模式卡 -->
  <el-card
    v-if="view === 'pattern'"
    shadow="never"
    class="kb-card pattern"
    :class="{ hot: isHot }"
    :body-style="{ padding: '0' }"
    @click="open"
  >
    <div class="kb-meta">
      <el-tag effect="light" round>模式</el-tag>
      <span class="kb-rep" :class="{ hot: isHot }">复发 {{ rep }} 次</span>
    </div>
    <div class="kb-title">{{ item.name || item.id }}</div>
    <div class="kb-sub">{{ (item.categories || []).join('、') || '—' }}<template v-if="modShort"> · {{ modShort }}</template></div>
    <div class="kb-footer">
      <span>{{ (item.defects || []).length }} 个缺陷</span>
      <span v-if="item.applied" class="kb-status ok">已采纳 {{ item.applied }}</span>
      <span v-else class="kb-status skipped">尚无采纳案例</span>
    </div>
  </el-card>

  <!-- 缺陷卡（兜底分支，必须排在最后） -->
  <el-card
    v-else
    shadow="never"
    class="kb-card"
    :body-style="{ padding: '0' }"
    @click="open"
  >
    <div class="kb-meta">
      <el-tag effect="light" round>{{ item.category }}</el-tag>
      <span>{{ item.priority || '—' }}</span>
    </div>
    <div class="kb-title">{{ item.title || item.id }}</div>
    <div class="kb-footer">
      <span class="kb-id">{{ item.id }}</span>
      <el-tag :type="statusType" effect="light" round>{{ st.text }}</el-tag>
      <el-tag v-if="gb" :type="gateType" effect="light" round>{{ gb.text }}</el-tag>
      <span v-if="item.commit" class="sha">{{ item.commit }}</span>
      <a v-if="item.proposal_id" class="link" @click="openProposal($event, item.proposal_id)">查看提案</a>
      <span>{{ timeText }}</span>
    </div>
  </el-card>
</template>
