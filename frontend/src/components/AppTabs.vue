<script setup>
// 页签栏。等价复刻旧版 switchTab() 的行为，但有两点刻意改进：
//
//   1. **保留 `switchTab` 全局函数**（见 main.js）—— 12 个界面用例直接调它，
//      把契约留着，迁移期测试才不会整片变红；
//   2. 切页签的副作用（显隐流程条 / 产出物面板、按需拉数据）从「手写类名切换」
//      改成「响应式 watch」，不再有 DOM 与状态的隐式耦合。
//
// class 名沿用旧版（#tabs .tab / .tabpane），style.css 与既有用例的选择器
// 都能直接命中。
import { ICON_PATHS, TABS } from './tabs.js'

defineProps({
  modelValue: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <nav class="tabs" id="tabs" role="tablist">
    <button
      v-for="t in TABS"
      :key="t.name"
      type="button"
      class="tab"
      :class="{ active: t.name === modelValue }"
      :data-tab="t.name"
      role="tab"
      :aria-selected="t.name === modelValue ? 'true' : 'false'"
      @click="emit('update:modelValue', t.name)"
    >
      <svg
        viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        v-html="ICON_PATHS[t.icon]"
      />
      {{ t.label }}
    </button>
  </nav>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 2px;
}
.tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
</style>
