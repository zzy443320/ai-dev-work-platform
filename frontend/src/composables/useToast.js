// 全局提示。原来是自绘的 #toast 胶囊，现在交给 Element Plus 的 ElMessage。
//
// 对外签名刻意保持 `toast(msg, kind)` 不变 —— 全项目 40+ 处调用点（composables
// 与各视图）都不用动，kind 仍是旧版那三个值：ok / err / warn。
//
// ElMessage 由 unplugin-auto-import 按需注入（见 vite.config.js）：这里**不写
// import 是刻意的**，插件会同时补上组件与它的样式；改成显式 import 反而会丢样式。
const KIND_TO_TYPE = { ok: 'success', err: 'error', warn: 'warning' }

export function toast(msg, kind = 'ok') {
  ElMessage({
    message: String(msg == null ? '' : msg),
    type: KIND_TO_TYPE[kind] || 'info',
    duration: 2600,   // 与旧版 #toast 的 2.6s 一致
    grouping: true,   // 连续同类提示合并，避免刷屏
  })
}

export function useToast() {
  return { toast }
}
