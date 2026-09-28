<script setup>
// 提案详情弹窗（#pmodal）。旧版 index.html:1107-1148 + app.js renderProposal。
//
// 契约：所有分区（#pm-analysis/#pm-diff/#pm-blocks/#pm-gate/#pm-issues/
// #pm-shots/#pm-console）与操作按钮（#btn-approve/#btn-force/#btn-reject/
// #btn-undo/#force-line/#pm-error/#pm-preflight）**始终渲染**，动态部分用
// :class="{ hidden: ... }" 驱动 —— 旧版用 classList 切换，v-if 会让元素
// 消失导致旧用例的查询抛错（ArtifactModal 里踩过的坑）。
import { onBeforeUnmount, onMounted } from 'vue'
import { useProposalModal } from '../composables/useProposalModal.js'

const {
  visible, detail, sections, note, forceConfirm, pendingError,
  actions, statusText, statusClass, gateHtml, preflight,
  close, onApprove, onForceApprove, onReject, onUndo,
} = useProposalModal()

/**
 * Esc 关闭。旧版是 `document.addEventListener('keydown')` 全局分发；
 * 原生 <div> 不可聚焦，所以不能靠元素自身的按键事件，必须挂在 window 上。
 */
function onKey(e) {
  if (e.key === 'Escape' && visible.value) close()
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div id="pmodal" class="modal pmodal" :class="{ hidden: !visible }" @click.self="close()">
    <div class="modal-box pmodal-box" @click.stop>
      <div class="modal-head">
        <h3>
          <span id="pmodal-title">{{ (detail && detail.id) || '提案详情' }}</span>
          <span id="pmodal-status" :class="[statusClass, { hidden: !detail }]">{{ statusText }}</span>
          <span id="pmodal-gate" v-html="gateHtml" />
        </h3>
        <button class="close" @click="close()">×</button>
      </div>
      <div id="pmodal-meta" class="modal-meta" v-html="(sections && sections.meta) || ''" />
      <div class="pm-body">
        <div class="block-banner" id="pm-preflight" :class="{ hidden: !preflight }" v-html="preflight" />
        <div class="pm-error" id="pm-error" :class="{ hidden: !pendingError }">
          <b>后端拒绝了这个操作：</b>{{ pendingError }}
        </div>
        <section class="pm-sec" id="pm-analysis" v-html="(sections && sections.analysis) || ''" />
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
          <textarea id="pm-note" rows="2" v-model="note" placeholder="采纳理由 / 拒绝原因 / 需要 AI 重跑时改什么" />
        </label>
        <label class="force-line" id="force-line" :class="{ hidden: actions.forceLine.hidden }">
          <input type="checkbox" id="force-confirm" v-model="forceConfirm">
          <span class="force-box" />
          <span>我已知悉风险：验收闸门未通过，仍要把这份补丁写进工作区</span>
        </label>
        <div class="pm-actions" id="pm-actions">
          <button class="btn-primary" id="btn-approve" :class="{ hidden: actions.approve.hidden }"
                  :disabled="actions.approve.disabled" :title="actions.approve.title" @click="onApprove()">采纳</button>
          <button class="btn-danger" id="btn-force" :class="{ hidden: actions.force.hidden }"
                  :disabled="actions.force.disabled" :title="actions.force.title" @click="onForceApprove()">强制采纳</button>
          <button class="btn-ghost" id="btn-reject" :class="{ hidden: actions.reject.hidden }"
                  :disabled="actions.reject.disabled" :title="actions.reject.title" @click="onReject()">拒绝</button>
          <button class="btn-ghost" id="btn-undo" :class="{ hidden: actions.undo.hidden }"
                  :disabled="actions.undo.disabled" :title="actions.undo.title" @click="onUndo()">撤销采纳</button>
          <button class="btn-ghost" @click="close()">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>
