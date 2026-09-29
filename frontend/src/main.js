import { createApp } from 'vue'
import App from './App.vue'
import { TABS, DEFAULT_TAB } from './components/tabs.js'

// ---------------------------------------------------------------------------
// Element Plus 样式加载顺序（**不能颠倒**）：
//   1. 暗色变量表：Element 的暗色主题只认 `html.dark`，这套变量必须先进来；
//   2. 项目桥接层：把 --el-* 映射到项目自己的 CSS 变量，并覆盖 Element 默认观感。
// 桥接层里用的是与 Element 同权重的选择器，靠「后加载者生效」，顺序反了会一半错色。
// 组件自身的样式不在这里 —— 由 unplugin-vue-components 按需注入（见 vite.config.js）。
// ---------------------------------------------------------------------------
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/theme.css'

const app = createApp(App)
app.mount('#app')

// ---------------------------------------------------------------------------
// 迁移期兼容层：把几个旧版里的全局函数挂到 window 上。
//
// 为什么需要：tests/ 下 12 个界面用例直接 `page.evaluate("switchTab('chat')")`。
// 那些断言测的是「切换页签后界面是否正确」，不是「是否存在全局函数」。
// 保留这个入口，能让迁移期间既有用例继续跑（回归网不失效），
// 等所有页签迁完、用例改成基于组件状态后再移除。
// ---------------------------------------------------------------------------
app.config.globalProperties.$tabs = TABS
window.__VUE_APP__ = app
window.__TAB_NAMES__ = TABS.map((t) => t.name)
window.__DEFAULT_TAB__ = DEFAULT_TAB
