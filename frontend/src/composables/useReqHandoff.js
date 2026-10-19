// 「需求开发 → Figma 转代码」的交接条（模块级单例）。
//
// 为什么要有它：需求分诊判出「需要设计稿」之后，用户下一步就是去拿设计稿实现代码。
// 让他手工把需求再抄一遍到另一个页签，既费事又会抄漏上下文（页面结构、状态、边界）。
// 这里只传**需求原文与约定**，不传分诊结论 —— 结论是给人看的，不是给生成器的输入。
//
// ⚠️ 必须是模块级单例：ReqPane 写、ReqdevPane 读，两个组件各自 useReqHandoff()
// 拿到的必须是同一份 ref（同 useArtifacts / useToast 的理由）。
// ⚠️ 用掉即清（take）：同一条草稿重复套用，会把用户后来手改的需求描述覆盖掉。
import { ref } from 'vue'

/** 待交接的草稿 { requirement, notes, source, at }；null = 没有 */
const draft = ref(null)

export function useReqHandoff() {
  /** 从分诊页发出交接 */
  function send(payload) {
    draft.value = {
      requirement: String((payload && payload.requirement) || ''),
      notes: String((payload && payload.notes) || ''),
      source: String((payload && payload.source) || ''),
      at: Date.now(),
    }
  }

  /** 取走并清空（读方调用；没有草稿返回 null） */
  function take() {
    const d = draft.value
    draft.value = null
    return d
  }

  return { draft, send, take }
}
