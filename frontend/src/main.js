import { createApp } from 'vue'
import App from './App.vue'
import { TABS, DEFAULT_TAB } from './components/tabs.js'

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
