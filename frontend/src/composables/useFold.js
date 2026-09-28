// 面板折叠状态：localStorage 键 `panel-fold-state`，与旧版 app.js:4596 完全同构。
//
// 旧版是命令式 `togglePanel(id)` 直接操作 DOM class，这里改成响应式 Set，
// 但**持久化格式必须一致** —— 同一份 localStorage 数据，新旧两版要能互相读懂
// （用户在旧版折叠过的面板，切到 /v2 应该还是折叠的）。
import { reactive, watch } from 'vue'

const FOLD_KEY = 'panel-fold-state'

function load() {
  try {
    return JSON.parse(localStorage.getItem(FOLD_KEY) || '{}') || {}
  } catch (e) {
    // 隐私模式 / 脏数据：不记忆状态即可，不影响功能
    return {}
  }
}

const state = reactive(load())

function persist() {
  try {
    localStorage.setItem(FOLD_KEY, JSON.stringify(state))
  } catch (e) { /* 隐私模式等场景下不记忆状态即可 */ }
}

watch(state, persist, { deep: true })

/** 面板是否被折叠。id 与旧版 index.html 里的元素 id 一致，如 `usage-tasks-panel` */
export function isCollapsed(id) {
  return state[id] === true
}

/** 折叠 / 展开。语义与旧版 togglePanel 一致：翻转后写回 localStorage */
export function toggleFold(id) {
  state[id] = !state[id]
}

export function useFold() {
  return { state, isCollapsed, toggleFold }
}
