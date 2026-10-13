<script setup>
// 参数展示形态 A：**就地控件条**（≤6 条且无长文本的模块用它）。
//
// 取代的是「顶部摘要 chip + 右侧浮层抽屉」那套：摘要 chip 只能看不能改，
// 要改还得点开抽屉（两下），抽屉一开主列让位 436px、看板从四列掉成一团。
// 这里把 chip 直接做成控件本身 —— 一格一个参数，标签在左值在右，
// 点上去就能改，**零次开合、不遮挡、不让位**，运行按钮就在同一张卡右端。
//
// 版式规则（由 schema 决定，不在组件里手挑）：
//   * 短控件（text / number / select / switch / segment）→ 顶部一行，自动换行；
//   * 长文本（textarea）→ 下方「正文」区，两行截断，点一下给全高；
//   * advanced 组 → 收进「更多设定」，多数时候不该占首屏。
import { computed, ref } from 'vue'
import { paramModule, isDefaultValue } from '../composables/useParamSchema.js'
import ParamField from './ParamField.vue'

const props = defineProps({
  name: { type: String, required: true },
  model: { type: Object, required: true },
})

const mod = computed(() => paramModule(props.name) || { fields: [] })
const inline = computed(() => mod.value.fields.filter(
  (f) => f.kind !== 'textarea' && f.group !== 'advanced'))
const body = computed(() => mod.value.fields.filter(
  (f) => f.kind === 'textarea' && f.group !== 'advanced'))
const advanced = computed(() => mod.value.fields.filter((f) => f.group === 'advanced'))

const moreOpen = ref(false)
/** 与默认值不等价的参数条数 —— 一眼看出这次跑的是不是默认组合 */
const changed = computed(() => mod.value.fields.filter(
  (f) => !isDefaultValue(props.model.get(f.key), f.default)).length)
/** 必填未填的条数。只报状态不拦按钮（「未齐禁跑」这次没选），
    但也不能反过来显示「全部默认值」骗人 —— 空必填项确实什么都还没给 */
const missing = computed(() => mod.value.fields.filter((f) => {
  if (!f.required) return false
  const v = props.model.get(f.key)
  return v === '' || v === null || v === undefined || (typeof v === 'string' && !v.trim())
}).length)
</script>

<template>
  <div class="pstrip" :id="`param-strip-${name}`">
    <div class="pstrip-row">
      <span class="pstrip-title">{{ mod.title }}</span>
      <ParamField v-for="f in inline" :key="f.key" :field="f" :model="model" variant="strip" />
      <button v-if="advanced.length" type="button" class="pstrip-more"
              :id="`btn-${name}-params-more`" :aria-expanded="moreOpen"
              @click="moreOpen = !moreOpen">
        {{ moreOpen ? '收起更多设定' : `更多设定 ${advanced.length} 项` }}
      </button>
      <span class="pstrip-flex"></span>
      <span v-if="missing" class="pstrip-changed miss">必填未填 {{ missing }} 项</span>
      <span v-else-if="changed" class="pstrip-changed">已改 {{ changed }} 项</span>
      <span v-else class="pstrip-changed none">全部默认值</span>
      <span class="pstrip-actions"><slot name="actions" /></span>
    </div>

    <div v-if="body.length" class="pstrip-body">
      <ParamField v-for="f in body" :key="f.key" :field="f" :model="model" variant="strip" />
    </div>

    <div v-if="moreOpen && advanced.length" class="pstrip-adv">
      <ParamField v-for="f in advanced" :key="f.key" :field="f" :model="model" variant="strip" />
    </div>

    <div v-if="mod.hint" class="pstrip-hint">{{ mod.hint }}</div>
  </div>
</template>
