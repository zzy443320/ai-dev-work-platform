// 前端纯函数的直调断言（不需要浏览器，也不需要把函数挂到 window 上）。
//
//   node frontend/tests/pure.test.mjs
//
// 为什么要这一份：`check_stages_ui.py` 与 `check_kb_render.py` 是旧版原生界面时代的
// 用例，靠 `page.evaluate("stagesFromRunResults(...)")` / `renderMarkdown(...)` 直调
// 挂在 window 上的全局函数。Vue 收口时那个 debugBridge 全局层被删了，两条用例整体
// 变成 ReferenceError，coverage 也随手迁成了「DOM 上有没有 class」这种存在性断言——
// 于是「部分 invalid → warn、全 invalid → fail、拉取失败 → 后四步 off」这类分支判定
// 和「代码块里的空行不能把 SEARCH/REPLACE 腰斩」这类渲染规则从此没人管。
// 这两组判定恰恰是最容易悄悄写歪的：改一行三元表达式，界面看起来还是"对的"。
//
// 所以把这些纯函数请出 composable（utils/stages.js），在这里直调断言。
import assert from 'node:assert/strict'
import {
  LEASE_STAGE_IDX, LIVE_STAGE_IDX, STAGE_STATES, stageAdvance, stagesFromLease,
  stagesFromProbeResults, stagesFromRunResults, stagesIdle, stagesRunning,
} from '../src/utils/stages.js'
import { escapeHtml, renderMarkdown } from '../src/utils/format.js'

let failed = 0
let passed = 0

function check(name, fn) {
  try {
    fn()
    passed += 1
    console.log('  PASS ' + name)
  } catch (e) {
    failed += 1
    console.log('  FAIL ' + name + '  ' + String(e.message).split('\n')[0])
  }
}

const row = (over = {}) => ({
  status: 'pending', locate_empty: false, gate_ok: true, kb_path: 'x/1.md', ...over,
})
const run = (...rows) => ({ source: 'ones', results: rows })

console.log('== 步骤条推导 ==')

check('运行中只有第 1 步 active', () => {
  assert.deepEqual(stagesRunning(), ['active', 'idle', 'idle', 'idle', 'idle'])
  assert.deepEqual(stagesIdle(), ['idle', 'idle', 'idle', 'idle', 'idle'])
})

// 外部脚本 / 别的标签页发起的运行，步骤条靠服务端占用租约推导（不是 SSE 事件）
check('租约快照：当前步 active，之前的 done，之后的 idle', () => {
  assert.deepEqual(stagesFromLease({ running: true, kind: 'run', stage: 'fetch' }),
    ['active', 'idle', 'idle', 'idle', 'idle'])
  assert.deepEqual(stagesFromLease({ running: true, kind: 'run', stage: 'gate' }),
    ['done', 'done', 'done', 'active', 'idle'])
  assert.deepEqual(stagesFromLease({ running: true, kind: 'run', stage: 'kb' }),
    ['done', 'done', 'done', 'done', 'active'])
})

check('租约快照：取证环节（verify/repro/agent）要归位，否则步骤条会退回「定位」', () => {
  // 修复循环那 7 轮是整条流水线最久的一段，名儿没映射上就会看起来"卡在定位"
  assert.equal(stagesFromLease({ running: true, kind: 'run', stage: 'agent' })[2], 'active')
  assert.equal(stagesFromLease({ running: true, kind: 'run', stage: 'repro' })[2], 'active')
  assert.equal(stagesFromLease({ running: true, kind: 'run', stage: 'verify' })[1], 'active')
  assert.deepEqual(Object.keys(LEASE_STAGE_IDX).sort(),
    ['agent', 'fetch', 'gate', 'kb', 'locate', 'patch', 'proposal', 'repro', 'verify'])
})

check('租约快照：没映射上的新阶段名不瞎猜，按已取到工单往下推一格', () => {
  assert.equal(stagesFromLease({ running: true, kind: 'run', stage: 'brand_new' })[0], 'active')
  assert.equal(stagesFromLease(
    { running: true, kind: 'run', stage: 'brand_new', index: 2 })[1], 'active')
})

check('租约快照：试运行后三步恒 off，且不会越过第 2 步', () => {
  assert.deepEqual(stagesFromLease({ running: true, kind: 'probe', stage: 'locate' }),
    ['done', 'active', 'off', 'off', 'off'])
  // 试运行里万一冒出 gate/kb 这类名，也只能停在第 2 步
  assert.equal(stagesFromLease({ running: true, kind: 'probe', stage: 'kb' })[1], 'active')
  assert.equal(stagesFromLease({ running: true, kind: 'probe', stage: 'kb' })[3], 'off')
})

check('租约快照：空闲返回 null（由调用方决定退回哪套，不假装五步全绿）', () => {
  assert.equal(stagesFromLease(null), null)
  assert.equal(stagesFromLease({ running: false }), null)
})

check('全部成功 → 5 步 done', () => {
  assert.deepEqual(stagesFromRunResults(run(row())), ['done', 'done', 'done', 'done', 'done'])
})

check('拉取失败 → 第 1 步 fail，后面全 off（没有发生的事不显示成完成）', () => {
  assert.deepEqual(stagesFromRunResults({ source: 'error', results: [] }),
    ['fail', 'off', 'off', 'off', 'off'])
  assert.deepEqual(stagesFromRunResults({ source: 'ones', results: [] }),
    ['fail', 'off', 'off', 'off', 'off'])
  assert.deepEqual(stagesFromRunResults(null), ['fail', 'off', 'off', 'off', 'off'])
})

check('locate_empty 护栏触发 → 定位步 warn', () => {
  assert.equal(stagesFromRunResults(run(row({ locate_empty: true })))[1], 'warn')
})

check('部分 invalid → warn，全部 invalid → fail', () => {
  assert.equal(stagesFromRunResults(
    run(row({ status: 'invalid' }), row()))[2], 'warn')
  assert.equal(stagesFromRunResults(
    run(row({ status: 'invalid' }), row({ status: 'invalid' })))[2], 'fail')
})

check('闸门 gate_ok=false：部分 warn / 全部 fail', () => {
  assert.equal(stagesFromRunResults(run(row({ gate_ok: false }), row()))[3], 'warn')
  assert.equal(stagesFromRunResults(run(row({ gate_ok: false })))[3], 'fail')
})

check('没落知识卡：部分 warn / 全部 fail', () => {
  assert.equal(stagesFromRunResults(run(row({ kb_path: '' }), row()))[4], 'warn')
  assert.equal(stagesFromRunResults(run(row({ kb_path: '' })))[4], 'fail')
})

check('多条工单取最坏的一档（有一条没落卡就不是全绿）', () => {
  const arr = stagesFromRunResults(run(row(), row({ kb_path: '' })))
  assert.equal(arr[4], 'warn', JSON.stringify(arr))
  const worse = stagesFromRunResults(run(row({ status: 'invalid' }),
                                         row({ status: 'invalid' }), row()))
  assert.equal(worse[2], 'warn', JSON.stringify(worse))
})

check('试运行只关联前两步，3-5 恒为 off', () => {
  assert.deepEqual(stagesFromProbeResults({ source: 'ones', results: [{}, {}] }),
    ['done', 'done', 'off', 'off', 'off'])
  assert.deepEqual(stagesFromProbeResults({ source: 'error', fetch_error: 'boom' }),
    ['fail', 'fail', 'off', 'off', 'off'])
  assert.deepEqual(stagesFromProbeResults({ source: 'ones', results: [] }),
    ['done', 'warn', 'off', 'off', 'off'])
})

check('试运行定位：全错 fail，部分错或护栏 warn，全好 done', () => {
  assert.equal(stagesFromProbeResults(
    { results: [{ analysis_error: 'x' }, { analysis_error: 'y' }] })[1], 'fail')
  assert.equal(stagesFromProbeResults(
    { results: [{ analysis_error: 'x' }, {}] })[1], 'warn')
  assert.equal(stagesFromProbeResults(
    { results: [{ locate_empty: true }] })[1], 'warn')
})

check('SSE 推进：start→active，done→done 并点亮下一步', () => {
  let s = stagesRunning()
  s = stageAdvance(s, 'fetch', 'done')
  assert.equal(s[0], 'done')
  assert.equal(s[1], 'active', '定位应被自动点亮')
  s = stageAdvance(s, 'gate', 'warn')
  assert.equal(s[LIVE_STAGE_IDX.gate], 'warn')
})

check('未知 stage（verify/agent）不进步骤条', () => {
  const s = stagesRunning()
  assert.deepEqual(stageAdvance(s, 'verify', 'done'), s)
  assert.deepEqual(stageAdvance(s, 'agentic 修复', 'done'), s)
})

check('最后一步 done 不会去点第 6 步', () => {
  const s = stageAdvance(['done', 'done', 'done', 'done', 'idle'], 'kb', 'done')
  assert.equal(s.length, 5)
  assert.equal(s[4], 'done')
})

check('状态取值都在 STAGE_STATES 里（写歪成别的 class 样式就失效了）', () => {
  const all = new Set(STAGE_STATES)
  for (const arr of [stagesRunning(), stagesIdle(),
    stagesFromRunResults(run(row())), stagesFromProbeResults({ results: [{}] })]) {
    for (const s of arr) assert.ok(all.has(s), '未知状态 ' + s)
  }
})

console.log('== Markdown 渲染 ==')

check('标题 / 加粗 / 行内代码', () => {
  const html = renderMarkdown('# 一级\n## 二级\n**重点** 和 `code`')
  assert.ok(html.includes('<h1>一级</h1>'), html)
  assert.ok(html.includes('<h2>二级</h2>'), html)
  assert.ok(html.includes('<strong>重点</strong>'), html)
  assert.ok(html.includes('<code>code</code>'), html)
})

check('代码块里的空行与井号不把块腰斩（SEARCH/REPLACE 的命门）', () => {
  const md = ['## 补丁', '```search-replace', '<<<<<<< SEARCH a.js', 'const x = 1', '',
    '# 像标题的一行', '=======', 'const x = 2', '>>>>>>> REPLACE', '```'].join('\n')
  const html = renderMarkdown(md)
  const blocks = html.match(/<pre><code>/g) || []
  assert.equal(blocks.length, 1, '整段应该只有一个代码块：' + html)
  // 块内正文走过 HTML 转义，所以断言要用转义后的形态
  assert.ok(html.includes('&lt;&lt;&lt;&lt;&lt;&lt;&lt; SEARCH a.js'), html)
  assert.ok(html.includes('const x = 1\n\n# 像标题的一行'),
    '块内的空行必须原样留着：' + html)
  assert.ok(!html.includes('<h1>像标题的一行'), '块内的 # 不该被解析成标题：' + html)
  assert.equal((html.match(/<\/pre>/g) || []).length, 1, html)
})

check('diff 代码块与未闭合围栏（流式输出会截断）', () => {
  const html = renderMarkdown('```diff\n--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n')
  assert.ok(html.includes('<pre><code>'), html)
  assert.ok(html.includes('-x'), html)
  assert.ok(html.includes('</code></pre>'), '未闭合也要收口')
})

check('影响文件表：table + 3 个 th + 空单元格补位', () => {
  const md = '| 文件 | +行 | -行 |\n| --- | --- | --- |\n| a.js | 3 | 1 |\n| b.js | 2 |'
  const html = renderMarkdown(md)
  assert.ok(html.includes('<table>'), html)
  assert.equal((html.match(/<th>/g) || []).length, 3, html)
  assert.equal((html.match(/<td>/g) || []).length, 6, '缺列也要补齐：' + html)
  assert.ok(html.includes('md-scroll'), '宽表要包在横向滚动容器里：' + html)
})

check('现象区多段落：空行分段、段内换行折成 <br>', () => {
  const md = '第一段一行\n第一段二行\n\n第二段\n\n第三段'
  const html = renderMarkdown(md)
  assert.equal((html.match(/<p>/g) || []).length, 3, html)
  assert.ok(html.includes('第一段一行<br>第一段二行'), html)
})

check('列表 ul / ol', () => {
  assert.ok(renderMarkdown('- a\n- b').includes('<ul><li>a</li><li>b</li></ul>'))
  assert.ok(renderMarkdown('1. a\n2. b').includes('<ol><li>a</li><li>b</li></ol>'))
  assert.ok(renderMarkdown('---').includes('<hr>'))
})

check('站内相对链接交给回调，外链照常新窗口', () => {
  const html = renderMarkdown('[100001](逻辑/100001.md) [官网](https://example.com/x?a=1&b=2)')
  assert.ok(html.includes('data-kb-link='), html)
  assert.ok(html.includes('逻辑/100001.md'), html)
  assert.ok(html.includes('target="_blank"'), 'https 外链必须真的能点：' + html)
  assert.ok(html.includes('https://example.com/x?a=1&amp;b=2'), html)
})

check('放宽 URL 字符类后，引号与尖括号仍然进不了标签（属性注入面）', () => {
  const html = renderMarkdown('[x](https://a.cn/?"onmouseover="alert(1))')
  assert.ok(!/<a[^>]*\sonmouseover/i.test(html), html)
  assert.ok(!html.includes('"onmouseover="alert'), html)
  // `javascript:` 这类伪协议不会被当成外链 href（相对链接分支只产出 href="#"）
  const bad = renderMarkdown('[y](javascript:alert(1))')
  assert.ok(!/<a[^>]+href="javascript/i.test(bad), '不该产出 javascript: 链接：' + bad)
})

check('HTML 与引号必须转义（正文里出现工单原文，这是 XSS 面）', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> "引号" \'单引\'')
  assert.ok(!html.includes('<img'), html)
  assert.ok(html.includes('&lt;img'), html)
  assert.equal(escapeHtml('<a href="x">&\''), '&lt;a href=&quot;x&quot;&gt;&amp;&#39;')
})

check('空输入不炸', () => {
  assert.equal(renderMarkdown(''), '')
  assert.equal(renderMarkdown(null), '')
  assert.equal(renderMarkdown(undefined), '')
})

console.log('\n' + (failed ? `${failed} FAIL / ${passed} pass`
  : `ALL ${passed} CHECKS PASSED`))
process.exit(failed ? 1 : 0)
