// 问答页签的「会话历史侧栏 + 两个浮层入口」的开合状态 —— 模块级单例。
//
// 为什么不放组件里：ChatPane 是 el-tab-pane 的子组件，切走再切回来会**重新挂载**，
// 局部 ref 会被重置成默认值（用户刚收起的侧栏又弹开）。共享状态按项目惯例放模块级
// 单例（见 useConfigDrawer / useLayout），需要的话用 localStorage 记忆。
//
// 2026-09-30 改版：原来的右栏（产出物 + 使用说明）拆成两个**浮层**——
//   右栏只有 320px，产出物卡片、说明文字都挤在里面；同时三栏挤占了对话区宽度。
//   现在：产出物 = 右侧浮窗抽屉（artOpen），使用说明 = 居中浮窗（helpOpen），
//   都由对话区标题栏的按钮打开。中栏因此变宽，对话区也不再被右栏压窄。
//
// 默认值即测试契约：
//   historyOpen 默认展开（首屏必须能看见会话历史在哪）；
//   artOpen / helpOpen 默认**关闭**（浮层默认盖在内容上，一进来就弹是打扰；
//   关着也让 `#pane-chat` 的可见文本保持干净，界面用例读它就是正文）。
import { ref, watch } from 'vue'

const KEY = 'chat-rails'
const saved = (() => {
  try {
    return typeof localStorage !== 'undefined' ? JSON.parse(localStorage.getItem(KEY) || '{}') : {}
  } catch {
    return {}
  }
})()

const historyCollapsed = ref(saved.history === true)
/** 产出物浮窗（右侧抽屉）。上次开着就还开着 —— 正在对照提案改代码时切页签回来不用重点一次。 */
const artOpen = ref(saved.art === true)
/** 使用说明浮窗。「打开看看」是一次性动作，关掉就该忘掉，所以**不持久化**。 */
const helpOpen = ref(false)

watch([historyCollapsed, artOpen], ([h, a]) => {
  try {
    localStorage.setItem(KEY, JSON.stringify({ history: h, art: a }))
  } catch {
    /* 隐私模式存不进去就算了，不影响功能 */
  }
})

export function useChatRails() {
  return {
    historyCollapsed,
    artOpen,
    helpOpen,
    toggleHistory: () => { historyCollapsed.value = !historyCollapsed.value },
    toggleArt: () => { artOpen.value = !artOpen.value },
    toggleHelp: () => { helpOpen.value = !helpOpen.value },
    /** 点遮罩 / Esc：两个浮层一起收 */
    closeFloats: () => { artOpen.value = false; helpOpen.value = false },
  }
}
