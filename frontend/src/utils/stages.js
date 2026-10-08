// 流水线步骤条的状态推导。旧版 app.js:1371-1424 的逐条搬运。
//
// 为什么从 useDefect.js 里拆出来：这几个函数是**纯函数**（输入结果数据，输出 5 个
// 状态字符串），但它们原先住在一个 import 了 vue / element-plus toast 的模块里，
// Node 直调会连带拉起整个浏览器依赖树。拆到 utils 后可以直接用
// `node frontend/tests/pure.test.mjs` 断言各分支——退役掉的 check_stages_ui.py 就是
// 因为拿不到 window 全局而整组失效，那套「partial invalid → warn、拉取失败 → off」
// 的分支判定自此没人管，正是最容易悄悄写歪的那类。

/** 步骤条状态集合。旧版 app.js:1371 —— 与 style.css 的 .stage 类名一一对应 */
export const STAGE_STATES = ['active', 'done', 'warn', 'fail', 'off', 'idle']

/** 事件里的 stage 名 → 步骤条下标。旧版 app.js:1429 */
export const LIVE_STAGE_IDX = { fetch: 0, locate: 1, patch: 2, proposal: 2, gate: 3, kb: 4 }

/** 运行开始：只有「拉取」进入进行中（呼吸），其余待定 */
export function stagesRunning() {
  return ['active', 'idle', 'idle', 'idle', 'idle']
}

export function stagesIdle() {
  return ['idle', 'idle', 'idle', 'idle', 'idle']
}

/** 完整流水线结束后的步骤条。旧版 app.js:1384 */
export function stagesFromRunResults(data) {
  const results = (data && data.results) || []
  if (!data || data.source === 'error' || !results.length) {
    return ['fail', 'off', 'off', 'off', 'off'] // 拉取失败/为空，后面没有发生
  }
  const arr = ['done', 'done', 'done', 'done', 'done']
  const rate = (bad) => results.filter(bad).length
  // 定位：locate_empty=护栏触发（警告）；invalid 由提案步表达
  const locBad = rate((x) => x.locate_empty)
  arr[1] = locBad ? 'warn' : 'done'
  // 生成提案：invalid=没有可用补丁
  const invalid = rate((x) => x.status === 'invalid')
  arr[2] = invalid === results.length ? 'fail' : (invalid ? 'warn' : 'done')
  // 闸门：gate_ok=false（含 gate_failed 待人工确认）
  const gateBad = rate((x) => x.gate_ok === false)
  arr[3] = gateBad === results.length ? 'fail' : (gateBad ? 'warn' : 'done')
  // 沉淀：kb_path 为空即没落卡
  const noKb = rate((x) => !x.kb_path)
  arr[4] = noKb === results.length ? 'fail' : (noKb ? 'warn' : 'done')
  return arr
}

/** 试运行只涉及前两步；3-5 标记为 off（不适用）。旧版 app.js:1406 */
export function stagesFromProbeResults(data) {
  const off = ['off', 'off', 'off', 'off', 'off']
  const results = (data && data.results) || []
  if (!data || data.source === 'error' || data.fetch_error) {
    return ['fail', 'fail', off[2], off[3], off[4]]
  }
  off[0] = 'done'
  if (!results.length) { off[1] = 'warn'; return off } // 接口通但没工单
  const errs = results.filter((x) => x.analysis_error).length
  const empty = results.filter((x) => x.locate_empty).length
  off[1] = errs === results.length ? 'fail' : (errs || empty ? 'warn' : 'done')
  return off
}

/** 推进一格：start→active，done→done 并把下一步点亮。旧版 app.js:1502 */
export function stageAdvance(states, stageName, status) {
  const idx = LIVE_STAGE_IDX[stageName]
  if (idx == null) return states            // verify 等只进日志行，不映射步骤条
  const next = states.slice()
  if (status === 'start') {
    next[idx] = 'active'
    return next
  }
  next[idx] = status === 'done' ? 'done' : status
  if (status === 'done' && idx < 4 && next[idx + 1] === 'idle') {
    next[idx + 1] = 'active'
  }
  return next
}
