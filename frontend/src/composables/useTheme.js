// 主题切换。localStorage 键名与旧版一致（'fixer-theme'），
// 这样新旧版来回切换时用户的选择不会丢。
import { ref } from 'vue'

const STORAGE_KEY = 'fixer-theme'

function readInitial() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'dark' || saved === 'light') return saved
  } catch (e) { /* 隐私模式 */ }
  // 旧版默认 dark（index.html 上 data-theme="dark"）
  return 'dark'
}

const theme = ref(readInitial())

function apply(t) {
  document.documentElement.dataset.theme = t
}

apply(theme.value)

export function useTheme() {
  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    apply(theme.value)
    try {
      localStorage.setItem(STORAGE_KEY, theme.value)
    } catch (e) { /* 不记忆即可 */ }
  }
  function set(t) {
    theme.value = t
    apply(t)
    try {
      localStorage.setItem(STORAGE_KEY, t)
    } catch (e) { /* 同上 */ }
  }
  return { theme, toggle, set }
}
