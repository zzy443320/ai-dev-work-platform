<script setup>
// 原生 <select> 的弹层样式在各浏览器不可控，旧版用自绘面板替换（见 app.js 的
// `fully custom themed dropdown` 一段）。这里用组件等价复刻：
//   - 收起时是一个 .cs-trigger 按钮，显示当前选项
//   - 展开时在 body 上挂一个 .cs-panel，用 fixed 定位跟随触发器
//   - 点击外部 / Esc 关闭；选项多时自动向上弹（空间不足）
//
// 之所以仍复用旧版的 class 名（.cs-*），是为了直接沿用 style.css 里的样式，
// 迁移期不重做视觉。
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  options: { type: Array, default: () => [] },   // [{ value, label }] 或 ['a','b']
  placeholder: { type: String, default: '请选择' },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'change'])

const open = ref(false)
const triggerEl = ref(null)
const panelEl = ref(null)
const pos = ref({ left: 0, top: 0, width: 0, up: false })

// 允许 options 传字符串数组，统一归一成 {value,label}
const norm = computed(() =>
  props.options.map((o) =>
    o && typeof o === 'object'
      ? { value: o.value, label: o.label ?? String(o.value) }
      : { value: o, label: String(o) }
  )
)
const currentLabel = computed(() => {
  const hit = norm.value.find((o) => String(o.value) === String(props.modelValue))
  return hit ? hit.label : props.placeholder
})

function layout() {
  const el = triggerEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const spaceBelow = window.innerHeight - r.bottom
  const up = spaceBelow < 220
  pos.value = {
    left: r.left,
    width: r.width,
    top: up ? r.top : r.bottom + 4,
    up,
  }
}

async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    await nextTick()
    layout()
  }
}

function pick(o) {
  emit('update:modelValue', o.value)
  emit('change', o.value)
  open.value = false
}

function onDocClick(e) {
  if (!open.value) return
  const t = e.target
  if (triggerEl.value && triggerEl.value.contains(t)) return
  if (panelEl.value && panelEl.value.contains(t)) return
  open.value = false
}
function onKey(e) {
  if (e.key === 'Escape' && open.value) open.value = false
}

watch(open, (v) => {
  if (v) {
    window.addEventListener('scroll', layout, true)
    window.addEventListener('resize', layout)
  } else {
    window.removeEventListener('scroll', layout, true)
    window.removeEventListener('resize', layout)
  }
})

onMounted(() => {
  document.addEventListener('click', onDocClick, true)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick, true)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('scroll', layout, true)
  window.removeEventListener('resize', layout)
})
</script>

<template>
  <div class="cs" :class="{ open, disabled }">
    <button
      ref="triggerEl"
      type="button"
      class="cs-trigger"
      :disabled="disabled"
      @click.stop="toggle"
    >
      <span class="cs-label">{{ currentLabel }}</span>
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="panelEl"
        class="cs-panel"
        :class="{ up: pos.up }"
        :style="{
          left: pos.left + 'px',
          width: pos.width + 'px',
          top: pos.top + 'px',
        }"
      >
        <button
          v-for="o in norm"
          :key="String(o.value)"
          type="button"
          class="cs-opt"
          :class="{ active: String(o.value) === String(modelValue) }"
          @click.stop="pick(o)"
        >{{ o.label }}</button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.cs { position: relative; display: inline-block; width: 100%; }
.cs-trigger {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; width: 100%; cursor: pointer;
}
.cs-trigger.disabled, .cs.disabled .cs-trigger { opacity: .55; cursor: not-allowed; }
.cs-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
