<script setup>
// 主区顶部的「当前参数摘要」条 —— 参数搬进抽屉后，主区必须还能一眼看出
// 「这次要跑的是什么」，否则用户面对一块空白的产物区会以为参数丢了。
//
// 形态：一行 chip（标签 + 值）+ 一枚开合抽屉的按钮。值由调用方按模块自己拼
// （每个模块的关键参数不一样），这里只负责排版与「没填」的提示色。
//
// 按钮刻意跟在 chip 后面**左对齐**，不推到行尾：抽屉是右侧浮层，
// 推到行尾的控件会被打开的抽屉盖住。
import { computed } from 'vue'
import { useModuleParams } from '../composables/useModuleParams.js'

const props = defineProps({
  /** 模块 key，与 ParamDrawer 的 name 对应 */
  name: { type: String, required: true },
  /** 摘要项：[{ label, value, tone }]；tone = 'todo' 表示这项还没填 */
  items: { type: Array, default: () => [] },
  /** 抽屉里的主行动词，用来生成按钮文案（改参数 / 收起参数） */
  action: { type: String, default: '参数' },
})

const { isOpen, toggle } = useModuleParams()
const opened = computed(() => isOpen(props.name))
const btnText = computed(() => (opened.value ? `收起${props.action}` : `修改${props.action}`))
// id 里不带冒号（见 ParamDrawer 同处注释）
const slug = computed(() => props.name.replace(/:/g, '-'))
const btnId = computed(() => `btn-${slug.value}-params`)
const barId = computed(() => `param-bar-${slug.value}`)
/** 有 tone='todo' 的项时整条起提示色，比逐个 chip 变色更好发现 */
const hasTodo = computed(() => props.items.some((i) => i.tone === 'todo'))
</script>

<template>
  <div class="param-bar" :id="barId" :class="{ 'has-todo': hasTodo }">
    <span class="param-bar-label">{{ action }}摘要</span>
    <span v-for="it in items" :key="it.label" class="param-chip"
          :class="{ todo: it.tone === 'todo' }" :title="`${it.label}：${it.value}`">
      <b>{{ it.label }}</b><i>{{ it.value }}</i>
    </span>
    <el-button size="small" :id="btnId" :class="{ 'is-on': opened }" @click="toggle(name)">
      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 6h16M4 12h10M4 18h13" />
        <circle cx="18" cy="12" r="2.2" /><circle cx="15" cy="18" r="2.2" />
      </svg>
      {{ btnText }}
    </el-button>
  </div>
</template>
