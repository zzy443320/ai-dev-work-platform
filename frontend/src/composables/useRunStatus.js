// 顶栏状态药丸（`#status`）。旧版是散落各处直接写 DOM：
//   `$('#status').className = 'status-pill ok'; $('#status').textContent = '...'`
// （app.js:1588/1629/1727/1734/1685…）。三处调用方 —— 流水线、试运行、
// 健康检查 —— 各写各的，谁后写谁生效。
//
// 迁移到 Vue 后它天然是「跨组件共享状态」：顶栏在 App.vue，写它的在 DefectPane。
// 所以抽成一个模块级单例（同 useToast 的做法），而不是靠 props 层层传。
import { ref } from 'vue'

/** 药丸文案 */
const text = ref('')
/** 药丸样式：'ok' | 'error' | 'running' | 'idle'（对应旧版 .status-pill 的类） */
const kind = ref('idle')

/**
 * 设置顶栏状态。
 * @param {string} t 文案
 * @param {'ok'|'error'|'running'|'idle'} k 样式
 */
export function setRunStatus(t, k = 'idle') {
  text.value = t
  kind.value = k
}

export function useRunStatus() {
  return { text, kind, setRunStatus }
}
