// 右侧配置抽屉的开合状态 —— 模块级单例。
//
// 前身是 useSidebar（三栏经典里那条常驻右栏的「收起/展开」）。三栏布局去掉之后，
// 两种布局下配置栏都只有抽屉一种形态，「收起后留一条竖排展开按钮」这个状态随之
// 失去意义（抽屉本来就靠遮罩/Esc/齿轮关，不需要常驻占位），所以这里只剩开与合。
//
// 为什么要单例：顶栏的 #sidebar-toggle 与抽屉头部 #settings-fold 要读写同一份状态，
// 项目惯例是共享状态放模块级单例（见 useLayout / useHealth）。
//
// **不持久化**：抽屉是浮层，开着会盖住内容，刷新后回到「关着」才是干净的首屏状态。
import { ref } from 'vue'

const open = ref(false)

function toggle() { open.value = !open.value }
function show() { open.value = true }
function hide() { open.value = false }

export function useConfigDrawer() {
  return { open, toggle, show, hide }
}
