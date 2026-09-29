<script setup>
// 运行结果放大弹窗（#runmodal）。旧版 index.html:1191-1199 的骨架。
// body 是纯展示 HTML（日志 / 时间线 / 结构化结果），与旧版一样整块拷贝。
//
// 组件已换成 <el-dialog>（自带 Esc 关闭、点遮罩关闭、焦点陷阱），所以这里不再需要
// 旧版那种「挂 window keydown 手动判 Esc」的绕法 —— 原生 <div> 不可聚焦才需要它。
// 外层常驻 div 承载稳定 id 与 hidden 语义，原因见 KbModal.vue 的说明。
import { useRunModal } from '../composables/useRunModal.js'

const { visible, title, bodyHtml, close } = useRunModal()
</script>

<template>
  <div id="runmodal" class="runmodal-host" :class="{ hidden: !visible }">
    <el-dialog
      class="runmodal-dialog"
      :model-value="visible"
      width="min(1240px, 96vw)"
      top="5vh"
      :show-close="false"
      @update:model-value="close()"
      @close="close()"
    >
      <template #header>
        <div class="modal-head">
          <h3><span id="runmodal-title">{{ title }}</span></h3>
          <el-button class="close" text @click="close()">×</el-button>
        </div>
      </template>

      <div id="runmodal-body" class="pm-body log runmodal-body" v-html="bodyHtml" />
    </el-dialog>
  </div>
</template>

<style scoped>
/* 旧版 .runmodal-box 是 90vh 高、正文自己滚。el-dialog 默认高度自适应，
   这里把正文交给弹窗体的 max-height，超出滚动。 */
.runmodal-body {
  max-height: 72vh;
  overflow: auto;
}
</style>
