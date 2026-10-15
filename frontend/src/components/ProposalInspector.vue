<script setup>
// 提案检视器（#inspector-panel）—— master-detail 的「detail」那一半。
//
// 为什么要它：原来审一份提案要「点卡片 → 开全屏弹窗 → 看完 → 关 → 再点下一张」，
// 审 12 份就要开关 12 次，而且弹窗一开就看不见列表，看不到「这份在整批里算什么」。
// 检视器把详情常驻在队列右侧：点行即换，列表一直在旁边，采纳/拒绝就地完成，
// 键盘 J/K 走队列（见 tier 3）时选中项和右侧内容是同步的。
//
// 状态全部来自 useProposalModal 这个模块级单例 —— 与弹窗共用同一份 detail/sections/
// actions/note，所以「在检视器里采纳」和「在弹窗里采纳」走的是同一个 decide()，
// 不存在两套判定逻辑。放大成弹窗只是把 visible 打开，不会重新拉一次后端。
import { computed } from 'vue'
import { useProposalModal } from '../composables/useProposalModal.js'
import { relTime } from '../utils/format.js'
import { agentLabel, agentTone, proposalTitle, statusBadge } from '../utils/labels.js'

const props = defineProps({
  /** 队列里选中的那一行摘要（标题 / 分类 / +/- 行数都从它读，见下方注释） */
  proposal: { type: Object, default: null },
})

const emit = defineEmits(['expand'])

const {
  pinnedId, detail, sections, loading, loadError, note, forceConfirm, pendingError,
  actions, statusClass, gateHtml, preflight, decideBusy,
  onApprove, onForceApprove, onReject, onUndo, unpin,
} = useProposalModal()

const has = computed(() => !!pinnedId.value)
const d = computed(() => detail.value || {})
/** 表头一律读队列摘要（row），详情载荷（detail）只喂正文分区。
    两者都缺时退回 id，绝不显示「(无标题)」。 */
const row = computed(() => props.proposal || d.value || {})
const title = computed(() => proposalTitle(row.value) !== '(无标题)'
  ? proposalTitle(row.value)
  : ((d.value.analysis || {}).title || (d.value.defect || {}).title || row.value.id || '提案详情'))
const status = computed(() => row.value.status || d.value.status || 'pending')
// 状态归一与 ProposalCard 同一口径：后端可能回 'proposed'，展示语义是「待审批」
const st = computed(() => statusBadge(status.value === 'proposed' ? 'pending' : status.value))
const timeText = computed(() => relTime(row.value.updated || row.value.created
  || d.value.updated || d.value.created))
const fileCount = computed(() => {
  const f = row.value.files
  if (Array.isArray(f)) return f.length
  const bl = (d.value.patch || {}).blocks
  return Array.isArray(bl) ? bl.length : 0
})

/** 修复循环结论：检视器头部先给结论，轨迹在下方分区里 */
const agentText = computed(() => (row.value.agent_conclusion || (d.value.agent || {}).conclusion
  ? agentLabel(row.value.agent_conclusion || (d.value.agent || {}).conclusion).split('（')[0] : ''))
const agentToneCls = computed(() => {
  const tone = agentTone(row.value.agent_conclusion || (d.value.agent || {}).conclusion)
  return tone === 'good' ? 'ok' : tone === 'bad' ? 'er' : 'wn'
})
/** 验证强度 → 该不该信这份补丁。与队列胶囊同一判据，不另算一遍 */
const verifiedHint = computed(() => {
  const lvl = (d.value.verify || {}).status || row.value.verified_by || row.value.gate_level || ''
  const gate = (d.value.gate || {}).level || row.value.gate_level || ''
  if (lvl === 'passed' || lvl === 'repro-test') return '复现用例先跑红再修绿 —— 可信度最高'
  if (gate === 'explicit') return '真实闸门命令跑过了（失败项见下方「验收闸门」）'
  if (gate === 'degraded') return '只过了降级语法检查 —— 建议人工复核'
  if (gate === 'none') return '没有执行任何验收闸门'
  return ''
})

// 分区渲染顺序与弹窗一致（proposalRender 产出的是 HTML 串，这里只做「有没有内容」的显隐）
const SECS = [
  { key: 'analysis', id: 'is-analysis', label: '根因与判据' },
  { key: 'agent', id: 'is-agent', label: '修复循环轨迹' },
  { key: 'diff', id: 'is-diff', label: '补丁 diff' },
  { key: 'blocks', id: 'is-blocks', label: '补丁块' },
  { key: 'gate', id: 'is-gate', label: '验收闸门' },
  { key: 'issues', id: 'is-issues', label: '问题与告警' },
  { key: 'shots', id: 'is-shots', label: '页面截图' },
  { key: 'console', id: 'is-console', label: '控制台' },
]
const shown = computed(() => SECS.filter((s) => (sections.value || {})[s.key]))
</script>

<template>
  <section class="panel inspector" id="inspector-panel">
    <!-- ── 空态 ── -->
    <div v-if="!has" class="insp-empty">
      <div class="ie-mark">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
             stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" />
          <path d="M14 3v5h5" />
        </svg>
      </div>
      <div class="ie-t">从左边队列选一份提案</div>
      <div class="ie-d">
        详情会就地展开在这里 —— 列表一直在旁边，审完一份点下一份不用反复开关弹窗。<br>
        需要更大的视野时点右上角「放大」。
      </div>
    </div>

    <template v-else>
      <!-- ── 头：标题 + 判定 + 主操作 ── -->
      <header class="insp-head">
        <div class="ih-main">
          <h2 class="ih-title">{{ title }}</h2>
          <div class="ih-tags">
            <span class="ih-id">{{ row.defect_id || row.id || d.id || '' }}</span>
            <span :class="['ih-st', statusClass]">{{ st.text }}</span>
            <span class="ih-gate" v-html="gateHtml" />
            <span v-if="agentText" :class="['ih-agent', agentToneCls]">{{ agentText }}</span>
            <span class="ih-meta">{{ row.category || '—' }} · {{ row.priority || '—' }} ·
              {{ fileCount }} 个文件 ·
              <b class="add">+{{ row.added || 0 }}</b> <b class="del">−{{ row.removed || 0 }}</b> ·
              {{ timeText }}</span>
          </div>
        </div>
        <div class="ih-acts">
          <el-button size="small" text class="ih-expand" id="btn-inspect-expand"
                     title="放大为弹窗（含决策备注、一键返工、后端交接）" @click="emit('expand')">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
            </svg>
            放大
          </el-button>
          <el-button size="small" class="ih-close" id="btn-inspect-close"
                     title="取消选中（Esc）" @click="unpin()">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </el-button>
        </div>
      </header>

      <div class="insp-verdict" v-if="verifiedHint" :id="'is-verdict'">
        <span class="iv-dot" />{{ verifiedHint }}
      </div>

      <!-- ── 体：分区就地展开 ── -->
      <div class="insp-body" id="inspector-body">
        <div class="block-banner" id="is-preflight" :class="{ hidden: !preflight }" v-html="preflight" />
        <div class="pm-error" id="is-error" :class="{ hidden: !pendingError }">
          <b>后端拒绝了这个操作：</b>{{ pendingError }}
        </div>
        <div class="insp-loading" :class="{ hidden: !loading }">加载中…</div>
        <div class="pm-error" :class="{ hidden: !loadError }">详情加载失败：{{ loadError }}</div>

        <section v-for="s in shown" :key="s.id" :id="s.id" class="pm-sec insp-sec"
                 v-html="sections[s.key]" />
        <div v-if="!shown.length && !loading && !loadError" class="empty">
          这份提案没有可展示的分区内容。
        </div>
      </div>

      <!-- ── 脚：主操作常驻（备注与返工在「放大」里）── -->
      <footer class="insp-foot">
        <label class="force-line" id="is-force-line" :class="{ hidden: actions.forceLine.hidden }">
          <el-checkbox id="is-force-confirm" v-model="forceConfirm" />
          <span>我已知悉风险：验收闸门未通过，仍要把这份补丁写进工作区</span>
        </label>
        <div class="insp-actrow">
          <el-input id="is-note" class="is-note" size="small" v-model="note"
                    placeholder="决策备注（会写进提案 JSON 与知识卡片）" />
          <el-button id="is-btn-approve" type="primary" size="small"
                     :class="{ hidden: actions.approve.hidden }"
                     :disabled="decideBusy || actions.approve.disabled"
                     :title="decideBusy ? '正在处理中…' : actions.approve.title"
                     @click="onApprove()">{{ decideBusy ? '处理中…' : '采纳' }}</el-button>
          <el-button id="is-btn-force" type="danger" size="small"
                     :class="{ hidden: actions.force.hidden }"
                     :disabled="decideBusy || actions.force.disabled" :title="actions.force.title"
                     @click="onForceApprove()">强制采纳</el-button>
          <el-button id="is-btn-reject" size="small"
                     :class="{ hidden: actions.reject.hidden }"
                     :disabled="decideBusy || actions.reject.disabled" :title="actions.reject.title"
                     @click="onReject()">拒绝</el-button>
          <el-button id="is-btn-undo" size="small"
                     :class="{ hidden: actions.undo.hidden }"
                     :disabled="decideBusy || actions.undo.disabled" :title="actions.undo.title"
                     @click="onUndo()">撤销采纳</el-button>
        </div>
      </footer>
    </template>
  </section>
</template>
