// 主题 = 两轴正交，各自独立可切换：
//
//   data-theme = dark | light             明暗模式（键 `fixer-theme`，沿用旧版键名，
//                                         新旧两版来回切换时用户的选择不丢）
//   data-style = indigo | blue | ...      强调色风格（键 `fixer-style`，本次新增）
//
// 为什么风格表里只写「一个主色 + 一个次色」：
// Element Plus 要把 --el-color-primary 派生到 light-3/5/7/8/9 与 dark-2 共 6 个色，
// 5 套风格 × 2 种明暗 = 60 个十六进制值，手抄必错且改一个就得改一片。
// 这里按 Element 官方的混色比例在**运行时**算出来，于是：
//   改一个主色 → 按钮 / 标签 / 页签 / 滑块 / 焦点环 / 图表 整套跟着变。
//
// 明暗模式的落地方式：项目自己的样式读 `[data-theme]`，而 Element Plus 的暗色
// 主题靠 `html.dark`。所以两个都要设 —— 只设一个会出现「面板是暗的、按钮是亮的」。
import { ref } from 'vue'

const MODE_KEY = 'fixer-theme'
const STYLE_KEY = 'fixer-style'

/**
 * 可切换的风格。primary 是强调色，accent 用于渐变、图表第二条系列与选中态点缀。
 * key 会写进 `data-style`，可以拿它写 CSS 覆盖（`[data-style="amber"] .xxx`）。
 */
export const STYLES = [
  { key: 'indigo', label: '靛青', primary: '#6366f1', accent: '#8b5cf6' },
  { key: 'blue', label: '海蓝', primary: '#2563eb', accent: '#38bdf8' },
  { key: 'teal', label: '松石', primary: '#0d9488', accent: '#22d3ee' },
  { key: 'amber', label: '琥珀', primary: '#d97706', accent: '#f59e0b' },
  { key: 'rose', label: '玫瑰', primary: '#e11d48', accent: '#fb7185' },
]

export const DEFAULT_STYLE = 'indigo'

/** Element Plus 暗色模式下，light-N 系列混的是这个暗底色（见其 dark/css-vars.css） */
const DARK_MIX_BASE = '#141414'
const LIGHT_MIX_BASE = '#ffffff'

// ── 颜色小工具（不引依赖，就这三个函数） ──────────────────────────────

function hexToRgb(hex) {
  const h = String(hex).replace('#', '')
  const s = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const n = parseInt(s, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** 按权重混色：w 是 a 的占比（0~1）。与 SCSS 的 mix($a, $b, $w) 同口径。 */
function mixHex(a, b, w) {
  const A = hexToRgb(a)
  const B = hexToRgb(b)
  const c = A.map((v, i) => Math.round(v * w + B[i] * (1 - w)))
  return '#' + c.map((v) => v.toString(16).padStart(2, '0')).join('')
}

function rgba(hex, alpha) {
  const [r, g, b] = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// ── 状态 ───────────────────────────────────────────────────────────

function readMode() {
  try {
    const saved = localStorage.getItem(MODE_KEY)
    if (saved === 'dark' || saved === 'light') return saved
  } catch (e) { /* 隐私模式 */ }
  // 旧版默认 dark（index.html 上 data-theme="dark"）
  return 'dark'
}

function readStyle() {
  try {
    const saved = localStorage.getItem(STYLE_KEY)
    if (STYLES.some((s) => s.key === saved)) return saved
  } catch (e) { /* 同上 */ }
  return DEFAULT_STYLE
}

const theme = ref(readMode())
const style = ref(readStyle())

function applyMode(mode) {
  const root = document.documentElement
  root.dataset.theme = mode
  // Element Plus 的暗色主题只认 html.dark，不认 data-theme
  root.classList.toggle('dark', mode === 'dark')
}

/**
 * 把一套风格写成 CSS 变量。项目变量与 Element 变量一起写，
 * 这样自研样式与 Element 组件在同一套配色下，不会一半新一半旧。
 */
function applyStyle(key, mode) {
  const st = STYLES.find((s) => s.key === key) || STYLES[0]
  const root = document.documentElement
  const set = (k, v) => root.style.setProperty(k, v)
  const mixBase = mode === 'dark' ? DARK_MIX_BASE : LIGHT_MIX_BASE

  root.dataset.style = st.key

  // Element Plus：主色 + 6 个派生色
  set('--el-color-primary', st.primary)
  for (const n of [3, 5, 7, 8, 9]) {
    set(`--el-color-primary-light-${n}`, mixHex(mixBase, st.primary, n / 10))
  }
  set('--el-color-primary-dark-2', mixHex('#000000', st.primary, 0.2))

  // 项目自己的强调色变量
  set('--primary', st.primary)
  set('--primary-2', st.accent)
  set('--primary-soft', rgba(st.primary, mode === 'dark' ? 0.16 : 0.10))
  // 图表第二条系列（style.css 的 .usage-chart 读它）
  set('--accent-2', st.accent)
  // 背景光晕跟着主色走，换风格时整页色调一致
  set('--bg-glow-1', rgba(st.primary, mode === 'dark' ? 0.16 : 0.10))
  set('--bg-glow-2', rgba(st.accent, mode === 'dark' ? 0.12 : 0.08))
}

/** 启动即落地一次，避免首帧先闪默认配色再跳到用户选的那套 */
applyMode(theme.value)
applyStyle(style.value, theme.value)

export function useTheme() {
  function set(t) {
    if (t !== 'dark' && t !== 'light') return
    theme.value = t
    applyMode(t)
    // light-N 的混色底会随明暗变化，所以换明暗必须重算风格变量
    applyStyle(style.value, t)
    try {
      localStorage.setItem(MODE_KEY, t)
    } catch (e) { /* 不记忆即可 */ }
  }

  function toggle() {
    set(theme.value === 'dark' ? 'light' : 'dark')
  }

  function setStyle(key) {
    if (!STYLES.some((s) => s.key === key)) return
    style.value = key
    applyStyle(key, theme.value)
    try {
      localStorage.setItem(STYLE_KEY, key)
    } catch (e) { /* 同上 */ }
  }

  return { theme, style, styles: STYLES, toggle, set, setStyle }
}
