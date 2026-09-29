<script setup>
// 知识卡片通用弹窗（#modal）。旧版 index.html:942-951 的骨架 + app.js
// viewCard/viewPattern 的 meta 行。正文 v-html（renderMarkdown 产物，与旧版
// innerHTML 一致），站内相对链接带 data-kb-link，靠容器上的事件委托路由。
//
// 组件已换成 <el-dialog>；外层那个极简 div 是**刻意的**：el-dialog 的内容体在
// 首次打开前根本不渲染，而界面用例要在打开前断言 `#modal` 存在且带 hidden 类。
// 所以把稳定 id 与 hidden 语义挂在常驻节点上，el-dialog 只管渲染与交互。
//
// ⚠ 这个壳的 bounding box 高度恒为 0：el-dialog 的 `.el-overlay` 是
// position:fixed，不参与父盒计算。所以**别用「宿主可见」判断弹窗打开了**
// （Playwright 只认「盒子非空 + 非 visibility:hidden」，会一直判它不可见）；
// 要等就等 `#modal .el-dialog`，或者像 check_vue_defect_ui 那样读 hidden 类。
import { useKbModal } from '../composables/useKbModal.js'

const { visible, title, metaRows, bodyHtml, openCard, openDoc, close } = useKbModal()

/** 正文里的站内相对链接下钻（旧版是内联 onclick="openKbDoc(...)"） */
function onBodyClick(e) {
  const a = e.target && e.target.closest ? e.target.closest('a[data-kb-link]') : null
  if (!a) return
  e.preventDefault()
  openDoc(a.getAttribute('data-kb-link'))
}
</script>

<template>
  <div id="modal" class="kbmodal-host" :class="{ hidden: !visible }">
    <el-dialog
      :model-value="visible"
      width="min(980px, 94vw)"
      :show-close="false"
      @update:model-value="close()"
      @close="close()"
    >
      <template #header>
        <div class="modal-head">
          <h3 id="modal-title">{{ title || '详情' }}</h3>
          <el-button class="close" text @click="close()">×</el-button>
        </div>
      </template>

      <div id="modal-meta" class="modal-meta">
        <span v-for="(r, i) in metaRows" :key="i">
          <template v-if="r.k">
            <b>{{ r.k }}:</b> {{ r.v }}
          </template>
          <template v-if="r.chips">
            <b>{{ r.k }}:</b>
            <template v-for="c in r.chips" :key="c.category + '/' + c.id">
              <a class="link" @click="openCard(c.category, c.id)">{{ c.id }}</a>
            </template>
          </template>
        </span>
      </div>
      <div id="modal-body" class="modal-body" v-html="bodyHtml" @click="onBodyClick" />
    </el-dialog>
  </div>
</template>
