<script setup>
// 提案详情弹窗（#pmodal）。旧版 index.html:1107-1148 + app.js renderProposal。
//
// 迁移到 <el-dialog>：
//   - v-model 直接绑 composable 的 visible（解构后的 ref，模板自动解包）。
//   - 同时把 :class="{ hidden: !visible }" 透传到对话框根：#pmodal 落在 .el-dialog 上，
//     沿用旧版「用 classList 切换 + 查询 hidden」的契约（见 useProposalModal 注释）。
//   - 不使用 append-to-body（默认即内联渲染，不会把 DOM 挪到 body，用例定位稳定）。
//   - 每个分区（#pm-*）**始终渲染**，只用 :class="{ hidden: ... }" 驱动，绝不用 v-if，
//     否则旧用例 eval_on_selector("#pm-diff", ...) 会因元素不存在而抛错。
import { useProposalModal } from '../composables/useProposalModal.js'

const {
  visible, detail, sections, note, forceConfirm, pendingError,
  actions, statusText, statusClass, gateHtml, preflight,
  close, onApprove, onForceApprove, onReject, onUndo,
} = useProposalModal()
</script>

<template>
  <!-- 外层常驻 div：el-dialog 的内容体首次打开前不渲染（Element 内部有 rendered 门），
       而界面用例要在打开前断言「#pmodal 隐尵」，所以稳定 id 与 hidden 语义挂在这里。
       弹窗本身仍是 el-dialog（遮罩 / Esc / 焦点陷阱都交给它）。 -->
  <div id="pmodal" class="pmodal-host" :class="{ hidden: !visible }">
  <el-dialog
    v-model="visible"
    class="pmodal"
    width="min(1240px, 96vw)"
    :show-close="false"
    :close-on-click-modal="true"
    :close-on-press-escape="true"
    :body-style="{ padding: '0', display: 'flex', flexDirection: 'column', flex: '1', minHeight: '0' }"
    @close="close"
  >
    <template #header>
      <!-- ⚠ header 槽内容必须包在一层 flex 容器里：.el-dialog__header 本身是
           纯 block（Element 构建产物实测），两个顶层节点（h3 + 关闭按钮）会让
           × 掉到第二行左侧（用户实测截图）。其余五个弹窗都有 .modal-head 包裹，
           这里对齐成 .pm-head。 -->
      <div class="pm-head">
        <h3>
          <span id="pmodal-title">{{ (detail && detail.id) || '提案详情' }}</span>
          <span id="pmodal-status" :class="[statusClass, { hidden: !detail }]">{{ statusText }}</span>
          <span id="pmodal-gate" v-html="gateHtml" />
        </h3>
        <!-- 用例会 page.click("#pmodal .close")：保留一个 class 为 close 的关闭元素 -->
        <el-button class="close" text @click="close()">×</el-button>
      </div>
    </template>

    <div id="pmodal-meta" class="modal-meta" v-html="(sections && sections.meta) || ''" />
    <div class="pm-body">
      <div class="block-banner" id="pm-preflight" :class="{ hidden: !preflight }" v-html="preflight" />
      <div class="pm-error" id="pm-error" :class="{ hidden: !pendingError }">
        <b>后端拒绝了这个操作：</b>{{ pendingError }}
      </div>
      <section class="pm-sec" id="pm-analysis" v-html="(sections && sections.analysis) || ''" />
      <section class="pm-sec" id="pm-agent" v-html="(sections && sections.agent) || ''" />
      <section class="pm-sec" id="pm-diff" v-html="(sections && sections.diff) || ''" />
      <section class="pm-sec" id="pm-blocks" v-html="(sections && sections.blocks) || ''" />
      <section class="pm-sec" id="pm-gate" v-html="(sections && sections.gate) || ''" />
      <section class="pm-sec" id="pm-issues" v-html="(sections && sections.issues) || ''" />
      <section class="pm-sec" id="pm-shots" v-html="(sections && sections.shots) || ''" />
      <section class="pm-sec" id="pm-console" v-html="(sections && sections.console) || ''" />
    </div>

    <div class="pm-foot">
      <label class="field">
        <span>决策备注（会写进提案 JSON 与知识卡片）</span>
        <el-input id="pm-note" type="textarea" :rows="2" v-model="note"
                  placeholder="采纳理由 / 拒绝原因 / 需要 AI 重跑时改什么" />
      </label>
      <label class="force-line" id="force-line" :class="{ hidden: actions.forceLine.hidden }">
        <el-checkbox id="force-confirm" v-model="forceConfirm" />
        <span>我已知悉风险：验收闸门未通过，仍要把这份补丁写进工作区</span>
      </label>
      <div class="pm-actions" id="pm-actions">
        <el-button id="btn-approve" type="primary" :class="{ hidden: actions.approve.hidden }"
                   :disabled="actions.approve.disabled" :title="actions.approve.title" @click="onApprove()">采纳</el-button>
        <el-button id="btn-force" type="danger" :class="{ hidden: actions.force.hidden }"
                   :disabled="actions.force.disabled" :title="actions.force.title" @click="onForceApprove()">强制采纳</el-button>
        <el-button id="btn-reject" :class="{ hidden: actions.reject.hidden }"
                   :disabled="actions.reject.disabled" :title="actions.reject.title" @click="onReject()">拒绝</el-button>
        <el-button id="btn-undo" :class="{ hidden: actions.undo.hidden }"
                   :disabled="actions.undo.disabled" :title="actions.undo.title" @click="onUndo()">撤销采纳</el-button>
        <el-button @click="close()">关闭</el-button>
      </div>
    </div>
  </el-dialog>
  </div>
</template>

<style scoped>
/* 复刻旧版 .pmodal-box 的纵向弹性布局：头部固定、正文滚动、底部操作区贴底。
   .pmodal 落在 .el-dialog 根上（透传），给它限高 + 纵向 flex。 */
.pmodal {
  max-height: 92vh;
  display: flex;
  flex-direction: column;
}
/* header 槽自带 flex 布局（.el-dialog__header 是纯 block，靠 :deep 改不动的
   原因见上方模板注释），标题块居左、关闭按钮贴右上角 */
.pm-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
h3 {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 14px;
  font-weight: 700;
  margin: 0;
}
/* 复刻旧版 .modal-head .close 的视觉（透明、悬浮高亮），脱离 .modal-head 后单独立样式 */
.close {
  background: transparent;
  border: none;
  color: var(--muted);
  font-size: 20px;
  line-height: 1;
  padding: 0 4px;
  height: auto;
}
.close:hover {
  background: var(--border);
  color: var(--text);
}
</style>
