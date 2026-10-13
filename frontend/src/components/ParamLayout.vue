<script setup>
// 任务模块的**参数布局外壳**：按 schema 的分档结果决定用哪种形态，
// 模块自己只写「主区放什么、动作按钮放什么」。
//
//     <ParamLayout name="codetest" :model="params">
//       <template #actions>…运行 / 预览按钮…</template>
//       …主区面板（过程与产物）…
//     </ParamLayout>
//
// 分档口径（web/params_schema.py 的 layout_of）：
//   * strip —— 参数 ≤6 且没有 rows>=8 的长文本：参数常驻主区顶部一行（就地控件条）；
//   * rail  —— 参数 >6 或含长文本：参数轨作为 grid 的真实第二列。
// 两种形态都**没有浮层、没有遮罩、没有主列让位**，所以主区的列数不会
// 因为「打开参数」而突变 —— 这是这次改版要解决的第一病根。
//
// 为什么 rail 挂在 pane 内部而不是外壳的第三列：el-tab-pane 切走时本身 display:none，
// 轨跟着一起消失；用 Teleport 搬到外壳反而要自己处理「切页签后上一模块的轨还留着」。
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

const mod = computed(() => paramModule(props.name))
const rail = computed(() => paramLayout(props.name) === 'rail')
</script>

<template>
  <div v-if="!mod" class="pane-cols">
    <div class="pane-col-main">
      <div class="pstrip-empty">参数定义没到位（/api/params/schema 未就绪），主区仍可用。</div>
      <slot />
    </div>
  </div>

  <div v-else class="pane-cols" :class="rail ? 'cols-rail' : 'cols-strip'">
    <div class="pane-col-main">
      <ParamStrip v-if="!rail" :name="name" :model="model" :opt-label="optLabel">
        <template #actions><slot name="actions" /></template>
        <template #notes><slot name="notes" /></template>
      </ParamStrip>
      <slot />
    </div>
    <ParamRail v-if="rail" :name="name" :model="model" :opt-label="optLabel">
      <template #actions><slot name="actions" /></template>
      <template #notes><slot name="notes" /></template>
    </ParamRail>
  </div>
</template>
