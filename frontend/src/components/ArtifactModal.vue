<script setup>
// 产出物详情弹窗（#amodal）。旧版 index.html:1150-1189 + app.js:2093-2321
// （renderArtifact / renderArtifactFiles / renderAActions / aDecide）的等价迁移。
//
// 结构契约：id 与旧版一致（#amodal-meta / #am-preflight / #am-error / #am-summary /
// #am-plan / #am-cases / #am-files / #am-checklist / #am-mock / #am-note /
// #am-force-line / #am-actions / #a-btn-approve…）。
//
// 渲染差异说明：旧版把每个 section 用 innerHTML 拼 HTML 串；Vue 里改成结构化
// 模板 —— 文案与层级保持一致（h4 标题、.pm-line、.diff、.dl.blk 等 class 原样）。
// checklist 里的 **加粗** 走 mdBold 的 v-html（与旧版同一条语法）。
// diff 行的 <span> 必须紧挨着写（换行在字符串里，模板里多加空白会破坏 pre 布局）。
//
// 弹窗显隐：用 <el-dialog> 的 v-model 绑 composable 的 modalVisible，同时对根元素
// 绑 :class="{ hidden: !modalVisible }" —— 用例断言的是 #amodal 上的 hidden class。
// 不 append-to-body，保证 #amodal 始终在 DOM 里。关闭走 .close 按钮与遮罩，
// 不再需要原版的 onBackdrop。
import { computed } from 'vue'
import { useArtifacts } from '../composables/useArtifacts.js'
import { artifactStatusText, mdBold } from '../utils/labels.js'
import { relTime } from '../utils/format.js'

const {
  modalVisible, detail, fileFullView, pendingError, note, forceConfirm, deciding,
  fileViews, actions, closeModal, onApprove, onForceApprove, onReject, onUndo,
} = useArtifacts()

const emit = defineEmits(['close'])

/** 弹窗头部 meta 行。旧版 renderArtifact 里的 amodal-meta innerHTML */
const metaLines = computed(() => {
  const a = detail.value
  if (!a) return []
  const pre = a.preflight_live || {}
  const out = []
  const add = (k, v) => { if (v) out.push({ k, v: String(v) }) }
  add('类型', a.type_label)
  add('技术栈', (a.context || {}).framework)
  add('AI', a.ai_mode)
  add('分支', `${pre.current_branch || '未知'}${pre.expected_branch ? ` → 期望 ${pre.expected_branch}` : ''}`)
  add('工作区', pre.dirty ? '有未提交改动' : '干净')
  add('创建', relTime(a.created))
  return out
})
const branchBad = computed(() => {
  const a = detail.value
  if (!a) return false
  const pre = a.preflight_live || {}
  return !!(pre.current_branch && pre.expected_branch && pre.current_branch !== pre.expected_branch)
})
const preflightProblems = computed(() =>
  (((detail.value || {}).preflight_live || {}).problems || []).filter(Boolean))

/** 点拒绝：与旧版同款 confirm（测试里 page.on("dialog") 接住） */
function reject() {
  if (!window.confirm('确认拒绝该产出物？\n不会写任何文件，仅标记为 rejected。')) return
  onReject()
}
/** 点撤销：同款 confirm，列出涉及文件 */
function undo() {
  const files = ((detail.value || {}).apply || {}).files || []
  const list = files.length ? `\n涉及文件：\n${files.map((f) => '· ' + f).join('\n')}` : ''
  if (!window.confirm(`确认撤销采纳？\n会把写入的文件恢复为采纳前的内容（本次新建的文件会被删除）。${list}`)) return
  onUndo()
}
</script>

<template>
  <!-- 弹窗根换成 <el-dialog>：v-model 绑 modalVisible，根元素再绑 hidden class。
       不 append-to-body，保证 #amodal 始终在 DOM（用例靠 #amodal 上的 hidden 判定）。
       #header 插槽放原来的 .modal-head（标题 + 状态 + .close 按钮）。 -->
  <!-- 外层常驻 div：el-dialog 的内容体首次打开前不渲染（Element 内部有 rendered 门），
       用例要在打开前/关闭后按 id 断言 hidden，所以稳定 id 与 hidden 语义挂在这里。 -->
  <div id="amodal" class="amodal-host" :class="{ hidden: !modalVisible }">
  <el-dialog v-model="modalVisible"
             :append-to-body="false" :show-close="false"
             width="min(1000px, 96vw)">
    <template #header>
      <div class="modal-head">
        <h3>
          <span id="amodal-title">{{ detail && detail.id || '产出物详情' }}</span>
          <span class="st" :class="[`st-${(detail && detail.status) || 'pending'}`, { hidden: !detail }]"
                id="amodal-status">{{ detail ? artifactStatusText(detail.status) : '' }}</span>
        </h3>
        <el-button text circle class="close" @click="closeModal">×</el-button>
      </div>
    </template>

    <template v-if="detail">
      <div id="amodal-meta" class="modal-meta">
        <span v-for="(m, i) in metaLines" :key="i"><b>{{ m.k }}:</b> {{ m.v }}</span>
        <span v-if="(detail.context || {}).skills_used && (detail.context.skills_used).length"><b>技能:</b> {{ (detail.context.skills_used).join('、') }}</span>
        <span v-if="(detail.context || {}).tools_used && (detail.context.tools_used).length"><b>MCP 工具:</b> {{ (detail.context.tools_used).join('、') }}</span>
        <span v-if="branchBad" class="cnt bad">分支与配置不一致，采纳会被拒</span>
      </div>
      <div class="pm-body">
        <div class="block-banner" :class="{ hidden: !preflightProblems.length }" id="am-preflight">
          <template v-if="preflightProblems.length">
            <b>仓库预检未通过 —— 现在点采纳一定会被后端拒绝：</b>
            <ul><li v-for="(p, i) in preflightProblems" :key="i">{{ p }}</li></ul>
            <span class="muted">修好这些（如切换到配置的分支）后重新打开即可重试。</span>
          </template>
        </div>
        <div class="pm-error" :class="{ hidden: !pendingError }" id="am-error">
          <template v-if="pendingError"><b>后端拒绝了这个操作：</b>{{ pendingError }}</template>
        </div>

        <section class="pm-sec" id="am-summary">
          <h4>摘要</h4>
          <div class="pm-line"><b>说明</b><span>{{ detail.summary || '（空）' }}</span></div>
          <div v-if="detail.error" class="pm-line bad"><b>AI 输出问题</b><span>{{ detail.error }}</span></div>
        </section>

        <section v-if="detail.plan" class="pm-sec" id="am-plan">
          <h4>实现 / 联调计划</h4>
          <pre class="diff">{{ detail.plan }}</pre>
        </section>

        <section v-if="(detail.cases || []).length" class="pm-sec" id="am-cases">
          <h4>测试用例（{{ (detail.cases || []).length }}）</h4>
          <ul class="check-list">
            <li v-for="(c, i) in detail.cases" :key="i" class="ok">
              <div class="check-head"><b>{{ c.name || '' }}</b><span>{{ c.purpose || '' }}</span></div>
            </li>
          </ul>
        </section>

        <section class="pm-sec" id="am-files">
          <h4>将写入的文件（{{ (detail.files || []).length }}）<template v-if="fileViews.hasDiff"><span v-if="fileViews.applied" class="muted" style="font-weight:400">已采纳，内容与工作区一致</span><el-button class="am-toggle-btn" id="am-file-view-toggle" @click="fileFullView = !fileFullView">{{ fileFullView ? '查看改动高亮' : '查看完整文件' }}</el-button></template></h4>
          <div v-if="fileViews.showDiff" class="muted" style="margin:2px 0 6px;font-size:12px">高亮说明：<span class="dl add" style="padding:0 4px">绿底 = 新增行</span> · <span class="dl del" style="padding:0 4px">红底 = 被删除/替换的旧行</span> · 未变的行只展示改动前后各 3 行上下文，其余折叠。</div>
          <template v-if="fileViews.list.length">
            <template v-for="f in fileViews.list" :key="f.path">
              <div class="diff-file"><span class="df-path">{{ f.path }}</span><span class="tag" :title="f.isNew ? '仓库中不存在该文件，采纳时创建；以下所有行均为新增' : (f.mode === 'edit' ? '只替换 find 命中的片段，文件其余部分保持原样' : '')">{{ f.mode === 'edit' ? '局部替换' : (f.isNew ? '新建' : '覆盖已有') }}</span><template v-if="f.added || f.removed"><span class="df-stat df-stat-add">+{{ f.added }}</span><span class="df-stat df-stat-del">−{{ f.removed }}</span></template><span class="muted">{{ f.lineCount }} 行</span></div>
              <div v-if="f.desc" class="muted" style="margin:2px 0 4px">{{ f.desc }}</div>
              <pre v-if="f.lines" class="diff diff-lines"><span v-for="(l, i) in f.lines" :key="i" class="dl blk" :class="l.cls">{{ l.s }}</span></pre>
              <pre v-else class="diff">{{ f.content }}</pre>
            </template>
          </template>
          <p v-else class="muted">没有文件内容。</p>
        </section>

        <section v-if="(detail.checklist || []).length" class="pm-sec" id="am-checklist">
          <h4>Checklist</h4>
          <ul class="note-list">
            <li v-for="(c, i) in detail.checklist" :key="i" v-html="mdBold(c)" />
          </ul>
        </section>

        <section v-if="detail.mock_data" class="pm-sec" id="am-mock">
          <h4>Mock 数据</h4>
          <pre class="diff">{{ detail.mock_data }}</pre>
        </section>
      </div>
      <div class="pm-foot">
        <label class="field">
          <span>决策备注（采纳理由 / 拒绝原因）</span>
          <el-input type="textarea" id="am-note" :rows="2" v-model="note" placeholder="备注会记录在产出物 JSON 里" />
        </label>
        <label class="force-line" id="am-force-line" :class="{ hidden: !actions.force }">
          <input type="checkbox" id="am-force-confirm" v-model="forceConfirm">
          <span class="force-box" />
          <span>我已知悉风险：验收闸门未通过，仍要把这些文件写进工作区</span>
        </label>
        <div class="pm-actions" id="am-actions">
          <!-- 四个按钮始终在 DOM 里（靠 .hidden class 切换，display:none!important），
               与旧版 renderAActions 的 classList.toggle('hidden') 完全一致：
               界面用例断言的是 hidden class，不是内联 style。 -->
          <el-button type="primary" id="a-btn-approve"
                     :class="{ hidden: !(actions.approve || actions.approveDisabled) }"
                     :disabled="deciding || actions.approveDisabled" :title="actions.approveTitle"
                     @click="onApprove()">采纳（写入工作区）</el-button>
          <el-button type="danger" id="a-btn-force" :class="{ hidden: !actions.force }"
                     :disabled="deciding" @click="onForceApprove()">强制采纳</el-button>
          <el-button id="a-btn-reject" :class="{ hidden: !actions.reject }"
                     :disabled="deciding" @click="reject">拒绝</el-button>
          <el-button id="a-btn-undo" :class="{ hidden: !actions.undo }"
                     :disabled="deciding" :title="actions.undoTitle" @click="undo">撤销采纳</el-button>
          <el-button :disabled="deciding" @click="closeModal()">关闭</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
  </div>
</template>
