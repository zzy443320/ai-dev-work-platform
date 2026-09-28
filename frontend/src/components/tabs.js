// 页签的唯一事实来源。
//
// 旧版把页签名、图标、顺序、以及「切到该页签要做什么副作用」散在三处：
// index.html 的按钮、app.js 的 switchTab()、以及各处硬编码的字符串。
// 这里收拢成一份表，组件与路由都读它。
//
// 页签顺序与旧版严格一致（默认落在 stats）——顺序变了会让用户困惑，
// 也会让依赖「第 2 个页签是问答」的既有习惯失效。
export const TABS = [
  { name: 'stats', label: '统计', icon: 'chart' },
  { name: 'chat', label: '问答', icon: 'chat' },
  { name: 'defect', label: '缺陷修复', icon: 'wrench' },
  { name: 'team', label: '长任务作业', icon: 'team' },
  { name: 'reqdev', label: '需求开发', icon: 'image' },
  { name: 'apidebug', label: '接口联调', icon: 'code' },
  { name: 'codetest', label: '代码测试', icon: 'check' },
  { name: 'extensions', label: '扩展能力', icon: 'grid' },
]

export const DEFAULT_TAB = 'stats'

// 哪些页签在右侧挂「产出物」面板（旧版 ARTIFACT_TAB_LABEL 的同口径）
export const ARTIFACT_TAB_LABEL = {
  reqdev: '需求开发',
  apidebug: '接口联调',
  codetest: '代码测试',
  chat: '问答',
  team: '长任务作业',
}

// 只有缺陷修复页签显示顶部 5 步流程条（旧版 switchTab 里的判断）
export const STAGE_FLOW_TAB = 'defect'

/** 图标路径表。用 24x24 viewBox 的 stroke path，与旧版一致。 */
export const ICON_PATHS = {
  chart: '<path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6"/><rect x="12" y="8" width="3" height="10"/><rect x="17" y="5" width="3" height="13"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.7 9.7 0 0 1-2.8-.4L3 21l1.6-4.6A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4Z"/>',
  wrench: '<path d="M14.7 6.3a5 5 0 0 0-7 7l-4 4a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l4-4a5 5 0 0 0 7-7l-2.6 2.6-2-2Z"/>',
  team: '<circle cx="6" cy="7" r="3"/><circle cx="18" cy="7" r="3"/><circle cx="12" cy="18" r="3"/><path d="M9 7h6M8.3 9.4l2.5 6M15.7 9.4l-2.5 6"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
  code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  check: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M17.5 14v7M14 17.5h7"/>',
}
