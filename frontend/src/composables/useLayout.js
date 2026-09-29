// 整体布局模式 —— 模块级单例（惯例同 useSidebar/useHealth）。
//
// 三种布局共享同一套 DOM 与组件，只切外壳的排列方式：
//   tri = 三栏（顶部页签 + 主列 + 右侧配置常驻）—— 默认，历史样子
//   top = 顶部导航瘦身（顶部页签 + 全宽主列，配置改右侧抽屉）
//   side = 侧边工作台（左侧分组导航 + 全宽主列，顶部页签隐藏，配置改抽屉）
//
// 默认 tri 是界面用例的隐含契约：用例跑在干净 context（无 localStorage），
// 很多断言假设 el-tabs 可见、右栏 330px —— 默认值不能改成其它布局。
// 持久化 key `ui-layout`，与 `ui-sidebar` 互不干扰。
import { ref, watch } from 'vue'

export const LAYOUT_KEY = 'ui-layout'
export const LAYOUTS = [
  { key: 'side', label: '侧边工作台' },
  { key: 'top', label: '顶部导航' },
  { key: 'tri', label: '三栏经典' },
]

const KEYS = LAYOUTS.map((l) => l.key)
function readSaved() {
  try {
    const v = localStorage.getItem(LAYOUT_KEY)
    return KEYS.includes(v) ? v : 'tri'
  } catch {
    return 'tri'
  }
}

const mode = ref(readSaved())

watch(mode, (v) => {
  try {
    localStorage.setItem(LAYOUT_KEY, v)
  } catch {
    /* 存不进去就算了，不影响功能 */
  }
})

function setMode(m) {
  if (KEYS.includes(m)) mode.value = m
}

export function useLayout() {
  return { mode, setMode, layouts: LAYOUTS }
}
