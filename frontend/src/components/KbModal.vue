<script setup>
// 知识卡片通用弹窗（#modal）。旧版 index.html:942-951 的骨架 + app.js
// viewCard/viewPattern 的 meta 行。正文 v-html（renderMarkdown 产物，与旧版
// innerHTML 一致），站内相对链接带 data-kb-link，靠容器上的事件委托路由。
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
  <div id="modal" class="modal" :class="{ hidden: !visible }" @click.self="close()">
    <div class="modal-box" @click.stop>
      <div class="modal-head">
        <h3 id="modal-title">{{ title || '详情' }}</h3>
        <button class="close" @click="close()">×</button>
      </div>
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
    </div>
  </div>
</template>
