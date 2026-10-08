// 六个「任务型」模块（缺陷修复 / 长任务作业 / 需求开发 / 接口联调 / 代码测试 / 扩展能力）
// 右侧参数抽屉的开合状态 —— 模块级单例。
//
// 为什么要抽屉：这几个模块的主体应该只放**大模型的处理过程**与**最终产物**，
// 而表单（Figma 链接、接口文档、目标文件路径、作业参数……）是一次性输入，
// 常驻在主区里会把过程和产物挤到半屏以下。所以输入整体搬进右侧抽屉，
// 主区顶部只留一条「当前参数摘要 + 修改参数」入口（见 components/ParamBar.vue）。
//
// key 的形态：`页签名`（大多数模块一个抽屉）或 `页签名:槽位`（扩展能力有「技能」
// 与「MCP」两个表单，各占一个抽屉）。外壳用 anyOpenFor(页签) 判断要不要给主列让位，
// 所以一个页签里开几个抽屉、叫什么名字都不需要外壳配合改代码。
//
// 默认值：任务型模块**默认打开**（第一次进模块时主区还没有产物，此时要填的就是参数）；
// 扩展能力这类「列表才是主体」的模块传 defaultOpen=false，保持原来的「点新增才出表单」。
// 同一个页签内的抽屉互斥（show 一个就关其他的）—— 它们占的是同一个右侧位置，
// 同时开两个就是重叠。
//
// 关掉之后本次会话内保持关闭（切页签不重置），刷新回到各自的默认值。
// **刻意不写 localStorage**：抽屉是浮层，记住「上次是关的」会让新用户以为参数区不存在。
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
