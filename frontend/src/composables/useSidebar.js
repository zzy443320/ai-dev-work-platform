// 右侧配置侧栏的收起状态 —— 模块级单例。
//
// 为什么单独一个 composable：App.vue（顶栏开关 + 布局类名）和
// SettingsPanel.vue（面板头部的收起按钮 + 收起后的竖排展开按钮）
// 两处要读写同一份状态；项目惯例是共享状态放模块级单例（见 useHealth）。
//
// 状态持久化在 localStorage（`ui-sidebar`）：用户收起后刷新/重启仍保持收起。
// 默认展开 —— 首次使用的人应该直接看到配置在哪；界面用例跑在干净 context
// 里（无 localStorage），所以默认值同时是测试契约的一部分，别改成默认收起。
import { ref, watch } from 'vue'

const KEY = 'ui-sidebar'
const saved = typeof localStorage !== 'undefined' ? localStorage.getItem(KEY) : null
const collapsed = ref(saved === 'collapsed')

watch(collapsed, (v) => {
  try {
    localStorage.setItem(KEY, v ? 'collapsed' : 'open')
  } catch {
    /* 隐私模式等存不进去就算了，不影响功能 */
  }
})

function toggle() {
  collapsed.value = !collapsed.value
}

export function useSidebar() {
  return { collapsed, toggle }
}
