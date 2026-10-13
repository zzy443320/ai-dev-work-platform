<script setup>
// 参数展示形态 B：**常驻参数轨**（参数 >6 条，或含 rows>=8 长文本的模块用它）。
//
// 与旧抽屉的关键差别不是宽度，而是**它不再是浮层**：
// 旧抽屉 position:fixed + 主列 padding-right:436px 让位 + 窄屏退化成遮罩模态，
// 于是「打开参数」这件事会改变主区的列数，看板忽宽忽窄。
// 这里它是 pane 内部 grid 的真实一列（见 ParamLayout + styles/params.css），
// 主区列数不随开合变化；轨自己 sticky + 内滚，长表单不会把动作条顶出屏幕。
//
// 分组折叠：core / common 常驻，advanced 默认收起（在轨里它是「这一列还有下半段」的
// 提示，而不是像抽屉那样整层看不见）。
import { computed, ref } from 'vue'
import { paramModule, isDefaultValue } from '../composables/useParamSchema.js'
import ParamField from './ParamField.vue'

const props = defineProps({
  name: { type: String, required: true },
  model: { type: Object, required: true },
  /** 标签解析器，透传给 ParamField（见其注释） */
  optLabel: { type: Function, default: null },
})

const mod = computed(() => paramModule(props.name) || { fields: [] })
const byGroup = (g) => mod.value.fields.filter((f) => f.group === g)
const core = computed(() => byGroup('core'))
const common = computed(() => byGroup('common'))
const advanced = computed(() => byGroup('advanced'))
const openAdv = ref(false)

const changed = computed(() => mod.value.fields.filter(
  (f) => !isDefaultValue(props.model.get(f.key), f.default)).length)
/** 必填未填：只报状态不拦按钮（与 ParamStrip 同口径） */
const missing = computed(() => mod.value.fields.filter((f) => {
  if (!f.required) return false
  const v = props.model.get(f.key)
  return v === '' || v === null || v === undefined || (typeof v === 'string' && !v.trim())
}).length)
</script>

<template>
  <aside class="prail pcol" :id="`param-rail-${name}`" :aria-label="`${mod.title}（参数轨）`">
    <div class="prail-head">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 6h16M4 12h10M4 18h13" /><circle cx="18" cy="12" r="2.2" /><circle cx="15" cy="18" r="2.2" />
      </svg>
      <b>{{ mod.title }}</b>
      <span class="prail-count" :class="{ miss: missing }">
        {{ missing ? `必填未填 ${missing} 项` : `${mod.fields.length} 项 · 已改 ${changed}` }}
      </span>
    </div>
    <div v-if="mod.hint" class="prail-hint">{{ mod.hint }}</div>

    <div class="prail-body">
      <template v-if="core.length">
        <div class="prail-grp">核心</div>
        <ParamField v-for="f in core" :key="f.key" :field="f" :model="model" variant="rail" :opt-label="optLabel" />
      </template>
      <template v-if="common.length">
        <div class="prail-grp">常用</div>
        <ParamField v-for="f in common" :key="f.key" :field="f" :model="model" variant="rail" :opt-label="optLabel" />
      </template>
      <template v-if="advanced.length">
        <div class="prail-grp">
          <button type="button" class="prail-adv-btn" :id="`btn-${name}-params-advanced`"
                  :aria-expanded="openAdv" @click="openAdv = !openAdv">
            高级 {{ openAdv ? '▴' : `▾ ${advanced.length} 项` }}
          </button>
        </div>
        <template v-if="openAdv">
          <ParamField v-for="f in advanced" :key="f.key" :field="f" :model="model" variant="rail" :opt-label="optLabel" />
        </template>
      </template>

      <!-- 模块专属的解释性文字（如 Figma 取稿通道的能力差异）。
           放在轨里而不是主区：它讲的是「这些参数怎么选」，离参数远就没人看。
           id 沿用改版前契约（#req-figma-hint 有界面用例锁）。 -->
      <slot name="notes" />
    </div>

    <div class="prail-foot"><slot name="actions" /></div>
  </aside>
</template>
