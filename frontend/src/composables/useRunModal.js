// 运行/试运行/时间线放大弹窗（#runmodal）。旧版 app.js:1750-1774 的等价迁移。
// openRunModal(title, html) 与旧版同语义：把日志区 innerHTML 拷进弹窗、回到顶部。
// 模块级单例：缺陷修复页签的放大按钮、长任务页签的时间线放大都指向它。
import { ref } from 'vue'

const visible = ref(false)
const title = ref('运行结果')
const bodyHtml = ref('')

function openRunModal(t, html) {
  if (!html || !String(html).trim()) return
  title.value = t || '运行结果'
  bodyHtml.value = html
  visible.value = true
  // 与旧版一致：打开后回到顶部
  requestAnimationFrame(() => {
    const el = document.querySelector('#runmodal-body')
    if (el) el.scrollTop = 0
  })
}

function close() {
  visible.value = false
}

export function useRunModal() {
  return { visible, title, bodyHtml, openRunModal, close }
}
