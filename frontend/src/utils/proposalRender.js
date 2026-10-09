// 提案详情弹窗（#pmodal）的各分区 HTML 渲染。
// 旧版 app.js:254-537（renderProposal 及 pmAnalysis/pmDiff/pmGate/pmIssues/
// pmShots/pmConsole/pmBlocks）的逐行移植 —— 输出的 HTML 与旧版逐字节一致，
// 既有用例与 style.css 的选择器都按它写的。
import { escapeHtml } from './format.js'
import { GATE_TEXT, PAGE_STATUS_TEXT, PAGE_VERDICT_TEXT, STATUS_TEXT, agentLabel, agentTone, looksNonFrontend, mdBold } from './labels.js'
import { relTime } from './format.js'

/** 先例的「人工结局」中文（与后端 _human_decision 的取值一一对应） */
export const OUTCOME_TEXT = {
  applied: '人工采纳',
  rejected: '人工拒绝',
  force_applied: '强制采纳',
  applied_then_undone: '采纳后已撤销',
  pending: '未采纳/待审',
  unknown: '未知',
}

/** 闸门徽标（HTML 串版）。旧版 app.js:79 —— labels.js 里那个是结构化版，供组件用 */
export function gateBadgeHtml(level, ok) {
  const lv = level || 'none'
  const failed = ok === false ? ' · 未通过' : ''
  return `<span class="gate-badge gate-${escapeHtml(lv)}">闸门 ${escapeHtml(GATE_TEXT[lv] || lv)}${escapeHtml(failed)}</span>`
}

/** 闸门层级提示。旧版 app.js:45 */
export const GATE_HINT = {
  explicit: 'explicit：逐条执行了你在「验收闸门」里写的命令，可信度最高。',
  package_json: 'package_json：命令是从目标仓库 package.json 的 scripts 自动挑的，请确认它确实是团队平时用来验收的那条。',
  sandbox: 'sandbox：验收命令是**打在补丁上、在仓库外的临时沙箱里真跑**的（agentic 修复循环的产物）。这是目前可信度最高的一层——你的工作区没有被写入，跑的就是这份补丁。',
  degraded: 'degraded：仓库里没有任何可用的校验命令，只做了括号/引号闭合与 node --check。这只证明代码没被写坏，不证明类型、测试通过。',
  none: 'none：什么都没检查过，这份提案的正确性 100% 取决于你人工审查下面的 diff。',
}

/** 截图状态文案。旧版 app.js:51 */
export const SHOT_TEXT = {
  ok: '渲染无报错',
  error_visible: '页面有报错',
  blank: '页面空白',
  unreachable: '应用不可达',
  skipped: '未执行',
  error: '采集失败',
  none: '未执行',
}

function kv(k, v, cls) {
  return `<div class="pm-line${cls ? ` ${cls}` : ''}"><b>${escapeHtml(k)}</b><span>${escapeHtml(v || '（空）')}</span></div>`
}

function plainLines(text, cls) {
  return escapeHtml(text || '').split('\n')
    .map((l) => `<span class="dl ${cls}">${l === '' ? ' ' : l}</span>`).join('\n')
}

function diffLines(text) {
  return escapeHtml(text || '').split('\n').map((line) => {
    let cls = 'ctx'
    if (/^(---|@@|\+\+\+)/.test(line)) cls = line.startsWith('@@') ? 'hunk' : 'hdr'
    else if (line.startsWith('+')) cls = 'add'
    else if (line.startsWith('-')) cls = 'del'
    else if (line.startsWith('\\')) cls = 'note'
    return `<span class="dl ${cls}">${line === '' ? ' ' : line}</span>`
  }).join('\n')
}

export function pmAnalysis(p) {
  const a = p.analysis || {}
  const d = p.decision || {}
  let html = '<h4>根因 / 说明 / 预防</h4>'
  if (a.locate_empty) {
    html += `<div class="pm-line bad"><b>定位失败</b><span>定位阶段没有在仓库里找到嫌疑文件，AI 拿不到任何源码，`
      + `按安全规则拒绝盲猜补丁（这是刻意的护栏，不是故障）。常见原因：① 工单描述里没有可搜索的`
      + `代码标识（可把组件路径/报错关键字补进工单描述后重跑）；② 该功能不在当前配置的仓库里`
      + `（检查侧边栏「仓库路径」）；③ 关键词都被第三方文件命中，已被过滤。</span></div>`
  }
  if (looksNonFrontend(a) && !(a.patch_blocks || []).length) {
    html += `<div class="pm-line bad"><b>非前端缺陷</b><span>模型判断根因不在当前配置的前端仓库`
      + `（后端/接口/数据层），因此未生成前端补丁——这是结论而非故障。建议把工单转后端排查，`
      + `或补充后端日志/接口返回后再重跑。判断依据见下方根因。</span></div>`
  }
  html += kv('根因', a.root_cause)
  html += kv('说明', a.explanation)
  html += kv('预防措施', a.prevention)
  if ((a.suspect_files || []).length) {
    html += `<div class="pm-line"><b>定位文件</b><span>${a.suspect_files.slice(0, 10)
      .map((f) => `<code>${escapeHtml(f)}</code>`).join(' ')}</span></div>`
  }
  if ((a.keywords || []).length) {
    html += `<div class="pm-line"><b>搜索关键词</b><span>${a.keywords.slice(0, 12)
      .map((k) => `<code>${escapeHtml(k)}</code>`).join(' ')}</span></div>`
  }
  if (a.ai_error) html += kv('AI 侧错误', a.ai_error, 'bad')
  if (d.decided_at) {
    html += `<div class="pm-line"><b>上次决策</b><span>${d.approved ? '采纳' : '拒绝'} · ${escapeHtml(String(d.decided_at).replace('T', ' '))}`
      + `${d.forced ? ' · 强制越过闸门' : ''}${d.note ? ` · 备注：${escapeHtml(d.note)}` : ''}</span></div>`
  }
  if (p.kb_path) {
    html += `<div class="pm-line"><b>知识卡片</b><span><code>${escapeHtml(shortKbPath(p.kb_path))}</code></span></div>`
  }
  return html
}

/** 旧版此处用 shortPath —— 50 字符截断版 */
function shortKbPath(p) {
  if (!p) return '(未配置)'
  return String(p).length > 50 ? '…' + String(p).slice(-47) : p
}

/**
 * agentic 修复轨迹：这份补丁「是怎么来的、真跑过没有、跑成什么样」。
 *
 * 审批界面最该回答的问题是「凭什么信它」，而旧提案只能给出「模型说它改对了 + 一个和
 * 补丁无关的静态闸门」。这里把每一轮的候选补丁、命令真实退出码与输出原文摆出来，
 * 让人工复核时看的是证据而不是模型的作文。
 */
function pmAgent(p) {
  const a = p.agent || {}
  if (!a || !Object.keys(a).length) {
    return '<h4>修复轨迹</h4><p class="muted">这份提案来自旧的一次成型链路'
      + '（没有 agentic 循环记录）：补丁未经过任何真实运行验证，请重点人工审查 diff。</p>'
  }
  const tone = agentTone(a.conclusion)
  let html = `<h4>修复轨迹 <span class="tag agent-${tone}">${escapeHtml(agentLabel(a.conclusion))}</span></h4>`
  html += `<p class="muted">共 ${escapeHtml(String(a.rounds ?? 0))} 轮 / 提交 `
    + `${escapeHtml(String((a.attempts || []).length))} 次候选补丁 / 耗时 `
    + `${escapeHtml(String(a.elapsed_seconds ?? '—'))}s（预算 ${escapeHtml(String(a.max_rounds ?? '—'))} 轮）`
    + (a.best_round ? ` · 最终采用第 ${escapeHtml(String(a.best_round))} 轮的补丁` : '') + '。</p>'
  if (a.final_summary) html += kv('AI 的结论', a.final_summary)

  const base = a.baseline || {}
  if (Object.keys(base).length) {
    const bad = Object.entries(base).filter(([, v]) => !v.ok)
    html += `<div class="pm-line"><b>基线</b><span>未打补丁时：`
      + Object.entries(base).map(([k, v]) => `${escapeHtml(k)} ${v.ok ? '✓' : '✗'}`).join('、')
      + (bad.length ? `（${bad.length} 项本来就红，这些不是本次改动引入的）` : '')
      + '</span></div>'
  }
  const sb = a.sandbox || {}
  if (sb.mode) {
    html += `<div class="pm-line"><b>沙箱</b><span>后端 ${escapeHtml(sb.mode)}`
      + (sb.linked_dep_dirs && sb.linked_dep_dirs.length
        ? ` · 已链接 ${sb.linked_dep_dirs.length} 个依赖目录` : '')
      + (sb.available ? ' · 你的工作区未被写入' : ' · <b>不可用</b>')
      + `</span></div>`
    if ((sb.notes || []).length) {
      html += `<ul class="note-list">${sb.notes.map((n) => `<li>${mdBold(n)}</li>`).join('')}</ul>`
    }
  }
  const rp = a.repro || {}
  if (Object.keys(rp).length && rp.status !== 'disabled') {
    const strength = (rp.harness || {}).strength || ''
    const stateText = {
      green: '已在沙箱里跑红→跑绿（这条缺陷有了可执行判据）',
      red: '已跑红，但补丁还没让它转绿',
      blocked: `重写 ${rp.rewrites || 0} 次仍未复现出缺陷，已放弃复现环节`,
      none: rp.attempted ? '尝试过但没建立有效判据' : '未做复现（模型没有提交用例）',
      disabled: '配置已关闭复现',
    }[rp.status] || rp.status
    html += `<div class="pm-line${rp.status === 'green' ? '' : ' bad'}"><b>复现用例</b>`
      + `<span>${escapeHtml((rp.harness || {}).name || '未知跑器')}`
      + (strength ? ` · 证据强度 ${escapeHtml((rp.harness || {}).strength_label || strength)}` : '')
      + ` → ${escapeHtml(stateText)}</span></div>`
    if (rp.path) {
      html += `<div class="pm-line"><b>用例路径</b><span><code>${escapeHtml(rp.path)}</code>`
        + (rp.artifact_id
          ? ` · 已挂成产出物 <code>${escapeHtml(rp.artifact_id)}</code>，在「缺陷修复 → 复现用例」里采纳才会进仓库`
          : ' · 只存在于沙箱，跑完即回收') + '</span></div>'
      if (rp.cmd) html += `<div class="pm-line"><b>复现命令</b><span><code>${escapeHtml(rp.cmd)}</code></span></div>`
      if (rp.content) {
        html += `<details class="fold"><summary>用例原文（${rp.content.length} 字符）</summary>`
          + `<pre class="diff">${escapeHtml(rp.content)}</pre></details>`
      }
      const lastRun = (rp.runs || [])[Math.max(0, (rp.runs || []).length - 1)]
      if (lastRun && lastRun.output) {
        html += `<details class="fold"><summary>这一次运行的真实输出（${escapeHtml(lastRun.state || '')}）</summary>`
          + `<pre class="check-out">${escapeHtml(lastRun.output)}</pre></details>`
      }
    }
    if (strength === 'assert') {
      html += '<p class="muted">⚠ 这个仓库没有单测跑器，用例是**机械断言脚本**：'
        + '它能证明「该改的地方确实改了」，不能证明「页面上的现象消失了」。</p>'
    }
  }
  if (a.verified_via) {
    const via = {
      'repro-test': '证据来源：针对工单现象的单测从红转绿（最强）',
      'repro-assert': '证据来源：可执行断言脚本从红转绿（机械判据，弱于单测）',
      'gate-command': '证据来源：验收命令从红转绿（只证明构建/类型/测试没坏，不证明现象消失）',
    }[a.verified_via] || ''
    if (via) html += `<div class="pm-line"><b>verified 靠什么</b><span>${escapeHtml(via)}</span></div>`
  }
  const rw = a.rework_ref || p.rework || {}
  if (rw && (rw.note || rw.verdict)) {
    html += `<div class="pm-line bad"><b>人工返工判据</b><span>${escapeHtml(rw.note || rw.verdict)}`
      + (rw.at ? `（${escapeHtml(String(rw.at).replace('T', ' ').slice(0, 19))}）` : '')
      + '</span></div>'
    html += '<p class="muted">这一版是带着上面那条反馈重跑的：复现用例必须能把该现象跑红，'
      + '上一版被拒绝的改法也在先例里，模型不许原样重演。</p>'
  }
  const pg = a.page_after || {}
  if (Object.keys(pg).length) {
    const v = pg.verdict || ''
    const cls = v === 'gone' ? '' : (v === 'same' || pg.status === 'unusable' ? ' bad' : '')
    html += `<div class="pm-line${cls}"><b>页面复验</b><span>`
      + `${escapeHtml(PAGE_STATUS_TEXT[pg.status] || pg.status || '—')}`
      + (v ? ` · 判读 ${escapeHtml(PAGE_VERDICT_TEXT[v] || v)}` : '') + '</span></div>'
    if (pg.command) html += `<div class="pm-line"><b>起的服务</b><span><code>${escapeHtml(pg.command)}</code> → ${escapeHtml(pg.url || '')}</span></div>`
    if (pg.evidence) html += kv('判读依据', pg.evidence)
    if (pg.reason) html += kv('没做成的原因', pg.reason, 'bad')
    if ((pg.new_errors || []).length) {
      html += `<div class="pm-line bad"><b>新出现的报错</b><span>${escapeHtml(pg.new_errors.join(' / ').slice(0, 400))}</span></div>`
    }
    if ((pg.cleared_errors || []).length) {
      html += `<div class="pm-line"><b>消失的报错</b><span>${escapeHtml(pg.cleared_errors.join(' / ').slice(0, 400))}</span></div>`
    }
    if (pg.log_tail) {
      html += `<details class="fold"><summary>服务启动日志尾部</summary><pre class="check-out">${escapeHtml(pg.log_tail)}</pre></details>`
    }
  }
  const prec = a.precedents_used || []
  if (prec.length) {
    html += `<div class="pm-line"><b>注入的先例</b><span>${prec.length} 条同类历史缺陷的改法与结局`
      + '（被人工拒绝的也在里面，用来避免重演）</span></div>'
    html += `<ul class="iss-list warn">${prec.map((x) => `<li>${escapeHtml(x.title || x.defect_id || '')}`
      + ` · ${escapeHtml(OUTCOME_TEXT[x.outcome] || x.outcome || '—')}`
      + (x.conclusion ? ` · 循环结论 ${escapeHtml(x.conclusion)}` : '')
      + (x.same_defect ? ' · 本工单上一次尝试' : '') + '</li>').join('')}</ul>`
  }
  const attempts = a.attempts || []
  if (attempts.length) {
    html += `<ul class="check-list">${attempts.map((at) => {
      const v = at.verification || {}
      const state = at.built ? ((v.all_green && !(v.regressions || []).length) ? 'ok' : 'bad') : 'skip'
      const checks = at.checks || {}
      const head = `<b>第 ${escapeHtml(String(at.round))} 轮</b>`
        + `<span>${escapeHtml(String((at.files || []).length))} 个文件</span>`
        + (at.built ? '' : '<span class="tag">补丁未能构建</span>')
        + ((v.fixed || []).length ? `<span class="tag">转绿 ${escapeHtml(v.fixed.join(', '))}</span>` : '')
        + ((v.regressions || []).length
          ? `<span class="tag">⚠ 新弄坏 ${escapeHtml(v.regressions.join(', '))}</span>` : '')
        + ((v.still_failing || []).length
          ? `<span class="tag">仍失败 ${escapeHtml(v.still_failing.join(', '))}</span>` : '')
      const body = Object.entries(checks).map(([k, c]) => `<div class="muted">`
        + `${escapeHtml(k)} → ${c.ok ? '通过' : '失败（退出码 ' + escapeHtml(String(c.returncode)) + '）'}`
        + `</div>${c.output ? `<pre class="check-out">${escapeHtml(c.output)}</pre>` : ''}`).join('')
        || (at.errors || []).map((e) => `<div class="muted">${escapeHtml(e)}</div>`).join('')
      return `<li class="${state}"><div class="check-head">${head}</div>`
        + (at.summary ? `<div class="muted">${escapeHtml(at.summary)}</div>` : '')
        + (body ? `<details class="fold"><summary>这一轮的真实输出</summary>${body}</details>` : '')
        + '</li>'
    }).join('')}</ul>`
  }
  const v = a.verification || {}
  if ((v.regressions || []).length) {
    html += `<div class="pm-line bad"><b>风险提示</b><span>最后一次验证相比基线新弄坏了：`
      + `${escapeHtml(v.regressions.join('、'))}。这类补丁不要直接采纳。</span></div>`
  }
  if ((a.notes || []).length) {
    html += `<ul class="note-list">${a.notes.map((n) => `<li>${mdBold(n)}</li>`).join('')}</ul>`
  }
  if (a.conclusion === 'agent_disabled') {
    html += '<p class="muted">想让修复阶段真跑验证：在配置里开启 agent，并给仓库配上可执行的验收命令'
      + '（如 npx vue-tsc --noEmit / npx vitest run）。</p>'
  }
  return html
}

function pmDiff(p) {
  const changes = (p.patch || {}).changes || []
  let html = '<h4>逐文件 diff</h4>'
  if (!changes.length) {
    return html + '<p class="muted">没有生成任何文件改动 —— 见下方“补丁错误”。</p>'
  }
  html += changes.map((c) => {
    const s = c.stats || {}
    const head = `<div class="diff-file"><span class="df-path">${escapeHtml(c.file_path || '')}</span>`
      + `<span class="dstat add">+${s.added || 0}</span><span class="dstat del">−${s.removed || 0}</span>`
      + `<span class="muted">${c.applied_blocks || 0} 个补丁块</span></div>`
    if (c.error) return head + `<div class="df-bad">${escapeHtml(c.error)}</div>`
    if (!c.diff) return head + '<div class="muted">（该文件没有实际文本改动）</div>'
    let sec = head + `<pre class="diff">${diffLines(c.diff)}</pre>`
    if ((c.warnings || []).length) {
      sec += `<ul class="warn-list">${c.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`
    }
    return sec
  }).join('')
  return html
}

function pmGate(p) {
  const g = p.gate || {}
  const level = g.level || 'none'
  const checks = g.checks || []
  const risky = level === 'degraded' || level === 'none'
  let html = `<h4>验收闸门 ${gateBadgeHtml(level, g.ok)}</h4>`
  html += `<p class="gate-hint${risky ? ' warn' : ''}">${escapeHtml(GATE_HINT[level] || '未知闸门层级，按最不可信处理。')}</p>`
  if (checks.length) {
    html += `<ul class="check-list">${checks.map((c) => {
      const state = c.ok ? 'ok' : (c.skipped ? 'skip' : 'bad')
      return `<li class="${state}"><div class="check-head">`
        + `<b>${escapeHtml(c.name || '')}</b><code>${escapeHtml(c.cmd || '')}</code>`
        + `<span>退出码 ${escapeHtml(String(c.returncode === undefined ? '?' : c.returncode))}</span>`
        + `<span>${escapeHtml(String(c.seconds === undefined ? '—' : c.seconds))}s</span>`
        + (c.skipped ? '<span class="tag">跳过</span>' : '')
        + `</div>${c.output_tail ? `<pre class="check-out">${escapeHtml(c.output_tail)}</pre>` : ''}</li>`
    }).join('')}</ul>`
  } else {
    html += '<p class="muted">没有执行任何检查项。</p>'
  }
  if ((g.notes || []).length) {
    html += `<ul class="note-list">${g.notes.map((n) => `<li>${mdBold(n)}</li>`).join('')}</ul>`
  }
  const ag = (p.apply || {}).gate
  if (ag) {
    html += `<p class="muted">采纳后又在真实工作区重跑了一次闸门：${escapeHtml(GATE_TEXT[ag.level] || ag.level || '?')} · ${ag.ok === false ? '未通过' : '通过'}。</p>`
  }
  return html
}

function pmIssues(p) {
  const patch = p.patch || {}
  const errs = patch.errors || [], rej = patch.rejected || [], warn = patch.warnings || []
  let html = '<h4>补丁错误与告警</h4>'
  if (!errs.length && !rej.length && !warn.length) {
    return html + '<p class="muted">无。所有 SEARCH 块都精确命中了原文件。</p>'
  }
  if (errs.length) {
    html += `<div class="iss-title bad">错误 ${errs.length} 条（补丁不完整，采纳不会写任何文件）</div>`
      + `<ul class="iss-list bad">${errs.map((e) => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`
  }
  if (rej.length) {
    html += `<div class="iss-title bad">被拒绝的块 ${rej.length} 条</div>`
      + `<ul class="iss-list bad">${rej.map((e) => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`
  }
  if (warn.length) {
    html += `<div class="iss-title warn">告警 ${warn.length} 条（不阻断采纳，但需要你看一眼）</div>`
      + `<ul class="iss-list warn">${warn.map((e) => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`
  }
  return html
}

function shotSrc(path) {
  if (!path) return ''
  const base = String(path).split(/[\\/]/).pop()
  return base ? `/screenshots/${encodeURIComponent(base)}` : ''
}

function pmShotOps(v) {
  const note = v.plan_note ? `<div class="muted">${escapeHtml(v.plan_note)}</div>` : ''
  const acts = v.actions || []
  if (!acts.length) return note
  const summary = acts.slice(0, 4).join(' → ')
  const more = acts.length > 4 ? ` …共 ${acts.length} 步` : ''
  const log = v.actions_log || []
  const fails = log.filter((l) => !l.ok)
  const failNote = fails.length
    ? `（${fails.length} 步未执行成功：${fails.slice(0, 2).map((f) => f.op).join('、')}）`
    : ''
  return note + `<div class="muted">复刻操作：${escapeHtml(summary + more)}${escapeHtml(failNote)}</div>`
}

function pmShots(p) {
  const before = p.verify || {}
  const after = ((p.apply || {}).verify || {}).after || p.verify_after || {}
  const cells = [['修复前（提案生成时）', before]]
  // 中间那张是 agentic 循环在**打了补丁的沙箱**里拍的：还没采纳、工作区没动过，
  // 但已经能看到修复后的页面。没有它，用户只能在「采纳之后」才第一次看到效果。
  const sandboxShot = ((p.agent || {}).page_after) || {}
  if (sandboxShot.screenshot) {
    cells.push(['补丁后（沙箱里，未写入你的仓库）',
      { screenshot: sandboxShot.screenshot, route: sandboxShot.route,
        status: sandboxShot.page_status || sandboxShot.status }])
  }
  cells.push(['修复后（采纳写入后）', after])
  const body = cells.map(([label, v]) => {
    const src = shotSrc(v.screenshot)
    const st = v.status || 'none'
    const missing = escapeHtml(v.error || v.reason || '未生成截图')
    return `<div class="shot"><div class="shot-head"><span>${escapeHtml(label)}</span>`
      + `<span class="shot-st ${escapeHtml(st)}">${escapeHtml(SHOT_TEXT[st] || st)}</span></div>`
      + (src ? `<img src="${src}" alt="${escapeHtml(label)}" loading="lazy">`
             : `<div class="shot-missing">${missing}</div>`)
      + (v.route ? `<div class="muted">路由 ${escapeHtml(v.route)}${v.final_url ? ` · ${escapeHtml(v.final_url)}` : ''}</div>` : '')
      + pmShotOps(v)
      + '</div>'
  }).join('')
  return `<h4>页面截图</h4><div class="shots">${body}</div>`
    + '<p class="muted">截图会打开工单对应路由并复刻复现步骤（无步骤时仅打开页面）。<br>空白页会被标为「页面空白」——它不是通过。<br>中间那张来自**打了补丁的临时沙箱**（你的仓库还没被写入），它只证明沙箱里这个页面好了，不能替代真机联调。<br>截图不能替代闸门命令与人工审查 diff。</p>'
}

function pmConsole(p) {
  const before = p.verify || {}
  const after = ((p.apply || {}).verify || {}).after || p.verify_after || {}
  const pick = (v, label) => (v.console_errors || []).concat(v.page_errors || [])
    .map((e) => `[${label}] ${e}`)
  const errs = pick(before, '修复前').concat(pick(after, '修复后'))
  let html = '<h4>console / 页面报错</h4>'
  if (!errs.length) {
    const why = before.error || after.error || before.reason || after.reason
    return html + `<p class="muted">没有捕获到 console 错误或 page error。${escapeHtml(why || '')}</p>`
  }
  return html + `<ul class="iss-list bad">${errs.map((e) => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`
}

function pmBlocks(p) {
  const patch = p.patch || {}
  const blocks = patch.blocks || []
  let html = '<h4>SEARCH/REPLACE 原文</h4>'
  if (!blocks.length) {
    const raw = (p.analysis || {}).patch_canonical || patch.patch_text || ''
    return html + (raw
      ? `<details class="fold"><summary>模型原始输出（${raw.length} 字符）</summary><pre class="diff">${escapeHtml(raw)}</pre></details>`
      : '<p class="muted">模型没有给出可解析的 SEARCH/REPLACE 块。</p>')
  }
  const body = blocks.map((b, i) => `<div class="sr-block">
      <div class="sr-head">#${i + 1} <code>${escapeHtml(b.file_path || '')}</code>
        <span class="tag">${escapeHtml(b.match_mode || '未命中')}</span>
        <span class="muted">第 ${escapeHtml(String(b.match_start === -1 ? '?' : b.match_start))} 行</span></div>
      <div class="sr-label">SEARCH</div><pre class="diff">${plainLines(b.search, 'srch')}</pre>
      <div class="sr-label">REPLACE</div><pre class="diff">${plainLines(b.replace, 'srpl')}</pre>
    </div>`).join('')
  return html + `<details class="fold"><summary>展开 ${blocks.length} 个补丁块（核对模型到底改了哪几行）</summary>${body}</details>`
}

/** 弹窗头部的 meta 行（含分支不一致的红色提示）。旧版 app.js:269-281 */
export function pmMeta(p) {
  const patch = p.patch || {}
  const analysis = p.analysis || {}
  const pre = p.preflight_live || p.preflight || {}
  const branchBad = pre.current_branch && pre.expected_branch && pre.current_branch !== pre.expected_branch
  return [
    ['缺陷', `${p.defect && p.defect.id ? p.defect.id : ''} ${p.defect && p.defect.title ? p.defect.title : ''}`.trim()],
    ['分类', analysis.category],
    ['优先级', p.defect && p.defect.priority],
    ['AI', analysis.ai_mode],
    ['补丁', patch.ok ? '可应用' : '无法应用'],
    ['分支', `${pre.current_branch || '未知'}${pre.expected_branch ? ` → 期望 ${pre.expected_branch}` : ''}`],
    ['工作区', pre.dirty ? '有未提交改动' : '干净'],
    ['创建', relTime(p.created)],
    ['更新', String(p.updated || '').replace('T', ' ').slice(0, 19)],
  ].filter(([, v]) => v).map(([k, v]) =>
    `<span><b>${escapeHtml(k)}:</b> ${escapeHtml(String(v))}</span>`
  ).join('') + (branchBad ? '<span class="cnt bad">分支与配置不一致，采纳会被拒</span>' : '')
}

/** 一次算齐弹窗所有分区的 HTML */
export function renderProposalSections(p) {
  return {
    meta: pmMeta(p),
    analysis: pmAnalysis(p),
    agent: pmAgent(p),
    diff: pmDiff(p),
    gate: pmGate(p),
    issues: pmIssues(p),
    shots: pmShots(p),
    console: pmConsole(p),
    blocks: pmBlocks(p),
  }
}

/** 采纳按钮区的可见/禁用矩阵。旧版 app.js:543 renderActions */
export function proposalActions(p) {
  const st = p.status
  const gate = p.gate || {}
  const gateOk = gate.ok !== false
  const forceNeeded = !gateOk || st === 'gate_failed'
  const a = {
    approve: { hidden: false, disabled: false, title: '' },
    force: { hidden: true, disabled: false, title: '' },
    reject: { hidden: false, disabled: false, title: '' },
    undo: { hidden: true, disabled: false, title: '' },
    forceLine: { hidden: true },
    forceConfirm: false,
  }
  if (st === 'applied') {
    a.approve.hidden = true
    a.reject.hidden = true
    a.undo.hidden = false
    a.undo.title = '把 AI 改过的文件恢复为采纳前的内容（仅恢复本次采纳涉及的文件）'
    return a
  }
  if (st === 'rejected') {
    a.approve.hidden = true
    a.reject.hidden = true
    a.approve.title = a.reject.title = '提案已拒绝，只读'
    return a
  }
  if (forceNeeded) {
    a.force.hidden = false
    a.forceLine.hidden = false
    a.approve.disabled = true
    a.approve.title = '验收闸门未通过：只能勾选风险后「强制采纳」'
  }
  if (st === 'invalid') {
    a.approve.disabled = true
    a.force.hidden = true
    a.forceLine.hidden = true
    a.approve.title = '没有可应用的补丁，采纳不会写任何文件'
    a.reject.disabled = false
  }
  return a
}

/** 弹窗头部状态徽标的 class（旧版直接 stEl.className = `st st-xxx`） */
export function statusCls(p) {
  return `st st-${escapeHtml(p.status || 'pending')}`
}

export function statusLabel(p) {
  return STATUS_TEXT[p.status] || p.status || '—'
}
