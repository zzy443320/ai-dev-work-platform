// 全局提示条。与旧版 `toast(msg, kind)` 同样的 2.6s 自动消失。
//
// 旧版是命令式全局函数，这里做成组件 + 一个可在任意处调用的单例，
// 这样从 Vue 组件和从非组件代码（如 SSE 回调）都能用。
import { ref } from 'vue'

const message = ref('')
const kind = ref('ok')
const visible = ref(false)
let timer = null

export function toast(msg, k = 'ok') {
  message.value = msg
  kind.value = k
  visible.value = true
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    visible.value = false
    timer = null
  }, 2600)
}

export function useToast() {
  return { message, kind, visible, toast }
}
