// 「一组 ref ↔ 一个 model 对象」的薄适配层。
//
// 为什么需要：schema 驱动的渲染器（ParamField）只能按 `field.key` 读写值，
// 而各模块的状态是一堆散在 composable 里的 ref（testFile / testFramework / …）。
// 与其让渲染器认识每个 composable，不如让 composable 交出这几个 ref：
//
//     const params = useParamModel({ testFile, testFramework, testFocus })
//     <ParamLayout name="codetest" :model="params">
//
// 读写都落回原 ref，所以提交逻辑（runTask 里怎么拼 payload）完全不用改 ——
// 这次改版只动「怎么展示参数」，不动「参数去哪儿」。
import { isRef } from 'vue'

export function useParamModel(refs) {
  function get(key) {
    const r = refs[key]
    if (r === undefined) return undefined
    return isRef(r) ? r.value : r
  }

  function set(key, value) {
    const r = refs[key]
    if (!r) {
      // schema 里写了某个 key 而 composable 没给：静默失败会表现为「改了没生效」，
      // 比报错难查得多，所以这里明着喊
      console.warn(`[params] model 里没有 ${key}，这条参数的改动不会生效`)
      return
    }
    if (isRef(r)) r.value = value
    else Object.assign(r, value)
  }

  /** 派生写：switch-group 这类「一个参数多个子开关」用它，避免在组件里拼对象 */
  function setPart(key, part, value) {
    const cur = get(key) || {}
    set(key, { ...cur, [part]: value })
  }

  return { get, set, setPart, keys: Object.keys(refs) }
}
