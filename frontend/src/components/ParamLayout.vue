<script setup>
// 任务模块的**参数布局外壳**：按 schema 的分档结果决定用哪种形态，
// 模块自己只写「主区放什么、动作按钮放什么」。
//
//     <ParamLayout name="codetest" :model="params">
//       <template #actions>…运行 / 预览按钮…</template>
//       …主区面板（过程与产物）…
//     </ParamLayout>
//
// ⚠️ 这个组件**刻意不包一层 div**（多根 fragment）。各页签的多列布局是
// `#pane-team > section:nth-of-type(n) { grid-area: … }` 这种**子代选择器**排的
// （desk.css 第 13/14 节），中间插一层元素就会让面板掉回一列 —— 实测把六面板
// 挤成 459px 窄条、右边空出 559px。所以这里直接把「参数条 / 主区面板 / 参数轨」
// 作为页签 grid 的同级子节点交出去，轨用 CSS 钉在最后一列（见 params.css 第 3 节）。
//
// 分档口径（web/params_schema.py 的 layout_of）：
//   * strip —— 参数 ≤6 且没有 rows>=8 的长文本：参数常驻页签顶部一行（就地控件条）；
//   * rail  —— 参数 >6 或含长文本：参数轨作为页签 grid 的最后一列。
// 两种形态都没有浮层、没有遮罩、没有主列让位。
import { computed } from 'vue'
import { paramLayout, paramModule } from '../composables/useParamSchema.js'
import ParamStrip from './ParamStrip.vue'
import ParamRail from './ParamRail.vue'

const props = defineProps({
  name: { type: String, required: true },
  /** useParamModel() 的返回值 */
  model: { type: Object, required: true },
  /** 标签解析器 (field, option) => string，透传给渲染器（角色名以后端为准时用） */
  optLabel: { type: Function, default: null },
})

// 三个插槽：
//   default  —— 主区：过程与产物的各块面板
//   actions  —— 常驻动作（运行 / 预览 / 刷新），跟着形态放到参数条行尾或轨底
//   notes    —— 模块专属解释文字，放在参数区里（离参数近才有人看）

const mod = computed(() => paramModule(props.name))
const rail = computed(() => !!mod.value && paramLayout(props.name) === 'rail')
</script>

<template>
  <!-- schema 没到位（正常不会发生：main.js 先拉后挂载）：只交主区，不渲染参数区 -->
  <slot v-if="!mod" />

  <!-- A 就地控件条：参数条在前，主区面板跟着排，两者都是页签 grid 的直接子节点 -->
  <template v-else-if="!rail">
    <ParamStrip :name="name" :model="model" :opt-label="optLabel">
      <template #actions><slot name="actions" /></template>
      <template #notes><slot name="notes" /></template>
    </ParamStrip>
    <slot />
  </template>

  <!-- B 常驻参数轨：主区面板先排，轨钉在最后一列、跨满所有行 -->
  <template v-else>
    <slot />
    <ParamRail :name="name" :model="model" :opt-label="optLabel">
      <template #actions><slot name="actions" /></template>
      <template #notes><slot name="notes" /></template>
    </ParamRail>
  </template>
</template>
