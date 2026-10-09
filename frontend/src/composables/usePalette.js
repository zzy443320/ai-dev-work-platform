// 命令面板（⌘K）的开合状态。单独一个 composable 而不是塞进 App.vue：
// 顶栏那颗搜索胶囊要显示它、面板自己要用它、全局快捷键也要翻它，三处共享一份。
import { ref } from 'vue'

const open = ref(false)

export function usePalette() {
  function show() { open.value = true }
  function hide() { open.value = false }
  function toggle() { open.value = !open.value }
  return { open, show, hide, toggle }
}
