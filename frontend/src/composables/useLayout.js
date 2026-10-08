// 整体布局模式 —— 模块级单例（惯例同 useConfigDrawer / useHealth）。
//
// 两种布局共享同一套 DOM 与组件，只切外壳的排列方式：
//   side = 侧边工作台（左侧分组导航 + 全宽主列，顶部页签头隐藏）—— 默认
//   top  = 顶部导航（页签搬进顶栏、紧贴品牌右侧 + 全宽主列）
//
// 两种布局下右侧配置栏都是**抽屉**（`.col-side` position:fixed，齿轮按钮开合），
// 不再有「三栏经典」那种常驻右栏 —— 常驻右栏要给它单独限高内滚才能不把页面顶出
// 滚动条，而主列的模块都是自然流整页滚动，两套高度语言混在一屏里反而更难看。
//
// 持久化 key `ui-layout`。旧值 `tri`（三栏经典）已废弃：读到时回落到默认布局，
// 不报错、也不给老用户留一个不认识的抽屉状态。
import { ref, watch } from 'vue'

export const LAYOUT_KEY = 'ui-layout'
export const DEFAULT_LAYOUT = 'side'
export const LAYOUTS = [
  { key: 'side', label: '侧边工作台' },
  { key: 'top', label: '顶部导航' },
]

const KEYS = LAYOUTS.map((l) => l.key)
function readSaved() {
  try {
    const v = localStorage.getItem(LAYOUT_KEY)
    return KEYS.includes(v) ? v : DEFAULT_LAYOUT
  } catch {
    return DEFAULT_LAYOUT
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
