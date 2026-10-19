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
// 三栏工作台外壳层（2026-10-09 高级感改版）。必须在 theme.css 之后：
// 它要覆盖的是 style.css / theme.css 里的外壳与控件观感，靠「后加载者生效」赢。
import './styles/desk.css'
// 全局基元（卡片 / 表格 / 弹窗 / 徽标 / 表单 / 日志 / 气泡）：与 desk.css 同一轮改版，
// 单独一个文件是因为它覆盖的是 style.css 里那几十个页签共用的类，和外壳互不相干。
import './styles/primitives.css'
// 参数展示层（2026-10-13 改版）：就地控件条 + 常驻参数轨的观感都在这一个文件里。
// 必须在 skin-*.css 之前：皮肤层只调外观（圆角/软阴影），尺寸与版式以这里为准。
import './styles/params.css'
// 资源汇总（2026-10-19 新增模块）：分组卡片 + 可点条目 + 就地编辑。
// 同样排在皮肤层之前，颜色只走令牌，皮肤层能覆盖到圆角与阴影。
import './styles/resources.css'
// 黏土糖果皮肤层（2026-10-12）。必须排在最后：它只改外观（圆角/软阴影/配色），
// 不动结构、id、class 与布局尺寸，靠「最后加载」赢过前面所有同权重规则。
// 契约见文件头（--topbar-h、#figma-tree:empty::before、display:grid 等一律不碰）。
import './styles/skin-clay.css'

import { loadParamSchema } from './composables/useParamSchema.js'

const app = createApp(App)

// ---------------------------------------------------------------------------
// 参数 schema 必须**在挂载之前**到位。
//
// 原因：五个任务模块的表单初值现在直接读 schema（`schemaDefault(...)`），
// composable 是在 setup 里同步取值的 —— 挂载晚于这次 await，取到的一定是真值。
// 反过来（先挂载后拉 schema）就得给每个表单加骨架屏，还会出现「空表单闪一下
// 再回填默认值」的错觉，用户以为默认值是自己填进去的。
//
// 拉不到就明确报错并停在原地，**不**退化成一张手写的兜底表单：
// 表单骗人比表单不出来更难查（填了却跑不对，且没人知道范围是哪来的）。
// ---------------------------------------------------------------------------
function mountAfterSchema() {
  return loadParamSchema().catch((e) => {
    const host = document.getElementById('app')
    if (host) {
      host.innerHTML = '<div style="padding:48px 32px;font:14px/1.8 system-ui;color:#dbe4f5">'
        + '<h2 style="font-size:18px;margin-bottom:10px">参数定义没拿到，界面没有启动</h2>'
        + '<p style="color:#9fb0cc">接口 <code>/api/params/schema</code> 请求失败：'
        + String((e && e.message) || e).replace(/[<>&]/g, '') + '</p>'
        + '<p style="color:#6b7a99;margin-top:10px">确认本机服务在跑（http://127.0.0.1:8765），'
        + '或后端 web/params_schema.py 是否有语法错误，然后刷新。</p></div>'
    }
    throw e
  }).then(() => {
    app.mount('#app')
    return app
  })
}

const mounted = mountAfterSchema()

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
/** 用例与排查脚本可以 await 这个 Promise 确认「界面真的挂载完了」 */
window.__APP_READY__ = mounted
