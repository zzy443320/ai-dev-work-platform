<script setup>
// 运行结果放大弹窗（#runmodal）。旧版 index.html:1191-1199 的骨架。
// body 是纯展示 HTML（日志 / 时间线 / 结构化结果），与旧版一样整块拷贝。
//
// Esc 关闭必须走弹窗容器上的 keydown：原生 <div> 不可聚焦，光挂 @keydown 收不到事件。
// 旧版是全局 `document.keydown` 分发到各弹窗，这里逐个弹窗自持。
import { onBeforeUnmount, onMounted } from 'vue'
import { useRunModal } from '../composables/useRunModal.js'

const { visible, title, bodyHtml, close } = useRunModal()

function onKey(e) {
  if (e.key === 'Escape' && visible.value) close()
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div id="runmodal" class="modal pmodal" :class="{ hidden: !visible }" @click.self="close()">
    <div class="modal-box pmodal-box runmodal-box" @click.stop>
      <div class="modal-head">
        <h3><span id="runmodal-title">{{ title }}</span></h3>
        <button class="close" @click="close()">×</button>
      </div>
      <div id="runmodal-body" class="pm-body log runmodal-body" v-html="bodyHtml" />
    </div>
  </div>
</template>
