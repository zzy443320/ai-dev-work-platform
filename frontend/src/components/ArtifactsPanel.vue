<script setup>
// 产出物面板（#artifact-panel）。挂在右侧栏，随页签切换显示/隐藏并按类型拉列表。
// 旧版 index.html:799-811 + app.js:2004-2060 的等价迁移。
// 注意 h2 里的结构：图标 + #artifact-type-label + 「（AI 生成，未写入仓库）」+ #artifact-count，
// 与旧版逐字一致（文案顺序变了截图/用例都会对不上）。
import { computed } from 'vue'
import { useArtifacts } from '../composables/useArtifacts.js'
import { artifactStatusText } from '../utils/labels.js'

const props = defineProps({
  /** 当前页签对应的产出物类型标签（旧版 ARTIFACT_TAB_LABEL[name]） */
  label: { type: String, default: '' },
  /** 面板是否显示（旧版靠 hidden class 切换） */
  visible: { type: Boolean, default: false },
  /** id 后缀：问答页签把面板收进了自己的浮层抽屉（ChatPane），页面上可能同时存在
   *  两个实例（el-tab-pane 切走不卸载）——同 id 会让按 id 取元素的用例/脚本拿到
   *  错的那份。浮层实例传 idSuffix，id 变成 `artifact-panel-<suffix>` 等。 */
  idSuffix: { type: String, default: '' },
  /** 列表为空时的引导文案。默认那句是四个任务页签的口径（表单搬进右侧参数抽屉后，
   //  指向改成「作业参数」抽屉，不再说「左侧」），
   *  问答页签没有这个动作，另外传一句（2026-09-30）。 */
  emptyHint: {
    type: String,
    default: '还没有产出物。在右侧「参数」抽屉里填好信息点生成，AI 结果会在这里等你审阅采纳。',
  },
})

const emit = defineEmits(['open'])

const { sorted, error, badge, timeText, loadArtifacts } = useArtifacts()

const typeLabel = computed(() => props.label || '产出物')
/** 带 idSuffix 时生成唯一 id，否则保持原 id（既有用例的契约） */
const pid = (base) => (props.idSuffix ? `${base}-${props.idSuffix}` : base)
</script>

<template>
  <section v-show="visible" class="panel" :id="pid('artifact-panel')"
           :class="{ hidden: !visible }">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l9 5v10l-9 5-9-5V7Z" /><path d="M12 12l9-5M12 12v10M12 12L3 7" /></svg>
        <span :id="pid('artifact-type-label')">{{ typeLabel }}</span>（AI 生成，未写入仓库）
        <span class="count-badge" :class="{ alert: badge.alert, hidden: badge.hidden }"
              :id="pid('artifact-count')">{{ badge.text }}</span>
      </h2>
    </div>
    <div :id="pid('artifacts')" class="proposal-grid">
      <div v-if="error" class="empty">产出物加载失败: {{ error }}</div>
      <div v-else-if="!sorted.length" class="empty">{{ emptyHint }}</div>
      <el-card shadow="never" v-for="a in sorted" :key="a.id" class="pcard" :class="`pc-${a.status || 'pending'}`"
               @click="emit('open', a.id)">
        <div class="pcard-top">
          <span class="st" :class="`st-${a.status || 'pending'}`">{{ artifactStatusText(a.status) }}</span>
          <span class="pcard-id">{{ a.id || '' }}</span>
          <span class="pcard-time">{{ timeText(a) }}</span>
        </div>
        <div class="pcard-title">{{ a.title || '(无标题)' }}</div>
        <div class="pcard-meta">
          <span>{{ a.type_label || a.type || '—' }}</span>
          <span>AI {{ a.ai_mode || '—' }}</span>
          <span>{{ (a.files || []).length }} 个文件</span>
        </div>
        <div v-if="a.error" class="pcard-root" style="color:var(--danger)">{{ a.error }}</div>
        <div v-else-if="a.summary" class="pcard-root">{{ a.summary }}</div>
      </el-card>
    </div>
    <p class="panel-note">采纳只会把文件写入工作区（不 commit、不 push）；覆盖已有文件前会自动备份，可在详情里撤销。</p>
  </section>
</template>
