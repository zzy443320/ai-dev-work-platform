// 参数元数据（schema）的前端侧唯一入口。
//
// 数据来自 `GET /api/params/schema`（定义在 web/params_schema.py）。五个任务模块的
// 参数控件、分组、范围、默认值、说明文字**全部由它驱动**，模板里不再手写
// 「标签 + 输入框」——那是改版前形态改不动的根因：信息散在 Vue 模板、
// composable 的 ref 初值、后端路由的 clamp 三处，谁改了谁不知道。
//
// 加载时机：**在 app 挂载之前** await（见 main.js 的 bootstrap）。
// 这样 composable 里 `ref(schemaDefault(...))` 在 setup 阶段就能同步拿到默认值，
// 不必给每个表单加「加载中」的骨架屏，也不会出现「先渲染空表单再回填」的闪动。
// 拉不到就直接挡在挂载前（main.js 里出错误页），而不是静默渲出一张骗人的表单。
import { computed, reactive } from 'vue'
import { http } from '../api/client.js'

const state = reactive({
  modules: null,      // null = 还没拉到
  problems: [],       // 后端 validate() 的结果，非空说明元数据自己写错了
  error: '',
})

let inflight = null

export function loadParamSchema() {
  if (!inflight) {
    inflight = http.get('/api/params/schema')
      .then((d) => {
        state.modules = (d && d.modules) || {}
        state.problems = (d && d.problems) || []
        if (state.problems.length) {
          // 不挡界面，但必须让控制台与自检用例看见：schema 写错会以
          // 「填了却跑不对」的形式还回来，越早暴露越好
          console.warn('[params] schema 自检有问题：', state.problems.join('; '))
        }
        return d
      })
      .catch((e) => {
        state.error = (e && e.message) || String(e)
        throw e
      })
  }
  return inflight
}

/** 取某个模块的参数定义。schema 没到位时返回 null（调用方按「还没准备好」处理） */
export function paramModule(name) {
  return (state.modules && state.modules[name]) || null
}

export function paramFields(name) {
  return (paramModule(name) || {}).fields || []
}

/** strip = 就地控件条；rail = 常驻参数轨。分档规则在后端，前端不自己判断 */
export function paramLayout(name) {
  return (paramModule(name) || {}).layout || 'strip'
}

/** 某条参数的默认值（composable 初始化时用，保证默认值只有一处定义） */
export function schemaDefault(module, key, fallback) {
  const f = paramFields(module).find((x) => x.key === key)
  if (!f || f.default === undefined) return fallback
  return f.default
}

/** 值与默认值是否等价（对象/数组按浅比较，用于「默认」角标与「恢复默认」） */
export function isDefaultValue(value, def) {
  if (value === def) return true
  if (def && typeof def === 'object') {
    const a = value && typeof value === 'object' ? value : {}
    const keys = new Set([...Object.keys(def), ...Object.keys(a)])
    for (const k of keys) if (a[k] !== def[k]) return false
    return true
  }
  // 数字与字符串数字（input type=number 清空时是 ''）不算等价，交给上面严格比
  return false
}

export function useParamSchema() {
  return {
    state,
    ready: computed(() => !!state.modules),
    moduleOf: paramModule,
    fieldsOf: paramFields,
    layoutOf: paramLayout,
    schemaDefault,
  }
}
