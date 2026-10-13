// **浮层表单抽屉**的开合状态（2026-10-13 参数形态改版后只剩扩展能力在用）。
//
// 五个任务模块（缺陷修复 / 长任务作业 / 需求开发 / 接口联调 / 代码测试）的参数
// 已经不再走这里：它们由 /api/params/schema 驱动，渲染成**就地控件条**或
// **常驻参数轨**（见 components/ParamLayout.vue 与 styles/params.css），
// 没有「开 / 关」这一说，也就不占主列宽度。
//
// 还留在这里的只有扩展能力的「技能」与「MCP」两个新增表单 —— 那是 CRUD 浮层，
// 主体是列表，表单确实应该点了才出现。
//
// key 的形态：`页签名` 或 `页签名:槽位`（扩展能力 = extensions:skill / extensions:mcp）。
// 外壳用 anyOpenFor(页签) 判断要不要给主列让位，所以一个页签里开几个抽屉、
// 叫什么名字都不需要外壳配合改代码。
//
// 默认值：列表管理型传 defaultOpen=false，保持「点新增才出表单」。
// 同一个页签内的抽屉互斥（show 一个就关其他的）—— 它们占的是同一个右侧位置，
// 同时开两个就是重叠。
//
// 关掉之后本次会话内保持关闭（切页签不重置），刷新回到各自的默认值。
// **刻意不写 localStorage**：抽屉是浮层，记住「上次是关的」会让新用户以为表单不存在。
//
// 为什么不放组件里：共享状态按项目惯例一律放模块级单例
// （见 useConfigDrawer / useLayout / useChatRails）。
import { reactive } from 'vue'

/** open[key] === false 才是关着；没注册过的 key 一律按「没开」处理（见 anyOpenFor） */
const open = reactive({})

/** 抽屉注册自己的 key 与默认开合。ParamDrawer 在 setup 里调一次。 */
function register(key, defaultOpen = true) {
  if (!(key in open)) open[key] = defaultOpen
}

/** 页签前缀：`team` 或 `team:xxx` 都算 team 这个页签里的抽屉 */
function belongsTo(key, tab) {
  return key === tab || key.startsWith(`${tab}:`)
}

function isOpen(key) {
  return open[key] === true
}

/** 该页签下是否还有任何开着的抽屉 —— 决定主列要不要让出右侧空间 */
function anyOpenFor(tab) {
  return Object.keys(open).some((k) => belongsTo(k, tab) && open[k] === true)
}

function show(key) {
  const tab = key.split(':')[0]
  // 同页签互斥：两个抽屉占的是同一个右侧位置
  for (const k of Object.keys(open)) {
    if (k !== key && belongsTo(k, tab)) open[k] = false
  }
  open[key] = true
}
function close(key) { open[key] = false }
/** 关掉某个页签下的所有抽屉（Esc 用：不用知道当前页签有几个抽屉、叫什么） */
function closeFor(tab) {
  for (const k of Object.keys(open)) {
    if (belongsTo(k, tab)) open[k] = false
  }
}
function toggle(key) {
  if (isOpen(key)) close(key)
  else show(key)
}

export function useModuleParams() {
  return { open, register, isOpen, anyOpenFor, show, close, closeFor, toggle }
}
