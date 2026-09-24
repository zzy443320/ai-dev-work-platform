// ONES 前端研发助手 — frontend logic (vanilla JS, no build step)
const $ = (s) => document.querySelector(s);

/* ================= theme ================= */
function toggleTheme() {
  const cur = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('fixer-theme', next); } catch (e) {}
}
(function initTheme() {
  try {
    const saved = localStorage.getItem('fixer-theme');
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (e) {}
})();

/* ================= toast ================= */
let toastTimer = null;
function toast(msg, kind = 'ok') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 2600);
}

/* ================= labels ================= */
const STATUS_TEXT = {
  pending: '待审批',
  gate_failed: '闸门未通过',
  invalid: '无法生成补丁',
  applied: '已采纳',
  apply_failed: '采纳失败',
  rejected: '已拒绝',
};
// 状态权重：只作为时间完全相同时的稳定兜底，不再参与主排序
const STATUS_ORDER = ['pending', 'gate_failed', 'invalid', 'apply_failed', 'applied', 'rejected'];
const GATE_TEXT = {
  explicit: '显式命令',
  package_json: 'package.json 探测',
  degraded: '降级语法检查',
  none: '未校验',
};
const GATE_HINT = {
  explicit: 'explicit：逐条执行了你在「验收闸门」里写的命令，可信度最高。',
  package_json: 'package_json：命令是从目标仓库 package.json 的 scripts 自动挑的，请确认它确实是团队平时用来验收的那条。',
  degraded: 'degraded：仓库里没有任何可用的校验命令，只做了括号/引号闭合与 node --check。这只证明代码没被写坏，不证明类型、测试通过。',
  none: 'none：什么都没检查过，这份提案的正确性 100% 取决于你人工审查下面的 diff。',
};
const SHOT_TEXT = {
  ok: '渲染无报错',
  error_visible: '页面有报错',
  blank: '页面空白',
  unreachable: '应用不可达',
  skipped: '未执行',
  error: '采集失败',
  none: '未执行',
};

let proposalsCache = [];
let settingsCache = null;
let healthCache = null;
let pmCurrent = null;      // 详情弹窗当前提案
let pmPendingError = '';   // 决策失败原因，重新渲染详情后仍要显示

/* ================= proposals ================= */
function isOpenStatus(s) { return s === 'pending' || s === 'gate_failed'; }

function statusText(s) {
  const st = s || '';
  if (st === 'proposed') return STATUS_TEXT.pending;   // 知识卡片里 pending 写成 proposed
  return STATUS_TEXT[st] || st;
}
function statusBadge(s) {
  const st = (s === 'proposed' || !s) ? 'pending' : s;
  return `<span class="st st-${escapeAttr(st)}">${escapeHtml(statusText(s))}</span>`;
}
function gateBadge(level, ok) {
  const lv = level || 'none';
  const failed = ok === false ? ' · 未通过' : '';
  return `<span class="gate-badge gate-${escapeAttr(lv)}">闸门 ${escapeHtml(GATE_TEXT[lv] || lv)}${escapeHtml(failed)}</span>`;
}
function relTime(iso) {
  if (!iso) return '—';
  const t = new Date(String(iso)).getTime();
  if (Number.isNaN(t)) return String(iso);
  const mins = Math.floor(Math.max(0, Date.now() - t) / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小时前`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days} 天前`;
  return String(iso).slice(0, 10);
}

async function loadProposals() {
  const el = $('#proposals');
  try {
    const r = await fetch('/api/proposals');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    proposalsCache = await r.json();
    renderProposals();
  } catch (e) {
    el.innerHTML = `<div class="empty">提案加载失败: ${escapeHtml(String(e))}</div>`;
  }
}

function setProposalBadge(open, total) {
  const badge = $('#proposal-count');
  if (!badge) return;
  const o = (open === undefined || open === null)
    ? proposalsCache.filter(p => isOpenStatus(p.status)).length : open;
  const t = (total === undefined || total === null) ? proposalsCache.length : total;
  badge.textContent = `${o} 待审 / ${t} 条`;
  badge.classList.toggle('alert', o > 0);
  badge.classList.toggle('hidden', t === 0);
}

function renderProposals() {
  const el = $('#proposals');
  if (!el) return;
  setProposalBadge();
  const sel = $('#proposal-filter');
  const filter = sel ? sel.value : '';
  let list = proposalsCache.slice();
  if (filter === 'pending') list = list.filter(p => isOpenStatus(p.status) || p.status === 'apply_failed');
  else if (filter) list = list.filter(p => p.status === filter);
  // 排序：一律按时间倒序（更新时间优先、创建时间兜底），最新的在最前面。
  // 曾经先按状态分组（待审的排最前）再按时间——8 分钟前闸门未过的提案会被
  // 压到两天前的 pending 后面，「待审批」列表的时间看起来忽新忽旧。
  list.sort((a, b) => {
    const d = String(b.updated || b.created || '').localeCompare(String(a.updated || a.created || ''));
    if (d) return d;
    return (STATUS_ORDER.indexOf(a.status) + 1 || 99) - (STATUS_ORDER.indexOf(b.status) + 1 || 99);
  });
  if (!proposalsCache.length) {
    el.innerHTML = '<div class="empty">还没有提案。运行流水线后会在这里等你审阅采纳。</div>';
    return;
  }
  if (!list.length) {
    el.innerHTML = '<div class="empty">当前筛选下没有提案。</div>';
    return;
  }
  // 分页：每页固定条数，翻页只切片，不重新请求
  const totalPages = Math.max(1, Math.ceil(list.length / PROP_PAGE_SIZE));
  if (propPage > totalPages) propPage = totalPages;
  if (propPage < 1) propPage = 1;
  const start = (propPage - 1) * PROP_PAGE_SIZE;
  const pageItems = list.slice(start, start + PROP_PAGE_SIZE);
  el.innerHTML = pageItems.map(proposalCard).join('') + renderPropPager(list.length, totalPages, start);
}

/* ---------- 待审批提案分页 ---------- */
const PROP_PAGE_SIZE = 8;   // 2 列 × 4 行
let propPage = 1;

function gotoPropPage(n) {
  propPage = Math.max(1, n);
  renderProposals();
  const el = $('#proposals');
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function onPropFilterChange() {
  propPage = 1;   // 切换筛选回到第一页
  renderProposals();
}

function pageNumbers(current, total) {
  if (total <= 9) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const out = [1];
  const s = Math.max(2, current - 1);
  const e = Math.min(total - 1, current + 1);
  if (s > 2) out.push('…');
  for (let i = s; i <= e; i++) out.push(i);
  if (e < total - 1) out.push('…');
  out.push(total);
  return out;
}

function renderPropPager(total, totalPages, start) {
  const end = Math.min(total, start + PROP_PAGE_SIZE);
  const nums = pageNumbers(propPage, totalPages).map(n => {
    if (n === '…') return '<span class="pg-dots">…</span>';
    return `<button class="pg-num${n === propPage ? ' active' : ''}" onclick="gotoPropPage(${n})">${n}</button>`;
  }).join('');
  return `<div class="prop-pager">
    <span class="pg-info">共 ${total} 条 · 第 ${start + 1}-${end} 条</span>
    <div class="pg-btns">
      <button class="pg-num" title="上一页" ${propPage <= 1 ? 'disabled' : ''} onclick="gotoPropPage(${propPage - 1})">‹</button>
      ${nums}
      <button class="pg-num" title="下一页" ${propPage >= totalPages ? 'disabled' : ''} onclick="gotoPropPage(${propPage + 1})">›</button>
    </div>
  </div>`;
}

function proposalCard(p) {
  const files = p.files || [];
  const err = p.error_count || 0;
  const warn = p.warning_count || 0;
  const failed = (p.gate_failed || []);
  return `
    <div class="pcard pc-${escapeAttr(p.status || 'pending')}" onclick="openProposal('${escapeAttr(p.id)}')">
      <div class="pcard-top">
        ${statusBadge(p.status)}
        ${gateBadge(p.gate_level, p.gate_ok)}
        <span class="pcard-id">${escapeHtml(p.defect_id || p.id || '')}</span>
        <span class="pcard-time">${escapeHtml(relTime(p.updated || p.created))}</span>
      </div>
      <div class="pcard-title">${escapeHtml(p.title || '(无标题)')}</div>
      <div class="pcard-meta">
        <span>${escapeHtml(p.category || '—')}</span>
        <span>${escapeHtml(p.priority || '—')}</span>
        <span>AI ${escapeHtml(p.ai_mode || '—')}</span>
        <span>${files.length} 个文件</span>
        <span class="dstat add">+${p.added || 0}</span>
        <span class="dstat del">−${p.removed || 0}</span>
        ${p.locate_empty ? '<span class="cnt warn">⚠ 未定位到文件</span>' : ''}
        ${err ? `<span class="cnt bad">${err} 补丁错误</span>` : ''}
        ${warn ? `<span class="cnt warn">${warn} 告警</span>` : ''}
        ${failed.length ? `<span class="cnt bad">闸门失败: ${escapeHtml(failed.join(', '))}</span>` : ''}
        ${p.sha ? `<span class="sha">${escapeHtml(String(p.sha).slice(0, 8))}</span>` : ''}
      </div>
      ${p.root_cause ? `<div class="pcard-root">${escapeHtml(p.root_cause)}</div>` : ''}
    </div>`;
}

/* ---------- detail modal ---------- */
async function openProposal(id) {
  try {
    const r = await fetch(`/api/proposals/${encodeURIComponent(id)}`);
    let data = null;
    try { data = await r.json(); } catch (e) { /* 非 JSON */ }
    if (!r.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`);
    pmCurrent = data;
    renderProposal(data);
    $('#pmodal').classList.remove('hidden');
  } catch (e) {
    toast(`打开提案详情失败: ${e}`, 'err');
  }
}

function closePModal(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  $('#pmodal').classList.add('hidden');
  pmCurrent = null;
  pmPendingError = '';
}

function renderProposal(p) {
  const patch = p.patch || {};
  const analysis = p.analysis || {};
  const pre = p.preflight_live || p.preflight || {};
  const problems = (pre.problems || []).filter(Boolean);

  $('#pmodal-title').textContent = p.id || '';
  const stEl = $('#pmodal-status');
  stEl.className = `st st-${escapeAttr(p.status || 'pending')}`;
  stEl.textContent = STATUS_TEXT[p.status] || p.status || '—';
  stEl.classList.remove('hidden');
  const gtEl = $('#pmodal-gate');
  gtEl.innerHTML = gateBadge((p.gate || {}).level, (p.gate || {}).ok);

  const branchBad = pre.current_branch && pre.expected_branch && pre.current_branch !== pre.expected_branch;
  $('#pmodal-meta').innerHTML = [
    ['缺陷', `${p.defect && p.defect.id ? p.defect.id : ''} ${p.defect && p.defect.title ? p.defect.title : ''}`.trim()],
    ['分类', analysis.category],
    ['优先级', p.defect && p.defect.priority],
    ['AI', analysis.ai_mode],
    ['补丁', patch.ok ? '可应用' : '无法应用'],
    ['分支', `${pre.current_branch || '未知'}${pre.expected_branch ? ` → 期望 ${pre.expected_branch}` : ''}`],
    ['工作区', pre.dirty ? '有未提交改动' : '干净'],
    ['创建', relTime(p.created)],
    ['更新', String(p.updated || '').replace('T', ' ').slice(0, 19)],
  ].filter(([_, v]) => v).map(([k, v]) =>
    `<span><b>${escapeHtml(k)}:</b> ${escapeHtml(String(v))}</span>`
  ).join('') + (branchBad ? '<span class="cnt bad">分支与配置不一致，采纳会被拒</span>' : '');

  const banner = $('#pm-preflight');
  if (problems.length) {
    banner.innerHTML = `<b>仓库预检未通过 —— 现在点采纳一定会被后端拒绝：</b>`
      + `<ul>${problems.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`
      + `<span class="muted">修好这些（如切换到配置的分支）后重新打开提案即可重试，无需重跑流水线。</span>`;
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
    banner.innerHTML = '';
  }

  $('#pm-analysis').innerHTML = pmAnalysis(p);
  $('#pm-diff').innerHTML = pmDiff(p);
  $('#pm-gate').innerHTML = pmGate(p);
  $('#pm-issues').innerHTML = pmIssues(p);
  $('#pm-shots').innerHTML = pmShots(p);
  $('#pm-console').innerHTML = pmConsole(p);
  $('#pm-blocks').innerHTML = pmBlocks(p);

  const errBox = $('#pm-error');
  if (pmPendingError) {
    errBox.innerHTML = `<b>后端拒绝了这个操作：</b>${escapeHtml(pmPendingError)}`;
    errBox.classList.remove('hidden');
    pmPendingError = '';
  } else {
    errBox.classList.add('hidden');
    errBox.innerHTML = '';
  }
  $('#pm-note').value = (p.decision && p.decision.note) || '';
  renderActions(p);
}

function kv(k, v, cls) {
  return `<div class="pm-line${cls ? ` ${cls}` : ''}"><b>${escapeHtml(k)}</b><span>${escapeHtml(v || '（空）')}</span></div>`;
}

// 「根因不在前端仓库」判定。新版提案由后端 analysis.non_frontend 直出；
// 历史提案没有该字段，按同样的信号词兜底重算（否则 100002 这类正确结论
// 在界面上仍显示成红色「未生成可应用补丁」）。
const NON_FE_STRONG = ['非前端缺陷', '不修改任何前端文件', '不需要修改前端',
  '前端无需修改', '根因在后端', '问题在后端', '后端缺陷', '需改后端',
  '后端修复', '不属于前端', '超出前端范围'];
const NON_FE_WEAK = ['后端', '服务端', '接口层', '数据库', '未命中'];
function looksNonFrontend(a) {
  a = a || {};
  if (a.block_count) return false;
  if (typeof a.non_frontend === 'boolean') return a.non_frontend;
  const text = [a.root_cause, a.explanation, a.prevention].join(' ');
  if (!text.trim()) return false;
  let score = 0;
  NON_FE_STRONG.forEach(s => { if (text.indexOf(s) >= 0) score += 2; });
  NON_FE_WEAK.forEach(s => { if (text.indexOf(s) >= 0) score += 1; });
  if (/\/api\//i.test(text) && text.indexOf('接口') >= 0) score += 1;
  return score >= 2;
}

function pmAnalysis(p) {
  const a = p.analysis || {};
  const d = p.decision || {};
  let html = '<h4>根因 / 说明 / 预防</h4>';
  if (a.locate_empty) {
    html += `<div class="pm-line bad"><b>定位失败</b><span>定位阶段没有在仓库里找到嫌疑文件，AI 拿不到任何源码，`
      + `按安全规则拒绝盲猜补丁（这是刻意的护栏，不是故障）。常见原因：① 工单描述里没有可搜索的`
      + `代码标识（可把组件路径/报错关键字补进工单描述后重跑）；② 该功能不在当前配置的仓库里`
      + `（检查侧边栏「仓库路径」）；③ 关键词都被第三方文件命中，已被过滤。</span></div>`;
  }
  if (looksNonFrontend(a) && !(a.patch_blocks || []).length) {
    html += `<div class="pm-line bad"><b>非前端缺陷</b><span>模型判断根因不在当前配置的前端仓库`
      + `（后端/接口/数据层），因此未生成前端补丁——这是结论而非故障。建议把工单转后端排查，`
      + `或补充后端日志/接口返回后再重跑。判断依据见下方根因。</span></div>`;
  }
  html += kv('根因', a.root_cause);
  html += kv('说明', a.explanation);
  html += kv('预防措施', a.prevention);
  if ((a.suspect_files || []).length) {
    html += `<div class="pm-line"><b>定位文件</b><span>${a.suspect_files.slice(0, 10)
      .map(f => `<code>${escapeHtml(f)}</code>`).join(' ')}</span></div>`;
  }
  if ((a.keywords || []).length) {
    html += `<div class="pm-line"><b>搜索关键词</b><span>${a.keywords.slice(0, 12)
      .map(k => `<code>${escapeHtml(k)}</code>`).join(' ')}</span></div>`;
  }
  if (a.ai_error) html += kv('AI 侧错误', a.ai_error, 'bad');
  if (d.decided_at) {
    html += `<div class="pm-line"><b>上次决策</b><span>${d.approved ? '采纳' : '拒绝'} · ${escapeHtml(String(d.decided_at).replace('T', ' '))}`
      + `${d.forced ? ' · 强制越过闸门' : ''}${d.note ? ` · 备注：${escapeHtml(d.note)}` : ''}</span></div>`;
  }
  if (p.kb_path) {
    html += `<div class="pm-line"><b>知识卡片</b><span><code>${escapeHtml(shortPath(p.kb_path))}</code></span></div>`;
  }
  return html;
}

function plainLines(text, cls) {
  return escapeHtml(text || '').split('\n')
    .map(l => `<span class="dl ${cls}">${l === '' ? ' ' : l}</span>`).join('\n');
}

function diffLines(text) {
  return escapeHtml(text || '').split('\n').map(line => {
    let cls = 'ctx';
    if (/^(---|@@|\+\+\+)/.test(line)) cls = line.startsWith('@@') ? 'hunk' : 'hdr';
    else if (line.startsWith('+')) cls = 'add';
    else if (line.startsWith('-')) cls = 'del';
    else if (line.startsWith('\\')) cls = 'note';
    return `<span class="dl ${cls}">${line === '' ? ' ' : line}</span>`;
  }).join('\n');
}

function pmDiff(p) {
  const changes = (p.patch || {}).changes || [];
  let html = '<h4>逐文件 diff</h4>';
  if (!changes.length) {
    return html + '<p class="muted">没有生成任何文件改动 —— 见下方“补丁错误”。</p>';
  }
  html += changes.map(c => {
    const s = c.stats || {};
    const head = `<div class="diff-file"><span class="df-path">${escapeHtml(c.file_path || '')}</span>`
      + `<span class="dstat add">+${s.added || 0}</span><span class="dstat del">−${s.removed || 0}</span>`
      + `<span class="muted">${c.applied_blocks || 0} 个补丁块</span></div>`;
    if (c.error) return head + `<div class="df-bad">${escapeHtml(c.error)}</div>`;
    if (!c.diff) return head + '<div class="muted">（该文件没有实际文本改动）</div>';
    let sec = head + `<pre class="diff">${diffLines(c.diff)}</pre>`;
    if ((c.warnings || []).length) {
      sec += `<ul class="warn-list">${c.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`;
    }
    return sec;
  }).join('');
  return html;
}

function pmGate(p) {
  const g = p.gate || {};
  const level = g.level || 'none';
  const checks = g.checks || [];
  const risky = level === 'degraded' || level === 'none';
  let html = `<h4>验收闸门 ${gateBadge(level, g.ok)}</h4>`;
  html += `<p class="gate-hint${risky ? ' warn' : ''}">${escapeHtml(GATE_HINT[level] || '未知闸门层级，按最不可信处理。')}</p>`;
  if (checks.length) {
    html += `<ul class="check-list">${checks.map(c => {
      const state = c.ok ? 'ok' : (c.skipped ? 'skip' : 'bad');
      return `<li class="${state}"><div class="check-head">`
        + `<b>${escapeHtml(c.name || '')}</b><code>${escapeHtml(c.cmd || '')}</code>`
        + `<span>退出码 ${escapeHtml(String(c.returncode === undefined ? '?' : c.returncode))}</span>`
        + `<span>${escapeHtml(String(c.seconds === undefined ? '—' : c.seconds))}s</span>`
        + (c.skipped ? '<span class="tag">跳过</span>' : '')
        + `</div>${c.output_tail ? `<pre class="check-out">${escapeHtml(c.output_tail)}</pre>` : ''}</li>`;
    }).join('')}</ul>`;
  } else {
    html += '<p class="muted">没有执行任何检查项。</p>';
  }
  if ((g.notes || []).length) {
    html += `<ul class="note-list">${g.notes.map(n => `<li>${mdBold(n)}</li>`).join('')}</ul>`;
  }
  const ag = (p.apply || {}).gate;
  if (ag) {
    html += `<p class="muted">采纳后又在真实工作区重跑了一次闸门：${escapeHtml(GATE_TEXT[ag.level] || ag.level || '?')} · ${ag.ok === false ? '未通过' : '通过'}。</p>`;
  }
  return html;
}

function pmIssues(p) {
  const patch = p.patch || {};
  const errs = patch.errors || [], rej = patch.rejected || [], warn = patch.warnings || [];
  let html = '<h4>补丁错误与告警</h4>';
  if (!errs.length && !rej.length && !warn.length) {
    return html + '<p class="muted">无。所有 SEARCH 块都精确命中了原文件。</p>';
  }
  if (errs.length) {
    html += `<div class="iss-title bad">错误 ${errs.length} 条（补丁不完整，采纳不会写任何文件）</div>`
      + `<ul class="iss-list bad">${errs.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`;
  }
  if (rej.length) {
    html += `<div class="iss-title bad">被拒绝的块 ${rej.length} 条</div>`
      + `<ul class="iss-list bad">${rej.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`;
  }
  if (warn.length) {
    html += `<div class="iss-title warn">告警 ${warn.length} 条（不阻断采纳，但需要你看一眼）</div>`
      + `<ul class="iss-list warn">${warn.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`;
  }
  return html;
}

function shotSrc(path) {
  if (!path) return '';
  const base = String(path).split(/[\\/]/).pop();
  return base ? `/screenshots/${encodeURIComponent(base)}` : '';
}

function pmShots(p) {
  const before = p.verify || {};
  const after = ((p.apply || {}).verify || {}).after || p.verify_after || {};
  const cells = [['修复前（提案生成时）', before], ['修复后（采纳写入后）', after]].map(([label, v]) => {
    const src = shotSrc(v.screenshot);
    const st = v.status || 'none';
    const missing = escapeHtml(v.error || v.reason || '未生成截图');
    return `<div class="shot"><div class="shot-head"><span>${escapeHtml(label)}</span>`
      + `<span class="shot-st ${escapeAttr(st)}">${escapeHtml(SHOT_TEXT[st] || st)}</span></div>`
      + (src ? `<img src="${src}" alt="${escapeAttr(label)}" loading="lazy">`
             : `<div class="shot-missing">${missing}</div>`)
      + (v.route ? `<div class="muted">路由 ${escapeHtml(v.route)}${v.final_url ? ` · ${escapeHtml(v.final_url)}` : ''}</div>` : '')
      + pmShotOps(v)
      + '</div>';
  }).join('');
  return `<h4>页面截图</h4><div class="shots">${cells}</div>`
    + '<p class="muted">截图会打开工单对应路由并复刻复现步骤（无步骤时仅打开页面）。<br>空白页会被标为「页面空白」——它不是通过。<br>截图不能替代闸门命令与人工审查 diff。</p>';
}

function pmShotOps(v) {
  const note = v.plan_note ? `<div class="muted">${escapeHtml(v.plan_note)}</div>` : '';
  const acts = v.actions || [];
  if (!acts.length) return note;
  const summary = acts.slice(0, 4).join(' → ');
  const more = acts.length > 4 ? ` …共 ${acts.length} 步` : '';
  const log = v.actions_log || [];
  const fails = log.filter(l => !l.ok);
  const failNote = fails.length
    ? `（${fails.length} 步未执行成功：${fails.slice(0, 2).map(f => f.op).join('、')}）`
    : '';
  return note + `<div class="muted">复刻操作：${escapeHtml(summary + more)}${escapeHtml(failNote)}</div>`;
}

function pmConsole(p) {
  const before = p.verify || {};
  const after = ((p.apply || {}).verify || {}).after || p.verify_after || {};
  const pick = (v, label) => (v.console_errors || []).concat(v.page_errors || [])
    .map(e => `[${label}] ${e}`);
  const errs = pick(before, '修复前').concat(pick(after, '修复后'));
  let html = '<h4>console / 页面报错</h4>';
  if (!errs.length) {
    const why = before.error || after.error || before.reason || after.reason;
    return html + `<p class="muted">没有捕获到 console 错误或 page error。${escapeHtml(why || '')}</p>`;
  }
  return html + `<ul class="iss-list bad">${errs.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>`;
}

function pmBlocks(p) {
  const patch = p.patch || {};
  const blocks = patch.blocks || [];
  let html = '<h4>SEARCH/REPLACE 原文</h4>';
  if (!blocks.length) {
    const raw = (p.analysis || {}).patch_canonical || patch.patch_text || '';
    return html + (raw
      ? `<details class="fold"><summary>模型原始输出（${raw.length} 字符）</summary><pre class="diff">${escapeHtml(raw)}</pre></details>`
      : '<p class="muted">模型没有给出可解析的 SEARCH/REPLACE 块。</p>');
  }
  const body = blocks.map((b, i) => `<div class="sr-block">
      <div class="sr-head">#${i + 1} <code>${escapeHtml(b.file_path || '')}</code>
        <span class="tag">${escapeHtml(b.match_mode || '未命中')}</span>
        <span class="muted">第 ${escapeHtml(String(b.match_start === -1 ? '?' : b.match_start))} 行</span></div>
      <div class="sr-label">SEARCH</div><pre class="diff">${plainLines(b.search, 'srch')}</pre>
      <div class="sr-label">REPLACE</div><pre class="diff">${plainLines(b.replace, 'srpl')}</pre>
    </div>`).join('');
  return html + `<details class="fold"><summary>展开 ${blocks.length} 个补丁块（核对模型到底改了哪几行）</summary>${body}</details>`;
}

function mdBold(s) {
  return escapeHtml(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

function renderActions(p) {
  const st = p.status;
  const gate = p.gate || {};
  const gateOk = gate.ok !== false;
  const forceNeeded = !gateOk || st === 'gate_failed';
  const ap = $('#btn-approve'), fo = $('#btn-force'), rj = $('#btn-reject'),
        un = $('#btn-undo'), fl = $('#force-line');
  [ap, rj, un, fo].forEach(b => { b.disabled = false; b.title = ''; });
  ap.classList.remove('hidden');
  rj.classList.remove('hidden');
  un.classList.add('hidden');
  fo.classList.add('hidden');
  fl.classList.add('hidden');
  $('#force-confirm').checked = false;

  if (st === 'applied') {
    ap.classList.add('hidden'); rj.classList.add('hidden');
    un.classList.remove('hidden');
    un.title = '把 AI 改过的文件恢复为采纳前的内容（仅恢复本次采纳涉及的文件）';
    return;
  }
  if (st === 'rejected') {
    ap.classList.add('hidden'); rj.classList.add('hidden');
    ap.title = rj.title = '提案已拒绝，只读';
    return;
  }
  if (forceNeeded) {
    fo.classList.remove('hidden');
    fl.classList.remove('hidden');
    ap.disabled = true;
    ap.title = '验收闸门未通过：只能勾选风险后「强制采纳」';
  }
  if (st === 'invalid') {
    ap.disabled = true; fo.classList.add('hidden'); fl.classList.add('hidden');
    ap.title = '没有可应用的补丁，采纳不会写任何文件';
    rj.disabled = false;
  }
}

/* ---------- decisions ---------- */
function noteValue() { return ($('#pm-note').value || '').trim(); }

function onApprove() {
  if (!pmCurrent) return;
  decide(pmCurrent.id, 'approve', { note: noteValue() });
}
function onForceApprove() {
  if (!pmCurrent) return;
  if (!$('#force-confirm').checked) {
    toast('强制采纳前必须勾选“我已知悉风险”', 'err');
    return;
  }
  decide(pmCurrent.id, 'approve', { force_gate: true, confirm_risk: true, note: noteValue() });
}
function onReject() {
  if (!pmCurrent) return;
  if (!confirm('确认拒绝该提案？\n不会写任何文件，仅把提案标记为 rejected（可重跑流水线生成新提案）。')) return;
  decide(pmCurrent.id, 'reject', { note: noteValue() });
}
function onUndo() {
  if (!pmCurrent) return;
  const files = ((pmCurrent.apply || {}).files) || [];
  const list = files.length ? `\n涉及文件：\n${files.map(f => '· ' + f).join('\n')}` : '';
  if (!confirm(`确认撤销采纳？\n后端会把下列改动恢复为采纳前的内容（仅恢复本次采纳写入的文件，不影响其他改动）。${list}`)) return;
  decide(pmCurrent.id, 'undo', {});
}

async function decide(id, action, payloadObj) {
  const payload = payloadObj || {};
  const btns = [...document.querySelectorAll('#pm-actions button')];
  btns.forEach(b => { b.dataset.was = b.disabled ? '1' : ''; b.disabled = true; });
  const prevNote = $('#pm-note').value;
  const modalOpen = !$('#pmodal').classList.contains('hidden');
  let ok = false, msg = '';

  try {
    const r = await fetch(`/api/proposals/${encodeURIComponent(id)}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* 空响应 */ }
    ok = r.ok && data.ok !== false;
    msg = data.error
      || (Array.isArray(data.detail) ? data.detail.join('\n') : data.detail)
      || `HTTP ${r.status}`;
    if (ok) {
      const res = data.result || {};
      if (action === 'approve') {
        toast(`已写入工作区（${(res.files || []).length} 个文件，未 commit）`);
      } else if (action === 'reject') {
        toast('已拒绝该提案，未改动仓库');
      } else if (action === 'undo') {
        toast(`已恢复采纳前的文件内容：${(data.restored || []).length} 个文件`);
      } else {
        toast('操作完成');
      }
    } else {
      toast(msg || '操作被拒绝', 'err');
    }
  } catch (e) {
    ok = false;
    msg = `请求失败: ${e}`;
    toast(msg, 'err');
  }

  await Promise.all([loadProposals(), loadStats(), loadHealth()]);
  btns.forEach(b => { b.disabled = false; });

  if (modalOpen && pmCurrent && pmCurrent.id === id) {
    if (!ok) pmPendingError = msg;
    await openProposal(id);
    if ($('#pm-note') && !noteValue()) $('#pm-note').value = prevNote;
  } else if (!ok) {
    pmPendingError = msg;
  }
}

/* ================= health ================= */
async function loadHealth() {
  try {
    const r = await fetch('/api/health');
    const h = await r.json();
    healthCache = h;
    const ai = h.ai_mode === 'mock' ? 'AI: mock' : `AI: ${h.ai_model}`;
    const ones = h.ones_configured ? 'ONES: 已配置' : 'ONES: 未配置';
    const cur = h.current_branch || '?';
    const branch = `分支: ${cur}${h.expected_branch && h.expected_branch !== cur ? `≠${h.expected_branch}` : ''}${h.dirty ? ' ·工作区脏' : ''}`;
    const gate = `闸门: ${GATE_TEXT[h.gate_level] || h.gate_level || '未知'}`;
    $('#health').textContent = `repo: ${shortPath(h.repo)}  ·  ${ai}  ·  ${ones}  ·  ${branch}  ·  ${gate}  ·  app: ${h.app_url || '未配置'}`;

    const banner = $('#repo-banner');
    const problems = (h.repo_problems || []).filter(Boolean);
    if (banner) {
      if (problems.length) {
        banner.innerHTML = '<b>仓库预检未通过：现在点「采纳」会被后端直接拒绝。</b>'
          + `<ul>${problems.map(p => `<li>${escapeHtml(p)}</li>`).join('')}</ul>`
          + '<span class="muted">切到配置的分支之后，再回来采纳；提案本身不会失效。<br>工作区有未提交改动不影响采纳。</span>';
        banner.classList.remove('hidden');
      } else {
        banner.classList.add('hidden');
        banner.innerHTML = '';
      }
    }
    const counts = h.proposal_counts || {};
    const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0);
    setProposalBadge(h.pending || 0, total || undefined);
    renderGatePreview();
    renderAiSummary();
  } catch (e) {
    healthCache = null;
    $('#health').textContent = '后端不可达';
  }
}

/* ================= stats ================= */
async function loadStats() {
  const el = $('#stats');
  try {
    const r = await fetch('/api/stats');
    const stats = await r.json();
    const cats = Object.entries(stats.categories || {});
    const cards = stats.cards == null ? 0 : stats.cards;
    const tot = cats.reduce((a, [, s]) => a + (s.total || 0), 0);
    const applied = cats.reduce((a, [, s]) => a + (s.applied || 0), 0);
    const countEl = $('#stats-count');
    if (countEl) {
      countEl.textContent = `${cards} 张卡片 · 已采纳 ${applied}/${tot}`
        + (stats.generated_at ? ` · 更新于 ${String(stats.generated_at).slice(0, 16).replace('T', ' ')}` : '');
    }
    if (cats.length === 0) {
      el.innerHTML = '<div class="empty">暂无数据</div>';
      return;
    }
    el.innerHTML = cats.map(([cat, s]) => {
      const total = s.total || 0;
      const rate = total ? Math.round(((s.applied || 0) / total) * 100) : 0;
      return `
        <div class="stat-card">
          <div class="stat-cat">${escapeHtml(cat)}</div>
          <div class="stat-total">${total}</div>
          <div class="stat-bar"><i style="width:${rate}%"></i></div>
          <div class="stat-fixed">已采纳 ${s.applied ?? 0} / 待审 ${s.pending ?? 0} / 已拒绝 ${s.rejected ?? 0}
            <span class="stat-rate">采纳率 ${rate}%</span>
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty">加载失败: ${escapeHtml(String(e))}</div>`;
  }
}

/* ================= knowledge base（两层视图） =================
   模式 = 同类缺陷的共性沉淀（复发 N 次 → 该处缺护栏，可复用改法在这里）；
   缺陷卡 = 单个工单的事实档案（现象/根因/补丁/验证）。
   默认展示模式：用户要的「这类问题怎么修」在模式卡里，缺陷卡是证据。 */
let KB_VIEW = localStorage.getItem('kb-view') === 'defect' ? 'defect' : 'pattern';
// 后端还没重启时的降级说明。存在这里而不是直接写字，是因为页面初始化与
// 手动切换可能并发触发 loadKb()，后到的 syncKbToggle 会把提示冲掉。
let KB_FALLBACK = '';

function setKbView(v) {
  KB_VIEW = v === 'defect' ? 'defect' : 'pattern';
  KB_FALLBACK = '';
  localStorage.setItem('kb-view', KB_VIEW);
  loadKb();
}

function syncKbToggle() {
  document.querySelectorAll('#kb-views .seg-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.kbview === KB_VIEW));
  const hint = $('#kb-hint');
  if (hint) {
    hint.textContent = KB_FALLBACK || (KB_VIEW === 'pattern'
      ? '同类缺陷的共性沉淀：复发次数越高，越说明该处缺护栏'
      : '一个工单一张卡：现象、根因、补丁与验证结果');
  }
}

function loadKb() {
  syncKbToggle();
  return KB_VIEW === 'defect' ? loadCards() : loadPatterns();
}

async function loadPatterns() {
  const el = $('#cards');
  let r;
  try {
    r = await fetch('/api/patterns');
  } catch (e) {
    return kbFallback(String(e));
  }
  if (!r.ok) return kbFallback(`HTTP ${r.status}`);
  try {
    const pats = await r.json();
    if (!Array.isArray(pats)) throw new Error(pats.error || '返回异常');
    const recurring = pats.filter(p => (p.recurrence || 0) >= 2).length;
    $('#card-count').textContent = `${pats.length} 个模式`
      + (recurring ? ` · ${recurring} 个已复发` : '');
    if (pats.length === 0) {
      el.innerHTML = '<div class="empty">暂无模式：知识库还没有卡片，跑一条工单就会自动成模</div>';
      return;
    }
    el.innerHTML = pats.map(p => {
      const rep = p.recurrence || 0;
      const modShort = (p.module || '').split('/').slice(-2).join('/');
      return `
      <div class="kb-card pattern${rep >= 2 ? ' hot' : ''}" onclick="viewPattern('${escapeAttr(p.id)}')">
        <div class="kb-meta">
          <span class="kb-cat">模式</span>
          <span class="kb-rep${rep >= 2 ? ' hot' : ''}">复发 ${rep} 次</span>
        </div>
        <div class="kb-title">${escapeHtml(p.name || p.id)}</div>
        <div class="kb-sub">${escapeHtml((p.categories || []).join('、') || '—')}${modShort ? ' · ' + escapeHtml(modShort) : ''}</div>
        <div class="kb-footer">
          <span>${(p.defects || []).length} 个缺陷</span>
          ${p.applied
            ? `<span class="kb-status ok">已采纳 ${p.applied}</span>`
            : '<span class="kb-status skipped">尚无采纳案例</span>'}
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty">加载失败: ${escapeHtml(String(e))}</div>`;
  }
}

/* 后端还没重启（没有 /api/patterns）时，知识库面板不该看起来是坏的：
   记忆体内回退到缺陷卡片视图并说明原因。**不写 localStorage**——
   重启后自动恢复成模式视图。 */
function kbFallback(reason) {
  KB_VIEW = 'defect';
  KB_FALLBACK = `模式视图需重启后端（python -m web.server）后可用，当前为缺陷卡片视图（${reason}）`;
  syncKbToggle();
  return loadCards();
}

async function viewPattern(id) {
  try {
    const r = await fetch(`/api/pattern/${encodeURIComponent(id)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const meta = data.front || {};
    $('#modal-title').textContent = meta.name || id;
    const chips = (data.cases || []).map(c =>
      `<a class="link" onclick="viewCard('${escapeAttr(c.category)}','${escapeAttr(c.id)}')">${escapeHtml(c.id)}</a>`
    ).join(' ');
    $('#modal-meta').innerHTML = [
      ['模式', meta.pattern_id],
      ['复发', meta.recurrence ? `${meta.recurrence} 次` : ''],
      ['已采纳', meta.applied],
      ['分类', meta.categories],
      ['模块', meta.module],
      ['更新', (meta.updated || '').replace('T', ' ').slice(0, 19)],
    ].filter(([_, v]) => v).map(([k, v]) =>
      `<span><b>${escapeHtml(k)}:</b> ${escapeHtml(String(v))}</span>`
    ).join('') + (chips ? `<span><b>关联缺陷:</b> ${chips}</span>` : '');
    $('#modal-body').innerHTML = renderMarkdown(data.body);
    $('#modal').classList.remove('hidden');
  } catch (e) {
    toast(`打开失败: ${e}`, 'err');
  }
}

/* ================= cards ================= */
async function loadCards() {
  const el = $('#cards');
  try {
    const r = await fetch('/api/cards');
    const cards = await r.json();
    $('#card-count').textContent = `${cards.length} 张卡片`;
    if (cards.length === 0) {
      el.innerHTML = '<div class="empty">暂无知识卡片</div>';
      return;
    }
    el.innerHTML = cards.map(c => `
      <div class="kb-card" onclick="viewCard('${escapeAttr(c.category)}','${escapeAttr(c.id)}')">
        <div class="kb-meta">
          <span class="kb-cat">${escapeHtml(c.category)}</span>
          <span>${escapeHtml(c.priority || '—')}</span>
        </div>
        <div class="kb-title">${escapeHtml(c.title || c.id)}</div>
        <div class="kb-footer">
          <span class="kb-id">${escapeHtml(c.id)}</span>
          ${statusBadge(c.status)}
          ${c.gate_level ? gateBadge(c.gate_level, true) : ''}
          ${c.commit ? `<span class="sha">${escapeHtml(c.commit)}</span>` : ''}
          ${c.proposal_id ? `<a class="link" onclick="event.stopPropagation();openProposal('${escapeAttr(c.proposal_id)}')">查看提案</a>` : ''}
          <span>${escapeHtml(((c.updated || c.created) || '').slice(0, 16).replace('T', ' '))}</span>
        </div>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty">加载失败: ${escapeHtml(String(e))}</div>`;
  }
}

/* 知识库文档相对链接的路由：`分类/缺陷.md` → 缺陷卡，`_patterns/x.md` → 模式卡 */
function openKbDoc(rel) {
  const parts = String(rel || '').split('/').filter(Boolean);
  if (parts.length < 2) return;
  const file = parts[parts.length - 1];
  const id = file.replace(/\.md$/, '');
  if (!id) return;
  if (parts[0] === '_patterns') viewPattern(id);
  else viewCard(parts[0], id);
}

async function viewCard(category, id) {
  try {
    const r = await fetch(`/api/card/${encodeURIComponent(category)}/${encodeURIComponent(id)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const meta = data.front || {};
    $('#modal-title').textContent = meta.title || id;
    $('#modal-meta').innerHTML = [
      ['ID', meta.defect_id],
      ['分类', data.category],
      ['优先级', meta.priority],
      ['状态', statusText(meta.status)],
      ['AI', meta.ai_mode],
      ['闸门', GATE_TEXT[meta.gate_level] || meta.gate_level],
      ['Commit', meta.commit || '—'],
      ['提案', meta.proposal_id],
      ['更新', (meta.updated || meta.created || '').replace('T', ' ').slice(0, 19)],
    ].filter(([_, v]) => v).map(([k, v]) =>
      `<span><b>${escapeHtml(k)}:</b> ${escapeHtml(String(v))}</span>`
    ).join('');
    $('#modal-body').innerHTML = renderMarkdown(data.body);
    $('#modal').classList.remove('hidden');
  } catch (e) {
    toast(`打开失败: ${e}`, 'err');
  }
}

function closeModal(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  $('#modal').classList.add('hidden');
}
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  $('#modal').classList.add('hidden');
  $('#pmodal').classList.add('hidden');
  $('#aimodal')?.classList.add('hidden');
  $('#runmodal')?.classList.add('hidden');
  $('#chmodal')?.classList.add('hidden');
  // 产出物详情（#amodal）此前漏在这里了：Esc 关不掉，弹窗还会一直挡住后面的点击。
  // 走 closeAModal 是为了顺带清掉 amCurrent / amPendingError。
  const am = $('#amodal');
  if (am && !am.classList.contains('hidden')) closeAModal();
});

/* ---------- AI 设置弹窗（配置已从侧边栏独立出去） ---------- */
function openAiModal() {
  $('#aimodal').classList.remove('hidden');
}
function closeAiModal(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  $('#aimodal').classList.add('hidden');
}

/* ---------- 侧边栏大模型摘要卡片 ---------- */
const AI_PROVIDER_TEXT = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  custom: '自定义网关',
};

function renderAiSummary() {
  const el = $('#ai-summary');
  if (!el) return;
  const s = settingsCache || {};
  const ai = s.ai || {};
  const h = healthCache || {};
  const mock = !!(ai.mock || h.ai_mode === 'mock');
  const provider = AI_PROVIDER_TEXT[ai.provider] || ai.provider || '—';
  const model = ai.model || h.ai_model || '未设置';
  const base = ai.base_url || '官方默认地址';
  const keyText = s.has_api_key
    ? `Key 已保存 ${(ai.api_key_masked || '').trim()}`
    : '未配置 API Key';
  el.innerHTML = `
    <div class="ais-row">
      <span class="ais-badge ${mock ? 'mock' : 'live'}">${mock ? 'Mock 模式' : '真实调用'}</span>
      <span class="ais-provider">${escapeHtml(provider)}</span>
    </div>
    <div class="ais-line" title="${escapeAttr(model)}">模型：${escapeHtml(model)}</div>
    <div class="ais-line" title="${escapeAttr(base)}">地址：${escapeHtml(shortPath(base))}</div>
    <div class="ais-line">${mock ? '不调用真实 API，结果仅供演示' : escapeHtml(keyText)}</div>
  `;
}

/* ================= settings ================= */
async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    const s = await r.json();
    settingsCache = s;
    $('#cfg-repo-path').value = s.repo.path || '';
    $('#cfg-repo-branch').value = s.repo.branch || '';
    fillAiForm(s.ai || {});
    $('#cfg-ai-key').value = '';
    $('#cfg-ai-key').placeholder = s.has_api_key
      ? `已保存 ${s.ai.api_key_masked}，留空保持不变`
      : '填写后才会发起真实调用（留空=Mock）';
    aiKeyCleared = false;
    $('#cfg-ones-url').value = s.ones.base_url || '';
    $('#cfg-ones-uuid').value = s.ones.project_uuid || '';
    $('#cfg-ones-team').value = s.ones.team_uuid || '';
    $('#cfg-ones-email').value = s.ones.email || '';
    $('#cfg-ones-password').value = '';
    $('#cfg-ones-password').placeholder = s.has_password ? '已保存，留空保持不变' : 'ONES 登录密码';
    $('#cfg-ones-token').value = '';
    $('#cfg-ones-token').placeholder = s.has_token ? `已保存 ${s.ones.token_masked}，留空保持不变` : '可粘贴浏览器里的 token';
    const fig = s.figma || {};
    $('#cfg-figma-token').value = '';
    $('#cfg-figma-token').placeholder = s.has_figma_token
      ? `已保存 ${fig.token_masked || ''}，留空保持不变`
      : '没有 Token 时可在需求开发页用「浏览器打开」兜底';
    const plg = s.pagelogin || {};
    $('#cfg-pagelogin-enabled').checked = plg.enabled === true;
    $('#cfg-pagelogin-email').value = plg.email || '';
    $('#cfg-pagelogin-password').value = '';
    $('#cfg-pagelogin-password').placeholder = s.has_page_password ? '已保存，留空保持不变' : '被测应用的登录密码';
    const gate = s.gate || {};
    $('#cfg-gate-commands').value = gate.commands_text || '';
    $('#cfg-gate-degraded').checked = gate.degraded !== false;
    const pw = s.playwright || {};
    $('#cfg-app-url').value = pw.base_url || '';
    $('#cfg-headless').checked = pw.headless !== false;
    renderGatePreview();
    updateRepoHint(s.repo.path);
    renderAiSummary();
  } catch (e) {
    toast(`配置加载失败: ${e}`, 'err');
  }
}

/* ================= AI 接入配置 ================= */
let aiPresets = [];
let aiKeyCleared = false;

const AI_FIELD_IDS = {
  provider: '#cfg-ai-provider', auth_style: '#cfg-ai-auth', base_url: '#cfg-ai-base',
  endpoint: '#cfg-ai-endpoint', models_path: '#cfg-ai-models-path',
  auth_header: '#cfg-ai-auth-header', model: '#cfg-ai-model',
  temperature: '#cfg-ai-temp', max_tokens: '#cfg-ai-max-tokens',
  top_p: '#cfg-ai-top-p', timeout: '#cfg-ai-timeout',
  content_path: '#cfg-ai-content-path', max_tokens_field: '#cfg-ai-max-tokens-field',
  system_role: '#cfg-ai-system-role', proxy: '#cfg-ai-proxy',
};

function setVal(sel, v) {
  const el = $(sel);
  if (!el) return;
  el.value = (v === null || v === undefined) ? '' : v;
  if (el._csSync) el._csSync();
}

function fillAiForm(ai) {
  for (const [k, sel] of Object.entries(AI_FIELD_IDS)) setVal(sel, ai[k]);
  $('#cfg-ai-mock').checked = !!ai.mock;
  $('#cfg-ai-json-mode').checked = ai.json_mode !== false;
  $('#cfg-ai-verify-ssl').checked = ai.verify_ssl !== false;
  $('#cfg-ai-headers').value = ai.extra_headers_text
    || prettyJson(ai.extra_headers) || '';
  $('#cfg-ai-body').value = ai.extra_body_text || prettyJson(ai.extra_body) || '';
  onProviderChange();
  onAuthChange();
}

function prettyJson(obj) {
  if (!obj || !Object.keys(obj).length) return '';
  try { return JSON.stringify(obj, null, 2); } catch (e) { return ''; }
}

function collectAi() {
  const num = (sel) => $(sel).value.trim();
  const out = {
    provider: $('#cfg-ai-provider').value,
    auth_style: $('#cfg-ai-auth').value,
    base_url: num('#cfg-ai-base'),
    endpoint: num('#cfg-ai-endpoint'),
    models_path: num('#cfg-ai-models-path'),
    auth_header: num('#cfg-ai-auth-header'),
    model: num('#cfg-ai-model'),
    temperature: num('#cfg-ai-temp'),
    max_tokens: num('#cfg-ai-max-tokens'),
    top_p: num('#cfg-ai-top-p'),
    timeout: num('#cfg-ai-timeout'),
    content_path: num('#cfg-ai-content-path'),
    max_tokens_field: num('#cfg-ai-max-tokens-field'),
    system_role: num('#cfg-ai-system-role'),
    proxy: num('#cfg-ai-proxy'),
    extra_headers: $('#cfg-ai-headers').value,
    extra_body: $('#cfg-ai-body').value,
    json_mode: $('#cfg-ai-json-mode').checked,
    verify_ssl: $('#cfg-ai-verify-ssl').checked,
    mock: $('#cfg-ai-mock').checked,
  };
  const key = $('#cfg-ai-key').value.trim();
  if (key) out.api_key = key;
  if (aiKeyCleared) out.clear_api_key = true;
  return out;
}

function onProviderChange() {
  const p = $('#cfg-ai-provider').value;
  const preset = aiPresets.find(x => x.provider === p && x.id === p);
  const hints = {
    anthropic: '留空 = https://api.anthropic.com；中转站填自己的域名（不带 /v1/messages）',
    openai: '填到版本层为止，例如 https://你的中转站.com/v1',
    custom: '自建/异构网关必填，Base URL + Chat 路径拼成完整地址',
  };
  $('#cfg-ai-base-hint').textContent = hints[p] || '';
  const ep = $('#cfg-ai-endpoint');
  if (preset && !ep.value.trim()) ep.placeholder = preset.endpoint;
  const mp = $('#cfg-ai-models-path');
  if (preset && !mp.value.trim()) mp.placeholder = preset.models_path;
  const cp = $('#cfg-ai-content-path');
  if (preset && !cp.value.trim()) cp.placeholder = preset.content_path;
  if (preset) {
    modelSuggestList = (preset.models || []).slice();
  }
}

function onAuthChange() {
  const style = $('#cfg-ai-auth').value;
  const needs = style === 'header' || style === 'query';
  const field = $('#ai-auth-header-field');
  if (field) field.classList.toggle('hidden', !needs);
}

async function loadAiPresets() {
  try {
    const r = await fetch('/api/ai/providers');
    const data = await r.json();
    aiPresets = data.presets || [];
    const sel = $('#cfg-ai-preset');
    if (sel && sel.options.length <= 1) {
      sel.innerHTML = '<option value="">— 选择模板自动填充下方字段 —</option>'
        + aiPresets.map(p => `<option value="${escapeAttr(p.id)}">${escapeHtml(p.label)}</option>`).join('');
    }
    const authSel = $('#cfg-ai-auth');
    if (authSel && data.auth_styles && authSel.options.length <= 5) {
      authSel.innerHTML = data.auth_styles.map(a =>
        `<option value="${escapeAttr(a.id)}">${escapeHtml(a.label)}</option>`).join('');
    }
  } catch (e) { /* 模板拉不到不影响手工填写 */ }
}

function applyAiPreset(id) {
  const p = aiPresets.find(x => x.id === id);
  if (!p) return;
  setVal(AI_FIELD_IDS.provider, p.provider);
  setVal(AI_FIELD_IDS.base_url, p.base_url);
  setVal(AI_FIELD_IDS.endpoint, p.endpoint);
  setVal(AI_FIELD_IDS.models_path, p.models_path);
  setVal(AI_FIELD_IDS.content_path, p.content_path);
  setVal(AI_FIELD_IDS.auth_style, p.auth_style);
  $('#cfg-ai-json-mode').checked = !!p.json_mode;
  if (p.models && p.models.length && !$('#cfg-ai-model').value.trim()) {
    setVal(AI_FIELD_IDS.model, p.models[0]);
  }
  onProviderChange();
  onAuthChange();
  toast(`已套用「${p.label}」，下方字段仍可逐项修改`, 'ok');
}

function clearAiKey() {
  $('#cfg-ai-key').value = '';
  aiKeyCleared = true;
  $('#cfg-ai-key').placeholder = '将清除已保存的 Key（保存后生效）';
  toast('保存后将清除已存的 API Key', 'err');
}

function closeAiTestResult() {
  $('#ai-test-result').classList.add('hidden');
}

function renderAiTestResult(data, dry) {
  const box = $('#ai-test-result');
  box.classList.remove('hidden');
  const mockSkipped = !!data.mock && !dry;
  const ok = !!data.ok && !mockSkipped;
  const req = data.request || {};
  const rows = [];
  if (req.url) rows.push(['请求地址', req.url]);
  if (req.headers) rows.push(['请求头', Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join('\n')]);
  if (req.body_keys && req.body_keys.length) rows.push(['请求体字段', req.body_keys.join(', ')]);
  if (data.reply) rows.push(['模型回显', data.reply]);
  if (data.usage && (data.usage.input || data.usage.output)) {
    rows.push(['token', `输入 ${data.usage.input ?? '?'} / 输出 ${data.usage.output ?? '?'}`]);
  }
  if (data.seconds) rows.push(['耗时', `${data.seconds}s`]);
  const errs = data.errors || (data.error ? [data.error] : []);
  const head = mockSkipped ? '未发起请求'
    : ok ? (dry ? '配置校验通过' : '连接成功') : '不通';
  box.className = `ai-test-result ${mockSkipped ? 'warn' : (ok ? 'good' : 'bad')}`;
  box.innerHTML = `
    <div class="atr-head">
      <b>${escapeHtml(head)}</b>
      <span class="atr-mode">${escapeHtml(data.mode || '')}</span>
      <button type="button" class="atr-close" title="关闭" onclick="closeAiTestResult()">×</button>
    </div>
    ${errs.length ? `<ul class="atr-errs">${errs.map(e => `<li>${escapeHtml(String(e))}</li>`).join('')}</ul>` : ''}
    ${data.body ? `<pre class="atr-body">${escapeHtml(String(data.body).slice(0, 600))}</pre>` : ''}
    ${rows.map(([k, v]) => `<div class="atr-row"><b>${escapeHtml(k)}</b><span>${escapeHtml(String(v))}</span></div>`).join('')}
    ${data.note ? `<p class="atr-note">${escapeHtml(data.note)}</p>` : ''}
    ${ok && !dry ? '<p class="atr-note">已确认可用。保存后流水线会走这个地址。</p>' : ''}
  `;
}

async function testAi(dry) {
  const btn = dry ? $('#btn-ai-dry') : $('#btn-ai-test');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = dry ? '校验中…' : '连接中…';
  const box = $('#ai-test-result');
  try {
    const r = await fetch('/api/ai/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai: collectAi(), dry: !!dry }),
    });
    const data = await r.json();
    renderAiTestResult(data, dry);
    if (data.mock && !dry) toast('Mock 模式，没有发起真实请求', 'err');
    else if (!r.ok && !data.ok) toast(data.error || (data.errors || [])[0] || '不通', 'err');
    else toast(dry ? '配置校验通过' : '连接成功', 'ok');
  } catch (e) {
    box.classList.remove('hidden');
    box.className = 'ai-test-result bad';
    box.innerHTML = `<div class="atr-head"><b>请求发出失败</b><button type="button" class="atr-close" title="关闭" onclick="closeAiTestResult()">×</button></div><ul class="atr-errs"><li>${escapeHtml(String(e))}</li></ul>`;
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function fetchAiModels() {
  const btn = $('#btn-ai-models');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '拉取中…';
  try {
    const r = await fetch('/api/ai/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai: collectAi() }),
    });
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || (data.errors || []).join('；') || `HTTP ${r.status}`);
    const names = data.models || [];
    modelSuggestList = names.slice();
    $('#cfg-ai-model-hint').textContent = `该端点返回 ${names.length} 个模型，点击下方即可填入`;
    showModelSuggest();
    toast(`拉到 ${names.length} 个模型`, 'ok');
  } catch (e) {
    $('#cfg-ai-model-hint').textContent = `拉取失败：${e}`;
    toast(`拉取模型列表失败: ${e}`, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function saveSettings() {
  const body = {
    repo: {
      path: $('#cfg-repo-path').value.trim(),
      branch: $('#cfg-repo-branch').value.trim(),
    },
    ai: collectAi(),
    ones: {
      base_url: $('#cfg-ones-url').value.trim(),
      project_uuid: $('#cfg-ones-uuid').value.trim(),
      team_uuid: $('#cfg-ones-team').value.trim(),
      email: $('#cfg-ones-email').value.trim(),
    },
    figma: {},
    pagelogin: {
      enabled: $('#cfg-pagelogin-enabled').checked,
      email: $('#cfg-pagelogin-email').value.trim(),
    },
    gate: {
      commands_text: $('#cfg-gate-commands').value,
      degraded: $('#cfg-gate-degraded').checked,
    },
    playwright: {
      base_url: $('#cfg-app-url').value.trim(),
      headless: $('#cfg-headless').checked,
    },
  };
  const token = $('#cfg-ones-token').value.trim();
  if (token) body.ones.token = token;
  const onesPassword = $('#cfg-ones-password').value;
  if (onesPassword) body.ones.password = onesPassword;
  const figmaToken = $('#cfg-figma-token').value.trim();
  if (figmaToken) body.figma.token = figmaToken;
  const pagePassword = $('#cfg-pagelogin-password').value;
  if (pagePassword) body.pagelogin.password = pagePassword;

  const saveBtns = [$('#btn-save'), $('#btn-save-ai')].filter(Boolean);
  saveBtns.forEach(b => { b.disabled = true; });
  try {
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || data.error || `HTTP ${r.status}`);
    toast('配置已保存', 'ok');
    aiKeyCleared = false;
    $('#cfg-ai-key').value = '';
    $('#cfg-ones-token').value = '';
    $('#cfg-ones-password').value = '';
    $('#cfg-figma-token').value = '';
    $('#cfg-pagelogin-password').value = '';
    await loadSettings();
    await loadHealth();
  } catch (e) {
    toast(`保存失败: ${e}`, 'err');
  } finally {
    saveBtns.forEach(b => { b.disabled = false; });
  }
}

function renderGatePreview() {
  const el = $('#gate-preview');
  if (!el) return;
  const cmds = (settingsCache && settingsCache.gate && settingsCache.gate.commands) || [];
  const h = healthCache || {};
  let html;
  const badge = (lv) => `<span class="gate-badge gate-${escapeAttr(lv)}">${escapeHtml(GATE_TEXT[lv] || lv)}</span>`;
  if (cmds.length) {
    html = `<div class="gp-title">${badge('explicit')} 逐条真实执行以下命令：</div>`
      + `<ul class="gp-list">${cmds.map(c => `<li><span class="gp-name">${escapeHtml(c.name || '')}</span><code>${escapeHtml(c.cmd || '')}</code></li>`).join('')}</ul>`;
  } else if ((h.gate_commands || []).length) {
    html = `<div class="gp-title">${badge('package_json')} 已从 package.json 探测到：</div>`
      + `<ul class="gp-list">${h.gate_commands.map(c => `<li><code>${escapeHtml(c)}</code></li>`).join('')}</ul>`;
  } else {
    html = `<div class="gp-title">${badge('degraded')} 无可用命令，闸门降级：</div>`
      + '<p>仓库里没有可识别的 type-check / lint / test / build 脚本，闸门只做括号与引号闭合、node --check、py ast.parse。<b>它不代表代码正确</b>。</p>';
  }
  html += '<p class="hint">留空则自动从 package.json 探测 type-check/lint/test/build；把团队真正用来验收的命令写进来（explicit）可信度最高。</p>';
  el.innerHTML = html;
}

function updateRepoHint(path) {
  const el = $('#repo-hint');
  if (!path) { el.textContent = ''; return; }
  el.textContent = '保存时校验路径是否存在';
  el.className = 'repo-hint muted';
}
$('#cfg-repo-path')?.addEventListener('change', e => updateRepoHint(e.target.value.trim()));
$('#cfg-ai-provider')?.addEventListener('change', onProviderChange);
$('#cfg-ai-auth')?.addEventListener('change', onAuthChange);
$('#cfg-ai-mock')?.addEventListener('change', e => {
  const grp = e.target.closest('.settings-group');
  if (grp) grp.classList.toggle('is-mock', e.target.checked);
  $('#cfg-ai-key').placeholder = e.target.checked
    ? 'Mock 模式下不会发起真实调用'
    : '填写后才会发起真实调用（留空=沿用已保存的 Key）';
});
$('#cfg-gate-commands')?.addEventListener('input', () => {
  if (!settingsCache) return;
  settingsCache.gate = settingsCache.gate || {};
  settingsCache.gate.commands = parseGatePreviewLines($('#cfg-gate-commands').value);
  renderGatePreview();
});

function parseGatePreviewLines(text) {
  return (text || '').split('\n')
    .map(l => l.trim())
    .filter(l => l && !l.startsWith('#'))
    .map(l => ({ name: l.split(/\s+/)[0], cmd: l }));
}


/* ================= run ================= */
/* 步骤条与真实结果关联：
   - 运行开始：只有「拉取」进入进行中（呼吸），其余待定——不假装推进；
   - 响应到达后按返回数据逐阶段判定 done/warn/fail/off，一直保留到下次运行。
   /api/run 是单次请求返回全部结果，没有分步事件流，因此不伪造中间进度。 */
const STAGE_STATES = ['active', 'done', 'warn', 'fail', 'off', 'idle'];

function setStagesStates(states) {
  document.querySelectorAll('.stage').forEach((s, i) => {
    s.classList.remove(...STAGE_STATES);
    s.classList.add(states[i] || 'idle');
  });
}

function stagesRunning() {
  return ['active', 'idle', 'idle', 'idle', 'idle'];
}

function stagesFromRunResults(data) {
  const results = data.results || [];
  if (data.source === 'error' || !results.length) {
    return ['fail', 'off', 'off', 'off', 'off'];   // 拉取失败/为空，后面没有发生
  }
  const arr = ['done', 'done', 'done', 'done', 'done'];
  const rate = (bad) => results.filter(bad).length;
  // 定位：locate_empty=护栏触发（警告）；invalid 由提案步表达
  const locBad = rate(x => x.locate_empty);
  arr[1] = locBad === results.length ? 'warn' : (locBad ? 'warn' : 'done');
  // 生成提案：invalid=没有可用补丁
  const invalid = rate(x => x.status === 'invalid');
  arr[2] = invalid === results.length ? 'fail' : (invalid ? 'warn' : 'done');
  // 闸门：gate_ok=false（含 gate_failed 待人工确认）
  const gateBad = rate(x => x.gate_ok === false);
  arr[3] = gateBad === results.length ? 'fail' : (gateBad ? 'warn' : 'done');
  // 沉淀：kb_path 为空即没落卡
  const noKb = rate(x => !x.kb_path);
  arr[4] = noKb === results.length ? 'fail' : (noKb ? 'warn' : 'done');
  return arr;
}

function stagesFromProbeResults(data) {
  // 试运行只涉及前两步；3-5 标记为 off（不适用）
  const off = ['off', 'off', 'off', 'off', 'off'];
  const results = data.results || [];
  if (data.source === 'error' || data.fetch_error) {
    return ['fail', 'fail', off[2], off[3], off[4]];
  }
  off[0] = 'done';
  if (!results.length) { off[1] = 'warn'; return off; }   // 接口通但没工单
  const errs = results.filter(x => x.analysis_error).length;
  const empty = results.filter(x => x.locate_empty).length;
  off[1] = errs === results.length ? 'fail' : (errs || empty ? 'warn' : 'done');
  return off;
}

function setStages(mode) {
  // 兼容保留：'reset' 恢复初始态；'done' 全部完成（不再被假动画使用）
  setStagesStates(mode === 'done' ? ['done', 'done', 'done', 'done', 'done'] : ['idle', 'idle', 'idle', 'idle', 'idle']);
}

/* ---------- 实时运行：SSE 流式消费 + 阶段/AI 输出实时视图 ---------- */
let liveRefs = null;
let liveStageState = ['idle', 'idle', 'idle', 'idle', 'idle'];
const LIVE_STAGE_IDX = { fetch: 0, locate: 1, patch: 2, proposal: 2, gate: 3, kb: 4 };

function _nowHMS() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function liveSetup(log) {
  const modelName = escapeHtml((healthCache && healthCache.ai_model) || '');
  log.innerHTML = '<div class="live-lines"></div>'
    + '<div class="ai-stream hidden"><div class="ai-stream-head">'
    + '<span class="ai-dot"></span><span>大模型实时输出</span>'
    + `<span class="ai-stream-meta">${modelName}</span></div>`
    + '<div class="ai-block think hidden"><div class="ai-block-label">思考</div><pre></pre></div>'
    + '<div class="ai-block out hidden"><div class="ai-block-label">输出</div><pre></pre></div>'
    + '</div>';
  liveStageState = ['active', 'idle', 'idle', 'idle', 'idle'];
  setStagesStates(liveStageState.slice());
  return {
    log,
    lines: log.querySelector('.live-lines'),
    stream: log.querySelector('.ai-stream'),
    think: log.querySelector('.ai-block.think'),
    out: log.querySelector('.ai-block.out'),
  };
}

function liveScroll() {
  if (!liveRefs) return;
  const el = liveRefs.log;
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) {
    el.scrollTop = el.scrollHeight;
  }
}

function liveLine(text, kind = 'dim') {
  if (!liveRefs) return;
  const div = document.createElement('div');
  div.className = `live-line ${kind}`;
  const t = document.createElement('span');
  t.className = 't';
  t.textContent = _nowHMS();
  const b = document.createElement('span');
  b.textContent = text;   // textContent 天然防注入
  div.appendChild(t);
  div.appendChild(b);
  liveRefs.lines.appendChild(div);
  liveScroll();
}

function liveAI(kind, text) {
  if (!liveRefs || !text) return;
  liveRefs.stream.classList.remove('hidden');
  const box = kind === 'reasoning' ? liveRefs.think : liveRefs.out;
  box.classList.remove('hidden');
  const pre = box.querySelector('pre');
  // 追加前记录是否贴近底部：贴底则跟随滚到最新输出；
  // 用户上翻阅读时（不贴底）不打扰，滚回底部即自动恢复跟随
  const near = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.textContent += text;
  if (near) pre.scrollTop = pre.scrollHeight;
  liveScroll();
}

function liveAIReset() {
  if (!liveRefs) return;
  liveRefs.think.classList.add('hidden');
  liveRefs.out.classList.add('hidden');
  liveRefs.think.querySelector('pre').textContent = '';
  liveRefs.out.querySelector('pre').textContent = '';
}

function liveStage(evt) {
  const idx = LIVE_STAGE_IDX[evt.stage];
  if (idx == null) return;   // verify 等只进日志行，不映射步骤条
  if (evt.status === 'start') {
    liveStageState[idx] = 'active';
  } else {
    liveStageState[idx] = evt.status === 'done' ? 'done' : evt.status;
    if (evt.status === 'done' && idx < 4 && liveStageState[idx + 1] === 'idle') {
      liveStageState[idx + 1] = 'active';
    }
  }
  setStagesStates(liveStageState.slice());
}

async function sseConsume(url, bodyObj, onEvent, signal) {
  /** POST + SSE：逐事件回调，返回 done 事件（连接结束时没收到则返回 null）。
      `signal` 可选：AbortController 的 signal，用于「停止」按钮主动断开。 */
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyObj),
    signal,
  });
  if (!r.ok || !r.body) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); if (j && j.error) msg = j.error; } catch (_) {}
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  let doneEvt = null;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) !== -1) {
        const chunk = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        for (const line of chunk.split('\n')) {
          const t = line.trim();
          if (!t.startsWith('data:')) continue;   // ': ping' 心跳注释
          const payload = t.slice(5).trim();
          if (!payload) continue;
          let evt;
          try { evt = JSON.parse(payload); } catch (_) { continue; }
          if (evt.type === 'done') { doneEvt = evt; continue; }
          onEvent(evt);
        }
      }
    }
  } catch (e) {
    try { reader.cancel(); } catch (_) {}
    throw e;
  }
  return doneEvt;
}

function aiReplayHtml(buffers) {
  const entries = Object.entries(buffers || {}).filter(([, v]) => v.think || v.out);
  if (!entries.length) return '';
  const blocks = entries.map(([id, v]) =>
    `<div class="ai-replay-defect">工单 ${escapeHtml(id)}</div>`
    + '<div class="ai-stream">'
    + (v.think
        ? `<div class="ai-block think"><div class="ai-block-label">思考</div><pre>${escapeHtml(v.think)}</pre></div>`
        : '')
    + (v.out
        ? `<div class="ai-block out"><div class="ai-block-label">输出</div><pre>${escapeHtml(v.out)}</pre></div>`
        : '')
    + '</div>'
  ).join('');
  return `<details class="ai-replay"><summary>展开本次大模型的实时思考 / 输出记录</summary>${blocks}</details>`;
}

async function runPipeline() {
  // 示例数据开关已移除：流水线只处理真实 ONES 工单
  const skipVerify = $('#opt-skip-verify').checked;
  const mineOnly = $('#opt-mine').checked;
  const limit = $('#opt-limit').value;
  const defectId = $('#opt-defect').value.trim();
  const status = $('#status');
  const log = $('#run-log');

  status.className = 'status-pill running';
  status.textContent = '运行中…';
  runLogTitle = '运行结果';
  log.className = 'log';
  liveRefs = liveSetup(log);
  setRunExpand();
  document.querySelectorAll('button').forEach(b => b.disabled = true);

  const buffers = {};   // defectId -> {think, out}，结束后作为回放渲染
  let currentDefect = '（等待工单）';

  const onEvent = (evt) => {
    if (evt.type === 'stage') {
      liveLine(evt.detail || '', evt.status === 'start' ? 'dim'
        : evt.status === 'fail' ? 'bad' : evt.status === 'warn' ? 'warn' : 'ok');
      liveStage(evt);
    } else if (evt.type === 'defect') {
      currentDefect = evt.id || `#${evt.index}`;
      liveLine(`▶ 工单 ${currentDefect}（${evt.index}/${evt.total}）：${evt.title}`, 'head');
      liveAIReset();
      liveStageState = ['done', 'active', 'idle', 'idle', 'idle'];
      setStagesStates(liveStageState.slice());
    } else if (evt.type === 'ai_delta') {
      const buf = buffers[currentDefect] || (buffers[currentDefect] = { think: '', out: '' });
      if (evt.kind === 'reasoning') buf.think += evt.text || '';
      else buf.out += evt.text || '';
      liveAI(evt.kind, evt.text || '');
    } else if (evt.type === 'fatal') {
      throw new Error(evt.error || '流水线异常');
    }
  };

  try {
    const doneEvt = await sseConsume('/api/run/stream', {
      skip_verify: skipVerify, limit: Number(limit) || 1,
      defect_id: defectId || null, mine_only: mineOnly,
    }, onEvent);
    if (!doneEvt) throw new Error('连接中断，未收到完整结果');
    const data = doneEvt.payload || {};
    if (data.error) throw new Error(data.error);
    log.innerHTML = renderRunResults(data) + aiReplayHtml(buffers);
    status.className = 'status-pill ok';
    status.textContent = `产出 ${data.count} 个提案 · AI:${data.ai_mode}`;
    setStagesStates(stagesFromRunResults(data));
    await Promise.all([loadProposals(), loadStats(), loadKb(), loadHealth()]);
  } catch (e) {
    status.className = 'status-pill error';
    status.textContent = '失败';
    liveLine(`✗ ${String((e && e.message) || e)}`, 'bad');
    setStagesStates(['fail', 'off', 'off', 'off', 'off']);
  } finally {
    liveRefs = null;
    setRunExpand();
    document.querySelectorAll('button').forEach(b => b.disabled = false);
  }
}

function renderRunResults(data) {
  const results = data.results || [];
  const head = `<div class="log-title">本次产出 ${results.length} 个提案。`
    + `${data.wrote_any_files === false ? '未写入目标仓库任何文件（wrote_any_files=false）。' : ''}`
    + ' 请在「待审批提案」里逐条审 diff 后再采纳。</div>';
  const rows = results.map(x => {
    const files = (x.files || []).map(f => `<code>${escapeHtml(f)}</code>`).join(' ')
      || '<i>无文件改动</i>';
    const errs = (x.errors || []).map(e => `<li>${escapeHtml(e)}</li>`).join('');
    const warns = (x.warnings || []).map(w => `<li>${escapeHtml(w)}</li>`).join('');
    return `<div class="run-row">`
      + `<div class="run-row-head"><b>${escapeHtml(x.id || '(无工单 ID)')}</b>`
      + `${statusBadge(x.status)}${gateBadge(x.gate_level, x.gate_ok)}`
      + `<span class="muted">${escapeHtml(x.category || '—')}</span>`
      + (x.proposal_id ? `<a class="link" onclick="openProposal('${escapeAttr(x.proposal_id)}')">查看提案</a>` : '')
      + `</div>`
      + `<div class="run-files">${files}</div>`
      + (x.status === 'invalid'
        ? (looksNonFrontend(x)
          ? '<div class="run-warn">模型判断根因不在前端仓库（见下方根因与证据链），未生成前端补丁——建议转后端/接口排查，或在工单补充后端日志后重跑。</div>'
          : '<div class="run-bad">未生成可应用补丁：模型没给出可用 SEARCH/REPLACE 块，这条提案没有 diff 可采纳，只会沉淀分析卡片。</div>')
        : '')
      + (errs ? `<ul class="run-iss bad">${errs}</ul>` : '')
      + (warns ? `<ul class="run-iss warn">${warns}</ul>` : '')
      + (x.verify_status ? `<div class="muted">页面验证：${escapeHtml(x.verify_status)}</div>` : '')
      + (x.root_cause ? `<div class="run-root">${escapeHtml(x.root_cause)}</div>` : '')
      + `</div>`;
  }).join('');
  return `<div class="log-html">${head}${rows || '<div class="muted">没有产出任何提案。</div>'}</div>`;
}

async function probePipeline() {
  // 示例数据开关已移除，试运行只拉真实 ONES 工单
  const mineOnly = $('#opt-mine').checked;
  const limit = $('#opt-limit').value;
  const defectId = $('#opt-defect').value.trim();
  const status = $('#status');
  const log = $('#run-log');

  status.className = 'status-pill running';
  status.textContent = '试运行中…';
  runLogTitle = '试运行结果';
  log.className = 'log';
  liveRefs = liveSetup(log);
  setRunExpand();
  document.querySelectorAll('button').forEach(b => b.disabled = true);

  const buffers = {};
  let currentDefect = '（等待工单）';

  const onEvent = (evt) => {
    if (evt.type === 'stage') {
      liveLine(evt.detail || '', evt.status === 'start' ? 'dim'
        : evt.status === 'fail' ? 'bad' : evt.status === 'warn' ? 'warn' : 'ok');
      liveStage(evt);
    } else if (evt.type === 'defect') {
      currentDefect = evt.id || `#${evt.index}`;
      liveLine(`▶ 工单 ${currentDefect}（${evt.index}/${evt.total}）：${evt.title}`, 'head');
      liveAIReset();
      liveStageState = ['done', 'active', 'off', 'off', 'off'];
      setStagesStates(liveStageState.slice());
    } else if (evt.type === 'ai_delta') {
      const buf = buffers[currentDefect] || (buffers[currentDefect] = { think: '', out: '' });
      if (evt.kind === 'reasoning') buf.think += evt.text || '';
      else buf.out += evt.text || '';
      liveAI(evt.kind, evt.text || '');
    } else if (evt.type === 'fatal') {
      throw new Error(evt.error || '流水线异常');
    }
  };

  try {
    const doneEvt = await sseConsume('/api/probe/stream', {
      limit: Number(limit) || 1, defect_id: defectId || null,
      mine_only: mineOnly,
    }, onEvent);
    if (!doneEvt) throw new Error('连接中断，未收到完整结果');
    const data = doneEvt.payload || {};
    if (data.error) throw new Error(data.error);
    log.innerHTML = renderProbeResults(data) + aiReplayHtml(buffers);
    setStagesStates(stagesFromProbeResults(data));
    if (data.source === 'error') {
      status.className = 'status-pill error';
      status.textContent = 'ONES 拉取失败';
      toast('ONES 拉取失败，详见运行日志', 'err');
    } else if (data.count === 0) {
      status.className = 'status-pill error';
      status.textContent = '接口通了，但没拉到工单（见提示）';
      toast('ONES 接口正常但结果为空，详见运行日志的排查提示', 'warn');
    } else {
      status.className = 'status-pill ok';
      status.textContent = `试运行成功 · 拉到 ${data.count} 条真实工单`;
    }
  } catch (e) {
    status.className = 'status-pill error';
    status.textContent = '试运行失败';
    liveLine(`✗ ${String((e && e.message) || e)}`, 'bad');
    setStagesStates(['fail', 'fail', 'off', 'off', 'off']);
  } finally {
    liveRefs = null;
    setRunExpand();
    document.querySelectorAll('button').forEach(b => b.disabled = false);
  }
}

/* ---------- 运行/试运行结果放大查看 ---------- */
let runLogTitle = '运行结果';

function setRunExpand() {
  const log = $('#run-log');
  const btn = $('#btn-run-expand');
  if (!log || !btn) return;
  const hasContent = !!log.innerHTML.trim();
  btn.classList.toggle('hidden', !hasContent);
}

function openRunModal() {
  const log = $('#run-log');
  const body = $('#runmodal-body');
  if (!log || !body || !log.innerHTML.trim()) return;
  $('#runmodal-title').textContent = runLogTitle;
  body.innerHTML = log.innerHTML;
  body.scrollTop = 0;
  $('#runmodal').classList.remove('hidden');
}

function closeRunModal(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  $('#runmodal').classList.add('hidden');
}

function renderProbeResults(data) {
  const src = data.source || 'ones';
  const empty = !(data.results || []).length;
  const srcBadge = src === 'ones'
    ? (empty
        ? '<span class="ais-badge mock">接口正常 · 结果为空</span>'
        : '<span class="ais-badge live">真实 ONES 工单</span>')
    : '<span class="ais-badge mock">拉取失败</span>';
  const head = `<div class="log-title">试运行结果：只做了「拉取工单 + AI 定位」两步，`
    + `没有生成提案、没有进审批、没有写任何文件。${srcBadge}</div>`
    + (data.fetch_error
        ? `<ul class="run-iss bad"><li>ONES 拉取失败：${escapeHtml(data.fetch_error)}</li>`
          + '<li>请检查侧边栏的 ONES 地址 / Project UUID / Access Token（JWT 过期后需重新复制）。</li></ul>'
        : '')
    + (data.notes || []).map(n => `<div class="probe-note">${escapeHtml(n)}</div>`).join('')
    + ((data.hints || []).length
      ? `<ul class="run-iss warn">${(data.hints || []).map(h => `<li>${escapeHtml(h)}</li>`).join('')}</ul>`
      : '');
  const rows = (data.results || []).map(x => {
    const d = x.defect || {};
    const a = x.analysis || null;
    const files = (a && a.suspect_files || []).map(f => `<code>${escapeHtml(f)}</code>`).join(' ');
    const kw = (a && a.keywords || []).map(k => `<span class="probe-kw">${escapeHtml(k)}</span>`).join('');
    const desc = String(d.description || '').replace(/<[^>]+>/g, ' ').slice(0, 300);
    const meta = [
      d.number ? `#${d.number}` : '',
      d.issue_type || '',
      (d.assignee || {}).name ? `负责人: ${d.assignee.name}` : '',
    ].filter(Boolean).map(m => `<span class="muted">${escapeHtml(m)}</span>`).join(' ');
    return `<div class="run-row">`
      + `<div class="run-row-head"><b>${escapeHtml(d.id || '(无工单 ID)')}</b>`
      + meta
      + `<span class="muted">${escapeHtml(d.status || '')}</span>`
      + (a ? `<span class="muted">${escapeHtml(a.category || '—')}</span>` : '')
      + `</div>`
      + `<div class="probe-title">${escapeHtml(d.title || '')}</div>`
      + (desc ? `<div class="probe-desc">${escapeHtml(desc)}</div>` : '')
      + (a && a.root_cause ? `<div class="run-root">${escapeHtml(a.root_cause)}</div>` : '')
      + (a && a.explanation ? `<div class="probe-desc">${escapeHtml(String(a.explanation).slice(0, 400))}</div>` : '')
      + (files ? `<div class="run-files">${files}</div>` : '')
      + (kw ? `<div class="probe-kws">${kw}</div>` : '')
      + (a && a.ai_error ? `<ul class="run-iss warn"><li>AI 提示：${escapeHtml(a.ai_error)}</li></ul>` : '')
      + (x.analysis_error ? `<ul class="run-iss bad"><li>定位失败：${escapeHtml(x.analysis_error)}</li></ul>` : '')
      + `</div>`;
  }).join('');
  return `<div class="log-html">${head}${rows || '<div class="muted">没有拉到任何工单。</div>'}</div>`;
}

/* ================= tabs（统计 / 问答 / 缺陷修复 / 长任务作业 / 需求开发 / 接口联调 / 代码测试） ================= */
let activeTab = 'stats';
let figmaDesign = '';
let artifactsCache = [];
let amCurrent = null;       // 产出物详情弹窗当前对象
let amPendingError = '';
let amDiff = null;          // 基线 diff（/diff 端点），null = 不可用
let amFileFullView = false; // 文件区视图：false=改动高亮 diff，true=完整文件

const ARTIFACT_TAB_LABEL = {
  reqdev: '需求开发', apidebug: '接口联调', codetest: '代码测试',
  // 问答里「帮我改一下」产出的改动提案，走同一条「产出物 → 人工采纳」链路
  chat: '问答',
  // 长任务作业的产出物同样走「产出物 → 人工采纳」这条链路
  team: '长任务作业',
};
const ARTIFACT_STATUS_TEXT = {
  pending: '待采纳',
  apply_failed: '闸门未通过',
  applied: '已采纳',
  rejected: '已拒绝',
};
const ARTIFACT_STATUS_ORDER = ['pending', 'apply_failed', 'applied', 'rejected'];

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll('#tabs .tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(p =>
    p.classList.toggle('active', p.id === `pane-${name}`));
  const flow = $('#stage-flow');
  if (flow) flow.classList.toggle('hidden', name !== 'defect');
  const artPanel = $('#artifact-panel');
  const isTaskTab = !!ARTIFACT_TAB_LABEL[name];
  artPanel.classList.toggle('hidden', !isTaskTab);
  if (isTaskTab) {
    $('#artifact-type-label').textContent = ARTIFACT_TAB_LABEL[name];
    loadArtifacts();
  } else if (name === 'extensions') {
    loadExtensions();
  }
  // 长任务作业：进页签时拉角色表 + 历史（首次进才拉角色，之后只刷历史）
  if (name === 'team') teamInit();
  // 统计：进页签就重算一次（用户可能刚跑完一次任务）
  if (name === 'stats') usageInit();
  // 问答：进页签刷新会话列表（正在流式回答时不打断当前渲染）
  if (name === 'chat') chatInit();
}

/* ---------- Figma 设计稿拉取 ---------- */
// 取稿通道：token = 官方 REST（推荐，有结构化树）；browser = 本机浏览器兜底（只有可见文案）
let figmaMode = localStorage.getItem('figma-fetch-mode') || 'token';
function setFigmaMode(mode) {
  figmaMode = mode === 'browser' ? 'browser' : 'token';
  localStorage.setItem('figma-fetch-mode', figmaMode);
  document.querySelectorAll('#req-figma-mode .seg-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === figmaMode);
  });
  const tf = $('#req-figma-token-field');
  if (tf) tf.classList.toggle('hidden', figmaMode === 'browser');
}

async function fetchFigma() {
  const btn = $('#btn-figma');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '拉取中…';
  const box = $('#figma-tree');
  box.className = 'log';
  try {
    const r = await fetch('/api/figma/fetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: $('#req-figma-url').value.trim(),
        token: figmaMode === 'token' ? $('#req-figma-token').value.trim() : '',
        browser_fallback: figmaMode === 'browser',
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    figmaDesign = data.tree || '';
    const head = data.source === 'browser'
      ? `浏览器通道拉取 · 「${escapeHtml(data.file_name || '')}」 · 页面可见文本（无图层结构）`
      : `已拉取「${escapeHtml(data.file_name || '')}」· 节点「${escapeHtml(data.node_name || '')}」· ${data.node_count} 个节点`;
    box.innerHTML = `<div class="log-title">${head}`
      + `${data.truncated ? '（内容过长，已截断）' : ''}</div>`
      + (data.note ? `<div class="info-note">${escapeHtml(data.note)}</div>` : '')
      + `<div class="log-html">${escapeHtml(data.tree || '')}</div>`;
    toast(data.source === 'browser' ? '设计稿已通过浏览器通道拉取（结构信息不全）' : '设计稿拉取成功',
          data.source === 'browser' ? 'warn' : 'ok');
  } catch (e) {
    figmaDesign = '';
    box.textContent = `Figma 拉取失败: ${e}`;
    toast(`Figma 拉取失败: ${e}`, 'err');
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

/* ---------- 代码测试：只读预览目标文件 ---------- */
async function loadTestFile() {
  const rel = $('#test-file').value.trim();
  if (!rel) { toast('请先填目标文件路径（相对仓库根）', 'err'); return; }
  const box = $('#test-preview');
  box.className = 'log';
  box.textContent = '读取中…';
  try {
    const r = await fetch(`/api/repo/file?path=${encodeURIComponent(rel)}`);
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    box.innerHTML = `<div class="log-title">${escapeHtml(data.path)}${data.truncated ? '（超过 2 万字符已截断）' : ''}</div>`
      + `<div class="log-html">${escapeHtml(data.content || '')}</div>`;
  } catch (e) {
    box.textContent = `读取失败: ${e}`;
    toast(`读取失败: ${e}`, 'err');
  }
}

/* ---------- 三大任务运行 ---------- */
async function runTask(kind) {
  let body;
  if (kind === 'reqdev') {
    const req = $('#req-desc').value.trim();
    body = {
      task: kind,
      requirement: req,
      framework: $('#req-framework').value,
      design: figmaDesign,
      repo_context: $('#req-context').value,
      notes: $('#req-notes').value.trim(),
      title: (req || 'Figma 需求').slice(0, 60),
    };
    if (!figmaDesign && !req) { toast('请先拉取设计稿或填写需求描述', 'err'); return; }
  } else if (kind === 'apidebug') {
    const doc = $('#api-doc').value;
    body = {
      task: kind, api_doc: doc, base_url: $('#api-base').value.trim(),
      framework: $('#api-framework').value, repo_context: $('#api-context').value,
      notes: $('#api-notes').value.trim(),
    };
    if (!doc.trim()) { toast('请粘贴接口文档', 'err'); return; }
  } else {
    const rel = $('#test-file').value.trim();
    body = {
      task: kind, file_path: rel, framework: $('#test-framework').value,
      requirements: $('#test-focus').value.trim(),
    };
    if (!rel) { toast('请填写目标文件路径（相对仓库根）', 'err'); return; }
  }

  const status = $('#status');
  status.className = 'status-pill running';
  status.textContent = 'AI 生成中…';
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const r = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.error || data.detail) throw new Error(data.error || data.detail);
    status.className = 'status-pill ok';
    status.textContent = `产出物已生成 · AI:${data.ai_mode || '?'}`;
    if (data.artifact && data.artifact.error) {
      toast(`产出物已保存，但 AI 输出不完整：${data.artifact.error}`, 'warn');
    } else {
      toast('产出物已生成，请在下方审阅后采纳', 'ok');
    }
    await Promise.all([loadArtifacts(), loadHealth()]);
  } catch (e) {
    status.className = 'status-pill error';
    status.textContent = '任务失败';
    toast(`任务失败: ${e}`, 'err');
  } finally {
    document.querySelectorAll('button').forEach(b => b.disabled = false);
  }
}

/* ---------- 产出物列表 ---------- */
async function loadArtifacts() {
  if (activeTab === 'defect') return;
  const el = $('#artifacts');
  try {
    const r = await fetch(`/api/artifacts?type=${encodeURIComponent(activeTab)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    artifactsCache = await r.json();
    renderArtifacts();
  } catch (e) {
    el.innerHTML = `<div class="empty">产出物加载失败: ${escapeHtml(String(e))}</div>`;
  }
}

function setArtifactBadge() {
  const badge = $('#artifact-count');
  if (!badge) return;
  const open = artifactsCache.filter(a => a.status === 'pending' || a.status === 'apply_failed').length;
  badge.textContent = `${open} 待采纳 / ${artifactsCache.length} 条`;
  badge.classList.toggle('alert', open > 0);
  badge.classList.toggle('hidden', artifactsCache.length === 0);
}

function artifactStatusText(s) { return ARTIFACT_STATUS_TEXT[s] || s || '—'; }

function renderArtifacts() {
  const el = $('#artifacts');
  if (!el) return;
  setArtifactBadge();
  if (!artifactsCache.length) {
    el.innerHTML = '<div class="empty">还没有产出物。填写左侧信息点「生成」后，AI 结果会在这里等你审阅采纳。</div>';
    return;
  }
  const list = artifactsCache.slice().sort((a, b) =>
    (ARTIFACT_STATUS_ORDER.indexOf(a.status) + 1 || 99) - (ARTIFACT_STATUS_ORDER.indexOf(b.status) + 1 || 99)
    || String(b.updated || b.created || '').localeCompare(String(a.updated || a.created || '')));
  el.innerHTML = list.map(artifactCard).join('');
}

function artifactCard(a) {
  const files = a.files || [];
  return `
    <div class="pcard pc-${escapeAttr(a.status || 'pending')}" onclick="openArtifact('${escapeAttr(a.id)}')">
      <div class="pcard-top">
        <span class="st st-${escapeAttr(a.status || 'pending')}">${escapeHtml(artifactStatusText(a.status))}</span>
        <span class="pcard-id">${escapeHtml(a.id || '')}</span>
        <span class="pcard-time">${escapeHtml(relTime(a.updated || a.created))}</span>
      </div>
      <div class="pcard-title">${escapeHtml(a.title || '(无标题)')}</div>
      <div class="pcard-meta">
        <span>${escapeHtml(a.type_label || a.type || '—')}</span>
        <span>AI ${escapeHtml(a.ai_mode || '—')}</span>
        <span>${files.length} 个文件</span>
      </div>
      ${a.error ? `<div class="pcard-root" style="color:var(--danger)">${escapeHtml(a.error)}</div>`
        : a.summary ? `<div class="pcard-root">${escapeHtml(a.summary)}</div>` : ''}
    </div>`;
}

/* ---------- 产出物详情 ---------- */
async function openArtifact(id) {
  try {
    // 详情与基线 diff 并行拉；diff 失败不阻塞弹窗，只是退回完整文件视图
    const [r, dr] = await Promise.all([
      fetch(`/api/artifacts/${encodeURIComponent(id)}`),
      fetch(`/api/artifacts/${encodeURIComponent(id)}/diff`).catch(() => null),
    ]);
    let data = null;
    try { data = await r.json(); } catch (e) { /* 非 JSON */ }
    if (!r.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`);
    amCurrent = data;
    amDiff = null;
    amFileFullView = false;
    if (dr && dr.ok) {
      try { amDiff = await dr.json(); } catch (e) { /* diff 缺失可容忍 */ }
    }
    renderArtifact(data);
    $('#amodal').classList.remove('hidden');
  } catch (e) {
    toast(`打开产出物详情失败: ${e}`, 'err');
  }
}

function closeAModal(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  $('#amodal').classList.add('hidden');
  amCurrent = null;
  amPendingError = '';
}

function renderArtifact(a) {
  const pre = a.preflight_live || {};
  const problems = (pre.problems || []).filter(Boolean);

  $('#amodal-title').textContent = a.id || '';
  const stEl = $('#amodal-status');
  stEl.className = `st st-${escapeAttr(a.status || 'pending')}`;
  stEl.textContent = artifactStatusText(a.status);
  stEl.classList.remove('hidden');

  const branchBad = pre.current_branch && pre.expected_branch && pre.current_branch !== pre.expected_branch;
  $('#amodal-meta').innerHTML = [
    ['类型', a.type_label],
    ['技术栈', (a.context || {}).framework],
    ['AI', a.ai_mode],
    ['分支', `${pre.current_branch || '未知'}${pre.expected_branch ? ` → 期望 ${pre.expected_branch}` : ''}`],
    ['工作区', pre.dirty ? '有未提交改动' : '干净'],
    ['创建', relTime(a.created)],
  ].filter(([_, v]) => v).map(([k, v]) =>
    `<span><b>${escapeHtml(k)}:</b> ${escapeHtml(String(v))}</span>`
  ).join('')
  + (((a.context || {}).skills_used || []).length
    ? `<span><b>技能:</b> ${escapeHtml(a.context.skills_used.join('、'))}</span>` : '')
  + (((a.context || {}).tools_used || []).length
    ? `<span><b>MCP 工具:</b> ${escapeHtml(a.context.tools_used.join('、'))}</span>` : '')
  + (branchBad ? '<span class="cnt bad">分支与配置不一致，采纳会被拒</span>' : '');

  const banner = $('#am-preflight');
  if (problems.length) {
    banner.innerHTML = '<b>仓库预检未通过 —— 现在点采纳一定会被后端拒绝：</b>'
      + `<ul>${problems.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`
      + '<span class="muted">修好这些（如切换到配置的分支）后重新打开即可重试。</span>';
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
    banner.innerHTML = '';
  }

  const errBox = $('#am-error');
  if (amPendingError) {
    errBox.innerHTML = `<b>后端拒绝了这个操作：</b>${escapeHtml(amPendingError)}`;
    errBox.classList.remove('hidden');
    amPendingError = '';
  } else {
    errBox.classList.add('hidden');
    errBox.innerHTML = '';
  }

  $('#am-summary').innerHTML = '<h4>摘要</h4>' + kv('说明', a.summary)
    + (a.error ? kv('AI 输出问题', a.error, 'bad') : '');

  $('#am-plan').innerHTML = a.plan
    ? `<h4>实现 / 联调计划</h4><pre class="diff">${escapeHtml(a.plan)}</pre>` : '';

  const cases = a.cases || [];
  $('#am-cases').innerHTML = cases.length
    ? `<h4>测试用例（${cases.length}）</h4><ul class="check-list">${cases.map(c =>
        `<li class="ok"><div class="check-head"><b>${escapeHtml(c.name || '')}</b>`
        + `<span>${escapeHtml(c.purpose || '')}</span></div></li>`).join('')}</ul>` : '';

  renderArtifactFiles(a);
  const checklist = a.checklist || [];
  $('#am-checklist').innerHTML = checklist.length
    ? `<h4>Checklist</h4><ul class="note-list">${checklist.map(c => `<li>${mdBold(c)}</li>`).join('')}</ul>` : '';

  $('#am-mock').innerHTML = a.mock_data
    ? `<h4>Mock 数据</h4><pre class="diff">${escapeHtml(a.mock_data)}</pre>` : '';

  $('#am-note').value = (a.decision && a.decision.note) || (a.apply && a.apply.note) || '';
  renderAActions(a);
}

function renderArtifactFiles(a) {
  const files = a.files || [];
  const diffByPath = {};
  ((amDiff && amDiff.files) || []).forEach(d => { diffByPath[d.path] = d; });
  const applied = a.status === 'applied';
  // 新建文件也要进 diff 视图：后端对不存在的文件返回全行 add，绿底展示即「整文件都是新增」
  const hasDiff = files.some(f => {
    const d = diffByPath[f.path];
    return d && !d.stale && (d.added || d.removed);
  });
  const showDiff = hasDiff && !applied && !amFileFullView;

  let html = `<h4>将写入的文件（${files.length}）`;
  if (hasDiff) {
    html += applied
      ? '<span class="muted" style="font-weight:400">已采纳，内容与工作区一致</span>'
      : `<button type="button" class="am-toggle-btn" id="am-file-view-toggle">${amFileFullView ? '查看改动高亮' : '查看完整文件'}</button>`;
  }
  html += '</h4>';
  if (showDiff) {
    html += '<div class="muted" style="margin:2px 0 6px;font-size:12px">'
      + '高亮说明：<span class="dl add" style="padding:0 4px">绿底 = 新增行</span> · '
      + '<span class="dl del" style="padding:0 4px">红底 = 被删除/替换的旧行</span> · '
      + '淡显 = 未变行；新建文件的所有行均为新增。</div>';
  }

  html += files.length ? files.map(f => {
    const d = diffByPath[f.path];
    const usable = d && !d.stale;
    const isNew = !usable || !d.exists;
    const fullLines = String(f.content || '').split('\n').length;
    let head = `<div class="diff-file"><span class="df-path">${escapeHtml(f.path || '')}</span>`
      + `<span class="tag"${isNew ? ' title="仓库中不存在该文件，采纳时创建；以下所有行均为新增"' : ''}>${isNew ? '新建' : '覆盖已有'}</span>`;
    if (usable && (d.added || d.removed)) {
      head += `<span class="df-stat df-stat-add">+${d.added}</span>`
        + `<span class="df-stat df-stat-del">−${d.removed}</span>`;
    }
    head += `<span class="muted">${fullLines} 行</span></div>`;
    const desc = f.description
      ? `<div class="muted" style="margin:2px 0 4px">${escapeHtml(f.description)}</div>` : '';
    let body;
    if (showDiff && usable && (d.lines || []).length) {
      // 对齐视图：整文件展示，未变行淡显，新增行绿底，被替换的旧行红底。
      // 新建文件后端返回全行 add → 整块绿底，直观表达「全部为新代码」。
      body = '<pre class="diff diff-lines">' + d.lines.map(l =>
        `<span class="dl blk ${l.t === 'same' ? 'ctx' : l.t}">${escapeHtml(l.s)}</span>`
      ).join('') + '</pre>';
    } else {
      body = `<pre class="diff">${escapeHtml(f.content || '')}</pre>`;
    }
    return head + desc + body;
  }).join('') : '<p class="muted">没有文件内容。</p>';

  $('#am-files').innerHTML = html;
  const tbtn = $('#am-file-view-toggle');
  if (tbtn) tbtn.addEventListener('click', () => {
    amFileFullView = !amFileFullView;
    if (amCurrent) renderArtifactFiles(amCurrent);
  });
}

function renderAActions(a) {
  const st = a.status;
  const ap = $('#a-btn-approve'), fo = $('#a-btn-force'), rj = $('#a-btn-reject'),
        un = $('#a-btn-undo'), fl = $('#am-force-line');
  [ap, rj, un, fo].forEach(b => { b.disabled = false; b.title = ''; });
  ap.classList.remove('hidden');
  rj.classList.remove('hidden');
  un.classList.add('hidden');
  fo.classList.add('hidden');
  fl.classList.add('hidden');
  $('#am-force-confirm').checked = false;

  if (st === 'applied') {
    ap.classList.add('hidden'); rj.classList.add('hidden');
    un.classList.remove('hidden');
    un.title = '把写入的文件恢复为采纳前的内容（新建文件会被删除）';
    return;
  }
  if (st === 'rejected') {
    ap.classList.add('hidden'); rj.classList.add('hidden');
    return;
  }
  if (st === 'apply_failed') {
    fo.classList.remove('hidden');
    fl.classList.remove('hidden');
    ap.disabled = true;
    ap.title = '上次采纳时验收闸门未通过：只能勾选风险后「强制采纳」';
  }
}

function onAAprove() {
  if (!amCurrent) return;
  aDecide(amCurrent.id, 'approve', { note: ($('#am-note').value || '').trim() });
}
function onAForceApprove() {
  if (!amCurrent) return;
  if (!$('#am-force-confirm').checked) {
    toast('强制采纳前必须勾选“我已知悉风险”', 'err');
    return;
  }
  aDecide(amCurrent.id, 'approve', { force_gate: true, confirm_risk: true, note: ($('#am-note').value || '').trim() });
}
function onAReject() {
  if (!amCurrent) return;
  if (!confirm('确认拒绝该产出物？\n不会写任何文件，仅标记为 rejected。')) return;
  aDecide(amCurrent.id, 'reject', { note: ($('#am-note').value || '').trim() });
}
function onAUndo() {
  if (!amCurrent) return;
  const files = ((amCurrent.apply || {}).files) || [];
  const list = files.length ? `\n涉及文件：\n${files.map(f => '· ' + f).join('\n')}` : '';
  if (!confirm(`确认撤销采纳？\n会把写入的文件恢复为采纳前的内容（本次新建的文件会被删除）。${list}`)) return;
  aDecide(amCurrent.id, 'undo', {});
}

async function aDecide(id, action, payload) {
  const btns = [...document.querySelectorAll('#am-actions button')];
  btns.forEach(b => { b.dataset.was = b.disabled ? '1' : ''; b.disabled = true; });
  const prevNote = $('#am-note').value;
  const modalOpen = !$('#amodal').classList.contains('hidden');
  let ok = false, msg = '';
  try {
    const r = await fetch(`/api/artifacts/${encodeURIComponent(id)}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* 空响应 */ }
    ok = r.ok && data.ok !== false;
    msg = data.error
      || (Array.isArray(data.detail) ? data.detail.join('\n') : data.detail)
      || `HTTP ${r.status}`;
    if (ok) {
      if (action === 'approve') toast(`已写入工作区（${(data.files || []).length} 个文件，未 commit）`);
      else if (action === 'reject') toast('已拒绝该产出物，未改动仓库');
      else if (action === 'undo') toast(`已恢复采纳前的文件内容：${(data.restored || []).length} 个文件`);
      else toast('操作完成');
    } else {
      toast(msg || '操作被拒绝', 'err');
    }
  } catch (e) {
    ok = false;
    msg = `请求失败: ${e}`;
    toast(msg, 'err');
  }
  await Promise.all([loadArtifacts(), loadHealth()]);
  btns.forEach(b => { b.disabled = false; });
  if (modalOpen && amCurrent && amCurrent.id === id) {
    if (!ok) amPendingError = msg;
    await openArtifact(id);
    if ($('#am-note') && !$('#am-note').value) $('#am-note').value = prevNote;
  } else if (!ok) {
    amPendingError = msg;
  }
}

/* ================= 扩展能力（技能 / MCP） ================= */
let extSkills = [];
let extMcp = [];
let editingSkillId = null;
let editingMcpId = null;

const EXT_SCOPE_TEXT = { all: '全部任务', reqdev: '需求开发', apidebug: '接口联调', codetest: '代码测试' };

async function loadExtensions() {
  try {
    const r = await fetch('/api/extensions');
    const data = await r.json();
    extSkills = data.skills || [];
    extMcp = data.mcp || [];
    renderExtSkills();
    renderExtMcp();
  } catch (e) {
    toast(`扩展能力加载失败: ${e}`, 'err');
  }
}

/* ---------- 技能 ---------- */
function renderExtSkills() {
  const badge = $('#skill-count');
  if (badge) badge.textContent = `${extSkills.filter(s => s.enabled).length} 启用 / ${extSkills.length}`;
  const el = $('#skill-list');
  if (!el) return;
  if (!extSkills.length) {
    el.innerHTML = '<div class="empty">还没有技能。新增后，启用的技能会自动注入对应 AI 任务的 system prompt。</div>';
    return;
  }
  el.innerHTML = extSkills.map(s => {
    const scope = (s.scope || ['all']).map(x => `<span class="probe-kw">${escapeHtml(EXT_SCOPE_TEXT[x] || x)}</span>`).join(' ');
    return `<div class="ext-card ${s.enabled ? '' : 'ext-off'}">
      <div class="ext-head">
        <span class="st ${s.enabled ? 'st-applied' : 'st-rejected'}">${s.enabled ? '启用' : '停用'}</span>
        <b>${escapeHtml(s.name || '')}</b>
        <span class="muted">${escapeHtml(s.description || '')}</span>
        <span class="ext-actions">
          <button class="btn-mini" onclick="toggleSkill('${escapeAttr(s.id)}')">${s.enabled ? '停用' : '启用'}</button>
          <button class="btn-mini" onclick="showSkillForm('${escapeAttr(s.id)}')">编辑</button>
          <button class="btn-mini" onclick="deleteSkill('${escapeAttr(s.id)}')">删除</button>
        </span>
      </div>
      <div class="ext-scopes">${scope}</div>
      <pre class="ext-content">${escapeHtml((s.content || '').slice(0, 300))}${(s.content || '').length > 300 ? '\n…' : ''}</pre>
    </div>`;
  }).join('');
}

function showSkillForm(id) {
  editingSkillId = id || null;
  const s = id ? extSkills.find(x => x.id === id) : null;
  $('#ext-skill-name').value = s ? (s.name || '') : '';
  $('#ext-skill-desc').value = s ? (s.description || '') : '';
  $('#ext-skill-content').value = s ? (s.content || '') : '';
  const scope = (s ? s.scope : ['all']) || ['all'];
  ['all', 'reqdev', 'apidebug', 'codetest'].forEach(k =>
    $(`#ext-skill-scope-${k}`).checked = scope.includes(k));
  $('#ext-skill-enabled').checked = s ? !!s.enabled : true;
  $('#skill-form').classList.remove('hidden');
  $('#ext-skill-name').focus();
}

function hideSkillForm() {
  $('#skill-form').classList.add('hidden');
  editingSkillId = null;
}

function _skillScopeFromForm() {
  const out = [];
  ['all', 'reqdev', 'apidebug', 'codetest'].forEach(k => {
    if ($(`#ext-skill-scope-${k}`).checked) out.push(k);
  });
  return out.length ? out : ['all'];
}

async function saveSkill() {
  const name = $('#ext-skill-name').value.trim();
  const content = $('#ext-skill-content').value.trim();
  if (!name) { toast('请填技能名称', 'err'); return; }
  if (!content) { toast('请填技能内容', 'err'); return; }
  const obj = {
    id: editingSkillId || undefined,
    name, content,
    description: $('#ext-skill-desc').value.trim(),
    scope: _skillScopeFromForm(),
    enabled: $('#ext-skill-enabled').checked,
  };
  const list = extSkills.slice();
  if (editingSkillId) {
    const i = list.findIndex(x => x.id === editingSkillId);
    if (i >= 0) list[i] = { ...list[i], ...obj, id: editingSkillId };
  } else {
    list.push(obj);
  }
  await _saveSkills(list);
  hideSkillForm();
}

async function toggleSkill(id) {
  const list = extSkills.map(s => s.id === id ? { ...s, enabled: !s.enabled } : s);
  await _saveSkills(list);
}

async function deleteSkill(id) {
  if (!confirm('确认删除该技能？')) return;
  await _saveSkills(extSkills.filter(s => s.id !== id));
}

async function _saveSkills(list) {
  try {
    const r = await fetch('/api/extensions/skills', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skills: list }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    extSkills = data.skills || [];
    renderExtSkills();
    toast('技能已保存', 'ok');
  } catch (e) {
    toast(`技能保存失败: ${e}`, 'err');
  }
}

/* ---------- MCP ---------- */
function renderExtMcp() {
  const badge = $('#mcp-count');
  if (badge) badge.textContent = `${extMcp.filter(s => s.enabled).length} 启用 / ${extMcp.length}`;
  const el = $('#mcp-list');
  if (!el) return;
  if (!extMcp.length) {
    el.innerHTML = '<div class="empty">还没有 MCP 服务。添加并启用后，它的工具会出现在 AI 任务的可调用工具列表里。</div>';
    return;
  }
  el.innerHTML = extMcp.map(s => {
    const where = s.transport === 'http' ? (s.url || '')
      : [s.command, s.args].filter(Boolean).join(' ');
    return `<div class="ext-card ${s.enabled ? '' : 'ext-off'}">
      <div class="ext-head">
        <span class="st ${s.enabled ? 'st-applied' : 'st-rejected'}">${s.enabled ? '启用' : '停用'}</span>
        <span class="probe-kw">${s.transport === 'http' ? 'HTTP' : 'stdio'}</span>
        <b>${escapeHtml(s.name || '')}</b>
        <span class="muted ext-where">${escapeHtml(where || '')}</span>
        <span class="ext-actions">
          <button class="btn-mini" onclick="testMcp('${escapeAttr(s.id)}')">测试</button>
          <button class="btn-mini" onclick="toggleMcp('${escapeAttr(s.id)}')">${s.enabled ? '停用' : '启用'}</button>
          <button class="btn-mini" onclick="showMcpForm('${escapeAttr(s.id)}')">编辑</button>
          <button class="btn-mini" onclick="deleteMcp('${escapeAttr(s.id)}')">删除</button>
        </span>
      </div>
      <div class="mcp-test-out" id="mcp-out-${escapeAttr(s.id)}"></div>
    </div>`;
  }).join('');
}

function showMcpForm(id) {
  editingMcpId = id || null;
  const s = id ? extMcp.find(x => x.id === id) : null;
  $('#ext-mcp-name').value = s ? (s.name || '') : '';
  $('#ext-mcp-transport').value = s ? (s.transport || 'stdio') : 'stdio';
  $('#ext-mcp-timeout').value = s ? (s.timeout || 30) : 30;
  $('#ext-mcp-enabled').checked = s ? !!s.enabled : true;
  $('#ext-mcp-command').value = s ? (s.command || '') : '';
  $('#ext-mcp-args').value = s ? (s.args || '') : '';
  $('#ext-mcp-env').value = s ? (s.env || '') : '';
  $('#ext-mcp-cwd').value = s ? (s.cwd || '') : '';
  $('#ext-mcp-url').value = s ? (s.url || '') : '';
  $('#ext-mcp-headers').value = s ? (s.headers || '') : '';
  toggleMcpTransport();
  $('#mcp-test-result').classList.add('hidden');
  $('#mcp-form').classList.remove('hidden');
  $('#ext-mcp-name').focus();
}

function hideMcpForm() {
  $('#mcp-form').classList.add('hidden');
  editingMcpId = null;
}

function toggleMcpTransport() {
  const http = $('#ext-mcp-transport').value === 'http';
  $('#ext-mcp-stdio-fields').classList.toggle('hidden', http);
  $('#ext-mcp-http-fields').classList.toggle('hidden', !http);
}

function _mcpObjFromForm() {
  return {
    id: editingMcpId || undefined,
    name: $('#ext-mcp-name').value.trim(),
    transport: $('#ext-mcp-transport').value,
    timeout: $('#ext-mcp-timeout').value,
    enabled: $('#ext-mcp-enabled').checked,
    command: $('#ext-mcp-command').value.trim(),
    args: $('#ext-mcp-args').value.trim(),
    env: $('#ext-mcp-env').value.trim(),
    cwd: $('#ext-mcp-cwd').value.trim(),
    url: $('#ext-mcp-url').value.trim(),
    headers: $('#ext-mcp-headers').value.trim(),
  };
}

async function testMcp(id) {
  const server = id ? extMcp.find(x => x.id === id) : _mcpObjFromForm();
  const box = id ? $(`#mcp-out-${CSS.escape(id)}`) : $('#mcp-test-result');
  box.classList.remove('hidden');
  box.textContent = '测试中…';
  try {
    const r = await fetch('/api/extensions/mcp/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server }),
    });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    const tools = (data.tools || []).map(t => `· ${t.name}${t.description ? ' — ' + t.description : ''}`).join('\n');
    box.innerHTML = `<div class="log-title">连接成功（${data.seconds}s，${data.tool_count} 个工具）</div>`
      + `<div class="log-html">${escapeHtml(tools || '（该服务没有暴露任何工具）')}</div>`;
    if (!id) toast(`连接成功，发现 ${data.tool_count} 个工具`, 'ok');
  } catch (e) {
    box.innerHTML = `<div class="log-title">连接失败</div><div class="log-html">${escapeHtml(String(e))}</div>`;
    if (!id) toast(`连接失败: ${e}`, 'err');
  }
}

async function saveMcp() {
  const obj = _mcpObjFromForm();
  if (!obj.name) { toast('请填服务名称', 'err'); return; }
  const list = extMcp.slice();
  if (editingMcpId) {
    const i = list.findIndex(x => x.id === editingMcpId);
    if (i >= 0) list[i] = { ...list[i], ...obj, id: editingMcpId };
  } else {
    list.push(obj);
  }
  await _saveMcp(list);
  hideMcpForm();
}

async function toggleMcp(id) {
  const list = extMcp.map(s => s.id === id ? { ...s, enabled: !s.enabled } : s);
  await _saveMcp(list);
}

async function deleteMcp(id) {
  if (!confirm('确认删除该 MCP 服务配置？')) return;
  await _saveMcp(extMcp.filter(s => s.id !== id));
}

async function _saveMcp(list) {
  try {
    const r = await fetch('/api/extensions/mcp', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ servers: list }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    extMcp = data.servers || [];
    renderExtMcp();
    toast('MCP 配置已保存', 'ok');
  } catch (e) {
    toast(`MCP 保存失败: ${e}`, 'err');
  }
}

/* ================= 更新日志（右上角入口） ================= */
/* 数据源是项目根 CHANGELOG.md，经 /api/changelog 解析后按日期分组返回。
   界面只做展示：不做二次归纳，也不缓存到 localStorage 之外的地方——
   保证「看到的就是仓库里写的」。未读提示靠条目 id（内容寻址，重排也不变）。 */
const CH_SEEN_KEY = 'changelog-seen';
let CH_CACHE = null;
let CH_FILTER = 'all';

async function ensureChangelog(force) {
  if (CH_CACHE && !force) return CH_CACHE;
  try {
    const r = await fetch('/api/changelog');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if (!Array.isArray(d.entries)) throw new Error(d.error || '返回格式异常');
    CH_CACHE = d;
  } catch (e) {
    CH_CACHE = { offline: true, reason: String((e && e.message) || e),
                 entries: [], groups: [], tags: [], total: 0 };
  }
  return CH_CACHE;
}

function updateChangelogDot(d) {
  const dot = $('#changelog-dot');
  if (!dot) return;
  const latest = (d && d.latest) || '';
  dot.classList.toggle('hidden', !latest || latest === (localStorage.getItem(CH_SEEN_KEY) || ''));
}

async function initChangelog() {
  updateChangelogDot(await ensureChangelog());
}

async function openChangelog() {
  CH_FILTER = 'all';
  $('#chmodal').classList.remove('hidden');
  $('#ch-body').innerHTML = '<div class="empty">加载中…</div>';
  const d = await ensureChangelog(true);
  renderChangelog(d);
  if (d.latest) localStorage.setItem(CH_SEEN_KEY, d.latest);
  updateChangelogDot(d);
}

function closeChangelog(ev) {
  if (ev && ev.target !== ev.currentTarget) return;
  $('#chmodal').classList.add('hidden');
}

function setChFilter(slug) {
  CH_FILTER = slug;
  renderChangelog(CH_CACHE || {});
}

const CH_WEEK = '日一二三四五六';

function chWeekday(dateStr) {
  const d = new Date(`${dateStr}T00:00:00`);
  return isNaN(d.getTime()) ? '' : `周${CH_WEEK[d.getDay()]}`;
}

// 行内的 `code` 与 **加粗**：日志里写文件/方法名很常见，值得保留样式
function chInline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

// ①②③ 这类序号常被顺手写进同一行，展示时拆开：每个序号自成一行
const CH_CIRCLED_RE = /(?=[①-⓳])/;
const CH_CIRCLED_START_RE = /^[①-⓳]/;

function chBlock(v) {
  const out = [];
  String(v || '').split('\n').forEach(line => {
    if (!line.trim()) return;
    line.split(CH_CIRCLED_RE).map(s => s.trim()).filter(Boolean).forEach(seg => {
      const num = CH_CIRCLED_START_RE.test(seg);          // ① 本身就是项目符号，不再加 •
      const bullet = !num && /^[•·]/.test(seg);
      const text = chInline(seg.replace(/^[•·]\s*/, ''));
      const cls = num ? 'num' : (bullet ? 'bullet' : '');
      out.push(`<div class="ch-para${cls ? ` ${cls}` : ''}">${text}</div>`);
    });
  });
  return out.join('');
}

function chField(label, inner, cls) {
  return `<div class="ch-field${cls ? ` ${cls}` : ''}"><b>${label}</b>`
    + `<div class="ch-val">${inner}</div></div>`;
}

function renderChEntry(e) {
  const rows = [];
  if (e.content) rows.push(chField('内容', chBlock(e.content)));
  if (e.how) rows.push(chField('做法', chBlock(e.how)));
  if ((e.files_list || []).length) {
    rows.push(chField('文件', e.files_list
      .map(f => `<code class="ch-file">${escapeHtml(f)}</code>`).join(''), 'files'));
  }
  if (e.impact) {
    rows.push(chField('影响', chBlock(e.impact)
      + (e.restart ? '<span class="ch-restart">需重启后端</span>' : ''), 'impact'));
  }
  if (e.note) rows.push(chField('备注', chBlock(e.note)));
  if (e._extra) rows.push(chField('补充', chBlock(e._extra)));
  const cls = `ch-item t-${e.tag_slug || 'other'}${e.restart ? ' needs-restart' : ''}`;
  return `<div class="${cls}">
    <div class="ch-item-head">
      <span class="ch-tag t-${e.tag_slug || 'other'}">${escapeHtml(e.tag)}</span>
      ${e.time ? `<span class="ch-time">${escapeHtml(e.time)}</span>` : ''}
      <span class="ch-title">${escapeHtml(e.title)}</span>
    </div>
    <div class="ch-fields">${rows.join('')}</div>
  </div>`;
}

function renderChDay(g) {
  return `<div class="ch-day">
    <div class="ch-day-label">
      <span class="ch-day-date">${escapeHtml(g.date)}</span>
      <span class="ch-day-week">${escapeHtml(chWeekday(g.date))}</span>
      <span class="ch-day-count">${g.entries.length} 条</span>
    </div>
    <div class="ch-items">${g.entries.map(renderChEntry).join('')}</div>
  </div>`;
}

function renderChangelog(d) {
  const body = $('#ch-body'), filters = $('#ch-filters'), foot = $('#ch-foot');
  if (!body) return;
  if (d.offline) {
    if (filters) filters.innerHTML = '';
    if (foot) foot.textContent = '';
    body.innerHTML = `<div class="empty">读不到更新日志：${escapeHtml(d.reason || '未知原因')}
      <div class="ch-hint">若是 404，说明后端还是旧代码——重启 <code>python -m web.server</code> 后刷新页面即可。</div>
    </div>`;
    return;
  }
  const chips = [{ name: '全部', slug: 'all', count: d.total }].concat(d.tags || []);
  if (filters) {
    filters.innerHTML = chips.map(c =>
      `<button class="ch-chip${CH_FILTER === c.slug ? ' active' : ''}" `
      + `onclick="setChFilter('${c.slug}')">${escapeHtml(c.name)}<span>${c.count}</span></button>`
    ).join('');
  }
  const groups = (d.groups || []).map(g => ({
    date: g.date,
    entries: g.entries.filter(e => CH_FILTER === 'all' || e.tag_slug === CH_FILTER),
  })).filter(g => g.entries.length);

  body.innerHTML = groups.length
    ? groups.map(renderChDay).join('')
    : '<div class="empty">该分类下暂无记录</div>';

  const shown = groups.reduce((n, g) => n + g.entries.length, 0);
  if (foot) {
    foot.innerHTML =
      `<span>${CH_FILTER === 'all' ? `共 ${d.total} 条` : `筛选出 ${shown} 条`} · ${groups.length} 天</span>`
      + (d.updated_at ? `<span>日志文件更新于 ${escapeHtml(d.updated_at.replace('T', ' ').slice(0, 16))}</span>` : '')
      + `<span>来源 ${escapeHtml(d.source || 'CHANGELOG.md')}</span>`;
  }
}

/* ================= 统计 · Token 用量 =================
   数据源是后端 append-only 账本（usage/usage.jsonl），每次真实模型调用一行。
   图表全部手绘 SVG：本项目无构建步骤、也要能在内网离线用，所以不引任何图表库。

   两条诚实性要求（对应后端口径）：
   1. 网关回了 usage 是真值，没回（流式最常见）是按字符估算 —— 估算必须打角标，
      不能把估算值画成账单。
   2. mock 调用不入账（不消耗 token），所以这里的"调用次数"是真实调用次数。 */

const USAGE_PALETTE = ['var(--primary)', 'var(--primary-2)', '#14b8a6', '#f59e0b',
  '#0ea5e9', '#f472b6', '#84cc16', '#a855f7'];

let USAGE = null;
let UGRAN = localStorage.getItem('usage-gran') || 'day';
let UDAYS = Number(localStorage.getItem('usage-days')) || 30;
if (!['day', 'week', 'month'].includes(UGRAN)) UGRAN = 'day';
if (![7, 30, 90, 365].includes(UDAYS)) UDAYS = 30;

function usageSyncControls() {
  document.querySelectorAll('#usage-gran .seg-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.g === UGRAN));
  document.querySelectorAll('#usage-range .seg-btn').forEach(b =>
    b.classList.toggle('active', Number(b.dataset.d) === UDAYS));
}

function setUsageGran(g) {
  UGRAN = g;
  localStorage.setItem('usage-gran', g);
  usageSyncControls();
  loadUsage();
}

function setUsageDays(d) {
  UDAYS = d;
  localStorage.setItem('usage-days', String(d));
  usageSyncControls();
  loadUsage();
}

async function loadUsage() {
  usageSyncControls();
  const trend = $('#usage-trend');
  if (trend && !USAGE) trend.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch(`/api/usage/report?days=${UDAYS}&granularity=${UGRAN}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || d.detail || `HTTP ${r.status}`);
    USAGE = d;
  } catch (e) {
    USAGE = null;
    const msg = `<div class="empty">用量统计加载失败：${escapeHtml(String(e))}</div>`;
    ['#usage-cards', '#usage-trend', '#usage-kinds', '#usage-models',
      '#usage-tasks', '#usage-recent'].forEach(sel => {
      const el = $(sel); if (el) el.innerHTML = msg;
    });
    toast(`用量统计加载失败: ${e}`, 'err');
    return;
  }
  usageRenderAll();
}

function usageRenderAll() {
  usageRenderCards();
  usageRenderNote();
  usageRenderTrend();
  usageRenderKinds();
  usageRenderModels();
  usageRenderTasks();
  usageRenderRecent();
  const gen = $('#usage-gen');
  if (gen) gen.textContent = USAGE.generated_at ? `更新于 ${USAGE.generated_at.replace('T', ' ').slice(5, 16)}` : '';
}

async function usageInit() {
  usageSyncControls();
  await loadUsage();
}

/* ---------- 数值格式 ---------- */
function fmtInt(n) {
  return (Number(n) || 0).toLocaleString('en-US');
}

function fmtTok(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
  if (n >= 1e4) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + 'k';
  return fmtInt(n);
}

// 坐标轴刻度取"好看"的整数步长（1/2/2.5/5/10 × 10^n）
function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(1, raw))));
  const c = raw / p;
  const m = c <= 1 ? 1 : c <= 2 ? 2 : c <= 2.5 ? 2.5 : c <= 5 ? 5 : 10;
  return m * p;
}

function usageTotLine(t) {
  if (!t || !t.calls) return '暂无调用';
  const bits = [`${fmtInt(t.calls)} 次调用`];
  if (t.avg) bits.push(`均 ${fmtTok(t.avg)}`);
  if (t.estimated) bits.push(`${t.estimated} 次估算`);
  if (t.failed) bits.push(`${t.failed} 次失败`);
  return bits.join(' · ');
}

/* ---------- 顶部卡片 ---------- */
function usageRenderCards() {
  const box = $('#usage-cards');
  if (!box) return;
  const cards = [
    ['区间内', USAGE.totals, `${USAGE.range.from} ~ ${USAGE.range.to}`],
    ['今天', USAGE.today, '自然日'],
    ['本周', USAGE.this_week, '周一起算'],
    ['本月', USAGE.this_month, '自然月'],
    ['累计', USAGE.all_time, '账本全部记录'],
  ];
  box.innerHTML = cards.map(([label, t, sub], i) => {
    t = t || {};
    return `<div class="usage-card${i === 0 ? ' primary' : ''}">
      <div class="usage-card-label">${escapeHtml(label)}</div>
      <div class="usage-card-value">${fmtInt(t.total || 0)}<span class="unit">tokens</span></div>
      <div class="usage-card-sub">${escapeHtml(usageTotLine(t))}</div>
      <div class="usage-card-foot">
        <span title="输入 token">↑ ${fmtTok(t.input || 0)}</span>
        <span title="输出 token">↓ ${fmtTok(t.output || 0)}</span>
        <span class="muted">${escapeHtml(sub)}</span>
      </div>
    </div>`;
  }).join('');
}

function usageRenderNote() {
  const el = $('#usage-note');
  if (!el) return;
  const t = USAGE.totals || {};
  const bits = [
    '每次<strong>真实</strong>模型调用都会按任务归属记一笔账（任务类型 + 任务标题 + 调用阶段），'
    + '所以「单任务消耗」「每天/每周/每月消耗」都是账本直接聚合出来的，不是抽样估算。',
  ];
  if (t.estimated) {
    bits.push(`区间内有 <strong>${t.estimated}</strong> 次调用网关没有返回用量（流式响应最常见），`
      + '这些数字是按字符折算的，表中标了「估算」角标 —— 请当作量级参考，不要当账单。');
  } else if (t.calls) {
    bits.push('区间内所有调用的用量都来自网关回包（无估算值）。');
  }
  bits.push('Mock 模式的调用不消耗 token，因此不入账 —— 面板上的调用次数就是真实调用次数。'
    + `账本：<code>${escapeHtml(USAGE.note?.ledger || '')}</code>`);
  el.innerHTML = bits.map(b => `<div>${b}</div>`).join('');
}

/* ---------- 趋势（堆叠柱：输入 + 输出） ---------- */
function usageRenderTrend() {
  const box = $('#usage-trend');
  if (!box) return;
  const buckets = USAGE.buckets || [];
  const meta = $('#usage-trend-meta');
  const granText = { day: '每天', week: '每周', month: '每月' }[USAGE.range.granularity] || '每天';
  if (!buckets.some(b => b.total)) {
    box.innerHTML = `<div class="empty">这个区间里还没有真实的模型调用记录。<br>`
      + `切到「缺陷修复」跑一次工单，或在「长任务作业」里跑一次作业，这里就会出现用量曲线。</div>`;
    if (meta) meta.textContent = '';
    return;
  }
  if (meta) {
    meta.textContent = `${granText} · ${buckets.length} 个刻度 · 峰值 ${fmtTok(Math.max(...buckets.map(b => b.total)))}`;
  }

  const W = 1000, H = 250, PL = 54, PR = 14, PT = 14, PB = 34;
  const iw = W - PL - PR, ih = H - PT - PB;
  const peak = Math.max(1, ...buckets.map(b => b.total));
  const stepv = niceStep(peak / 4);
  const top = stepv * 4;
  const yOf = v => PT + ih - (v / top) * ih;
  const n = buckets.length;
  const slot = iw / n;
  const bw = Math.max(1.5, Math.min(30, slot * 0.68));
  const labelEvery = Math.max(1, Math.ceil(n / 12));

  const grid = [];
  for (let i = 0; i <= 4; i++) {
    const v = stepv * i;
    const y = yOf(v);
    grid.push(`<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W - PR}" y2="${y.toFixed(1)}"`
      + ` stroke="var(--border)" stroke-width="1" ${i ? '' : 'stroke-opacity="1"'}></line>`);
    grid.push(`<text x="${PL - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end"`
      + ` class="usage-axis">${fmtTok(v)}</text>`);
  }

  const bars = buckets.map((b, i) => {
    const cx = PL + slot * i + slot / 2;
    const x = cx - bw / 2;
    const hOut = (b.output / top) * ih;
    const hIn = (b.input / top) * ih;
    const yIn = PT + ih - hIn;
    const yOut = yIn - hOut;
    const tip = `${b.date || b.key}　合计 ${fmtInt(b.total)} tokens`
      + `\n输入 ${fmtInt(b.input)} · 输出 ${fmtInt(b.output)}`
      + `\n${b.calls} 次调用` + (b.estimated ? `（${b.estimated} 次估算）` : '')
      + (b.failed ? `（${b.failed} 次失败）` : '');
    const stub = (!hIn && !hOut)
      ? `<rect x="${x.toFixed(1)}" y="${(PT + ih - 2).toFixed(1)}" width="${bw.toFixed(1)}" height="2"`
        + ` rx="1" fill="var(--border-strong)"></rect>`
      : '';
    return `<g class="usage-bar"><title>${escapeHtml(tip)}</title>`
      + (hIn ? `<rect x="${x.toFixed(1)}" y="${yIn.toFixed(1)}" width="${bw.toFixed(1)}"`
        + ` height="${Math.max(1, hIn).toFixed(1)}" rx="2" fill="var(--c-in)"></rect>` : '')
      + (hOut ? `<rect x="${x.toFixed(1)}" y="${yOut.toFixed(1)}" width="${bw.toFixed(1)}"`
        + ` height="${Math.max(1, hOut).toFixed(1)}" rx="2" fill="var(--c-out)"></rect>` : '')
      + stub + '</g>';
  }).join('');

  const xlabels = buckets.map((b, i) => {
    if (i % labelEvery) return '';
    return `<text x="${(PL + slot * i + slot / 2).toFixed(1)}" y="${H - 12}"`
      + ` text-anchor="middle" class="usage-axis">${escapeHtml(b.label)}</text>`;
  }).join('');

  box.innerHTML = `<div class="usage-legend">
      <span><i style="background:var(--c-in)"></i>输入</span>
      <span><i style="background:var(--c-out)"></i>输出</span>
    </div>
    <svg class="usage-svg" viewBox="0 0 ${W} ${H}" role="img"
         aria-label="${escapeAttr(granText)} token 消耗趋势">
      ${grid.join('')}
      <line x1="${PL}" y1="${PT + ih}" x2="${W - PR}" y2="${PT + ih}" stroke="var(--border-strong)" stroke-width="1"></line>
      ${bars}
      ${xlabels}
    </svg>`;
}

/* ---------- 构成：按任务类型 / 按模型 ---------- */
function usageBarRows(rows, labelOf) {
  const tot = rows.reduce((s, r) => s + r.total, 0) || 1;
  const max = Math.max(1, ...rows.map(r => r.total));
  return rows.map((r, i) => {
    const pct = (r.total / tot) * 100;
    const color = USAGE_PALETTE[i % USAGE_PALETTE.length];
    return `<div class="usage-row">
      <div class="usage-row-label" title="${escapeAttr(labelOf(r))}">${escapeHtml(labelOf(r))}</div>
      <div class="usage-row-track">
        <i style="width:${Math.max(1, (r.total / max) * 100).toFixed(1)}%;background:${color}"></i>
      </div>
      <div class="usage-row-val">${fmtInt(r.total)}
        <span class="muted">${pct.toFixed(1)}%</span></div>
      <div class="usage-row-sub">${fmtInt(r.calls)} 次
        ${r.estimated ? `· ${r.estimated} 估算` : ''}${r.failed ? ` · ${r.failed} 失败` : ''}</div>
    </div>`;
  }).join('');
}

function usageRenderKinds() {
  const box = $('#usage-kinds');
  if (!box) return;
  const rows = USAGE.kinds || [];
  const head = `<div class="usage-chart-title">按任务类型</div>`;
  if (!rows.length) {
    box.innerHTML = head + '<div class="empty">暂无数据</div>';
    return;
  }
  box.innerHTML = head + usageBarRows(rows, r => r.label || r.key);
}

function usageRenderModels() {
  const box = $('#usage-models');
  if (!box) return;
  const rows = (USAGE.models || []).slice(0, 8);
  const head = `<div class="usage-chart-title">按模型</div>`;
  const tot = rows.reduce((s, r) => s + r.total, 0);
  if (!rows.length || !tot) {
    box.innerHTML = head + '<div class="empty">暂无数据</div>';
    return;
  }
  box.innerHTML = head + usageDonut(rows, tot)
    + '<div class="usage-donut-legend">' + rows.map((r, i) =>
      `<div><i style="background:${USAGE_PALETTE[i % USAGE_PALETTE.length]}"></i>
        <span class="usage-donut-name" title="${escapeAttr(r.key)}">${escapeHtml(r.key)}</span>
        <span class="muted">${fmtInt(r.total)} · ${((r.total / tot) * 100).toFixed(1)}%</span></div>`
    ).join('') + '</div>';
}

function usageDonut(rows, total) {
  const cx = 108, cy = 104, rO = 78, rI = 52;
  if (rows.length === 1) {
    const w = rO - rI;
    return `<svg class="usage-svg usage-donut" viewBox="0 0 216 208" role="img" aria-label="模型占比">
      <circle cx="${cx}" cy="${cy}" r="${(rO + rI) / 2}" fill="none"
        stroke="var(--primary)" stroke-width="${w}"></circle>
      <text x="${cx}" y="${cy - 2}" text-anchor="middle" class="usage-donut-total">${fmtTok(total)}</text>
      <text x="${cx}" y="${cy + 16}" text-anchor="middle" class="usage-donut-cap">tokens</text>
    </svg>`;
  }
  let a = -Math.PI / 2;
  const arcs = rows.map((r, i) => {
    const sweep = (r.total / total) * Math.PI * 2;
    const a0 = a, a1 = a + sweep;
    a = a1;
    const large = sweep > Math.PI ? 1 : 0;
    const p = (rad, ang) => [(cx + rad * Math.cos(ang)).toFixed(2), (cy + rad * Math.sin(ang)).toFixed(2)];
    const [x0, y0] = p(rO, a0), [x1, y1] = p(rO, a1);
    const [x2, y2] = p(rI, a1), [x3, y3] = p(rI, a0);
    const d = `M${x0} ${y0} A${rO} ${rO} 0 ${large} 1 ${x1} ${y1}`
      + ` L${x2} ${y2} A${rI} ${rI} 0 ${large} 0 ${x3} ${y3} Z`;
    return `<path d="${d}" fill="${USAGE_PALETTE[i % USAGE_PALETTE.length]}">`
      + `<title>${escapeHtml(r.key)}：${fmtInt(r.total)} tokens（${((r.total / total) * 100).toFixed(1)}%）</title></path>`;
  }).join('');
  return `<svg class="usage-svg usage-donut" viewBox="0 0 216 208" role="img" aria-label="模型占比">
    ${arcs}
    <text x="${cx}" y="${cy - 2}" text-anchor="middle" class="usage-donut-total">${fmtTok(total)}</text>
    <text x="${cx}" y="${cy + 16}" text-anchor="middle" class="usage-donut-cap">tokens</text>
  </svg>`;
}

/* ---------- 单任务排行 ---------- */
function usageRenderTasks() {
  const box = $('#usage-tasks');
  if (!box) return;
  const rows = USAGE.tasks || [];
  const meta = $('#usage-task-meta');
  if (meta) meta.textContent = rows.length ? `${rows.length} 个任务（按区间内合计降序）` : '';
  if (!rows.length) {
    box.innerHTML = '<div class="empty">区间内还没有任务级记录。</div>';
    return;
  }
  const max = Math.max(1, ...rows.map(r => r.total));
  box.innerHTML = `<table class="usage-table">
    <thead><tr><th>#</th><th>类型</th><th>任务</th><th class="num">调用</th>
      <th class="num">输入</th><th class="num">输出</th><th class="num">合计</th>
      <th class="num">占比</th><th>最后</th></tr></thead>
    <tbody>${rows.map((r, i) => `<tr>
      <td class="rk">${i + 1}</td>
      <td><span class="usage-kind-chip">${escapeHtml(r.label || r.kind || '—')}</span></td>
      <td class="tname" title="${escapeAttr((r.title || '') + '　' + (r.key || ''))}">
        ${escapeHtml(r.title || '(无标题)')}
        <div class="usage-rel"><i style="width:${((r.total / max) * 100).toFixed(1)}%"></i></div>
      </td>
      <td class="num">${fmtInt(r.calls)}${r.failed ? `<span class="usage-badge bad">${r.failed} 失败</span>` : ''}</td>
      <td class="num">${fmtInt(r.input)}</td>
      <td class="num">${fmtInt(r.output)}</td>
      <td class="num strong">${fmtInt(r.total)}</td>
      <td class="num">${(USAGE.totals.total ? (r.total / USAGE.totals.total) * 100 : 0).toFixed(1)}%</td>
      <td class="muted">${escapeHtml(relTime(r.last || ''))}</td>
    </tr>`).join('')}</tbody></table>`;
}

/* ---------- 最近调用 ---------- */
function usageRenderRecent() {
  const box = $('#usage-recent');
  if (!box) return;
  const rows = USAGE.recent || [];
  const meta = $('#usage-recent-meta');
  const est = rows.filter(r => r.estimated).length;
  if (meta) {
    meta.textContent = rows.length
      ? `最近 ${rows.length} 次${est ? ` · 其中 ${est} 次为估算` : ''}` : '';
  }
  if (!rows.length) {
    box.innerHTML = '<div class="empty">区间内还没有调用记录。</div>';
    return;
  }
  const label = USAGE.kind_labels || {};
  box.innerHTML = `<table class="usage-table">
    <thead><tr><th>时间</th><th>类型 / 阶段</th><th>模型</th>
      <th class="num">输入</th><th class="num">输出</th><th class="num">合计</th>
      <th class="num">耗时</th><th>状态</th></tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td class="muted">${escapeHtml(String(r.ts || '').replace('T', ' ').slice(5, 19))}</td>
      <td><span class="usage-kind-chip">${escapeHtml(label[r.kind] || r.kind || '—')}</span>
        ${r.step ? `<span class="muted">${escapeHtml(r.step)}</span>` : ''}
        ${r.agent ? `<span class="muted">· ${escapeHtml(r.agent)}</span>` : ''}</td>
      <td class="muted">${escapeHtml(r.model || '—')}${r.mode ? `<span class="muted"> / ${escapeHtml(r.mode)}</span>` : ''}</td>
      <td class="num">${fmtInt(r.input_tokens)}</td>
      <td class="num">${fmtInt(r.output_tokens)}</td>
      <td class="num strong">${fmtInt(r.total_tokens)}</td>
      <td class="num muted">${r.seconds ? r.seconds.toFixed(1) + 's' : '—'}</td>
      <td>${r.ok === false
        ? `<span class="usage-badge bad" title="${escapeAttr(r.error || '')}">失败</span>`
        : '<span class="usage-badge ok">成功</span>'}
        ${r.estimated ? '<span class="usage-badge est" title="网关未返回 usage，按字符估算">估算</span>' : ''}</td>
    </tr>`).join('')}</tbody></table>`;
}

/* ================= 长任务作业（多子 Agent 协作） =================
   把重构 / 升级迁移这类大任务拆给四个子 Agent：决策官拆解、编码逐项实现、
   测试补用例、复核收口。这里的重点是「过程可见 + 随时能插话」：看板实时显示
   每个角色在做什么，人工介入按角色投递。 */

// 后端 /api/team/roles 不可用时的兜底（只影响看板渲染，不影响真实运行）
const TEAM_ROLE_FALLBACK = [
  { id: 'planner', name: '决策官', emoji: '🧭', color: '#6366f1', stage: '拆解',
    duty: '把长任务拆成可独立验收的工作项，定方案、定顺序、定验收标准，并在中途纠偏重规划。' },
  { id: 'coder', name: '编码工程师', emoji: '⌨️', color: '#0ea5e9', stage: '实现',
    duty: '按方案逐个工作项写代码，输出可直接落盘的完整文件内容。' },
  { id: 'tester', name: '测试工程师', emoji: '🧪', color: '#14b8a6', stage: '验证',
    duty: '为每个工作项的产出写测试、指出未覆盖的边界与回归风险。' },
  { id: 'reviewer', name: '复核官', emoji: '🔍', color: '#f59e0b', stage: '收口',
    duty: '通盘审查一致性、遗漏与风险，给出结论与必须修的清单。' },
];
const TEAM_AGENT_STATUS = {
  idle: '待命', working: '执行中', done: '已完成', error: '出错', waiting: '等待人工',
};
const TEAM_RUN_STATUS = {
  planning: '规划中', running: '运行中', paused: '已暂停',
  done: '已完成', failed: '失败', aborted: '已终止',
};
const TEAM_ITEM_STATUS = {
  todo: '待做', doing: '进行中', done: '已完成', failed: '失败', skipped: '已跳过',
};
const TEAM_VERDICT_TEXT = { pass: '通过', pass_with_risks: '通过（有风险）', block: '需返工' };
const TEAM_INTERVENE_TEXT = {
  message: '人工指令', replan: '要求重规划', skip: '跳过工作项',
  pause: '暂停', resume: '继续', stop: '终止',
};

let TEAM_ROLES = [];
let TEAM_RUNS = [];
let TEAM = null;

function teamNewState() {
  return {
    runId: '', running: false, paused: false, replaying: false,
    agents: [], plan: { items: [] }, review: null, events: [], interventions: [],
    stream: {}, filter: 'all', artifact: '', title: '',
  };
}

function teamRole(id) {
  return TEAM_ROLES.find(r => r.id === id) || { id, name: id, emoji: '' };
}

/* ---------- 看板 ---------- */
function teamRenderAgents() {
  const box = $('#team-agents');
  if (!box) return;
  const agents = (TEAM && TEAM.agents) || [];
  if (!agents.length) {
    box.innerHTML = '<div class="empty">还没有作业。点「开始作业」后，每个子 Agent 会占一张卡片，'
      + '实时显示它当前在做什么、计划进度和它产出的文件。</div>';
    return;
  }
  box.innerHTML = agents.map(teamAgentCard).join('');
  // 重渲染会清空卡片里的 <pre>，逐字输出从缓冲区回填
  agents.forEach(a => {
    const st = (TEAM.stream || {})[a.id];
    if (!st || (!st.think && !st.out)) return;
    const card = teamAgentEl(a.id);
    if (!card) return;
    [['think', st.think], ['out', st.out]].forEach(([k, txt]) => {
      if (!txt) return;
      const block = card.querySelector(`.ai-block.${k}`);
      if (!block) return;
      block.classList.remove('hidden');
      block.querySelector('pre').textContent = txt;
    });
    card.querySelector('.team-live')?.setAttribute('open', '');
    teamScrollCard(card);
  });
}

function teamAgentEl(id) {
  const box = $('#team-agents');
  if (!box) return null;
  return Array.from(box.querySelectorAll('.team-agent'))
    .find(el => el.dataset.agent === id) || null;
}

function teamScrollCard(card) {
  card.querySelectorAll('.ai-block pre').forEach(pre => {
    pre.scrollTop = pre.scrollHeight;
  });
}

function teamAgentCard(a) {
  const prog = a.progress || {};
  const pct = prog.total ? Math.round((prog.done / prog.total) * 100) : 0;
  const files = (a.files || []);
  const shown = files.slice(0, 5);
  const note = (a.notes || []).slice(-1)[0] || '';
  return `<div class="team-agent ${escapeAttr(a.status || 'idle')}" data-agent="${escapeAttr(a.id)}"
      style="--agent-color:${escapeAttr(a.color || '#6366f1')}">
    <div class="team-agent-head">
      <span class="team-dot"></span>
      <span class="team-agent-emoji">${escapeHtml(a.emoji || '')}</span>
      <span class="team-agent-name">${escapeHtml(a.name || a.id)}</span>
      <span class="team-agent-stage">${escapeHtml(a.stage || '')} · ${escapeHtml(TEAM_AGENT_STATUS[a.status] || a.status || '')}</span>
    </div>
    <div class="team-agent-duty">${escapeHtml(a.duty || '')}</div>
    <div class="team-agent-now">${escapeHtml(a.current || '待命中')}</div>
    ${prog.total ? `<div class="team-progress" title="${prog.done}/${prog.total} 个工作项"><i style="width:${pct}%"></i></div>` : ''}
    ${note ? `<div class="team-agent-note">${escapeHtml(note)}</div>` : ''}
    ${shown.length ? `<div class="team-agent-files">${shown.map(f =>
        `<span class="team-file-chip" title="${escapeAttr(f)}">${escapeHtml(shortPath(f))}</span>`).join('')
      }${files.length > shown.length ? `<span class="team-file-chip">+${files.length - shown.length}</span>` : ''}</div>` : ''}
    <details class="team-live">
      <summary>实时思考 / 输出</summary>
      <div class="ai-stream">
        <div class="ai-block think hidden"><div class="ai-block-label">思考</div><pre></pre></div>
        <div class="ai-block out hidden"><div class="ai-block-label">输出</div><pre></pre></div>
      </div>
    </details>
  </div>`;
}

function teamUpsertAgent(evt) {
  const ag = (TEAM.agents || []).find(x => x.id === evt.agent);
  if (!ag) return;
  if (evt.status) ag.status = evt.status;
  if (evt.current !== undefined) ag.current = evt.current;
  if (evt.progress && (evt.progress.total || !ag.progress.total)) ag.progress = evt.progress;
  if (evt.note) ag.notes = (ag.notes || []).slice(-4).concat([evt.note]);
  if (Array.isArray(evt.files)) ag.files = evt.files.slice();
  teamRenderAgents();
}

function teamStreamDelta(id, kind, text) {
  if (!text || !TEAM) return;
  const st = TEAM.stream[id] || (TEAM.stream[id] = { think: '', out: '' });
  const key = kind === 'reasoning' ? 'think' : 'out';
  st[key] += text;
  const card = teamAgentEl(id);
  if (!card) return;
  const block = card.querySelector(`.ai-block.${key}`);
  if (!block) return;
  block.classList.remove('hidden');
  card.querySelector('.team-live')?.setAttribute('open', '');
  const pre = block.querySelector('pre');
  // 贴底才跟随：用户上翻阅读历史输出时不打扰
  const near = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.textContent += text;
  if (near) pre.scrollTop = pre.scrollHeight;
}

function teamLiveBadge(on) {
  const el = $('#team-live-badge');
  if (el) el.classList.toggle('hidden', !on);
}

function setTeamRunMeta(text) {
  const el = $('#team-run-meta');
  if (el) el.textContent = text || '';
}

/* ---------- 工作项 / 复核 ---------- */
function teamRenderPlan() {
  const box = $('#team-plan');
  if (!box) return;
  const items = (TEAM && TEAM.plan && TEAM.plan.items) || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty">还没有工作项。决策官拆解后会在这里逐项列出，'
      + '并显示每一项的归属、状态与涉及文件。</div>';
    return;
  }
  box.innerHTML = items.map(it => `
    <div class="team-item ${escapeAttr(it.status || 'todo')}">
      <div class="team-item-head">
        <span class="team-item-id">${escapeHtml(it.id || '')}</span>
        <span class="team-item-title">${escapeHtml(it.title || '')}</span>
        <span class="team-item-st">${escapeHtml(TEAM_ITEM_STATUS[it.status] || it.status || '')}</span>
      </div>
      ${it.detail ? `<div class="team-item-detail">${escapeHtml(it.detail)}</div>` : ''}
      ${(it.files || []).length ? `<div class="team-item-files">${it.files.map(f =>
        `<span class="team-file-chip">${escapeHtml(shortPath(f))}</span>`).join('')}</div>` : ''}
      ${it.acceptance ? `<div class="team-item-acc">验收：${escapeHtml(it.acceptance)}</div>` : ''}
      ${it.note ? `<div class="team-item-note">${escapeHtml(it.note)}</div>` : ''}
    </div>`).join('');
}

function teamRenderPlanSummary() {
  const el = $('#team-plan-summary');
  if (!el) return;
  const items = (TEAM && TEAM.plan && TEAM.plan.items) || [];
  if (!items.length) { el.textContent = ''; return; }
  const done = items.filter(i => i.status === 'done').length;
  el.textContent = `${done}/${items.length} 项完成`;
}

function teamRenderReview(rv) {
  const box = $('#team-review');
  if (!box) return;
  if (!rv || !rv.verdict) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  const list = (arr, cls) => arr.length
    ? `<ul class="team-list ${cls || ''}">${arr.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '';
  box.classList.remove('hidden');
  box.innerHTML = `<div class="team-review-head">
      <span class="team-verdict ${escapeAttr(rv.verdict)}">复核 · ${escapeHtml(TEAM_VERDICT_TEXT[rv.verdict] || rv.verdict)}</span>
      ${rv.round ? `<span class="muted">第 ${rv.round} 轮返工后</span>` : ''}
    </div>
    ${rv.summary ? `<div class="team-review-sum">${escapeHtml(rv.summary)}</div>` : ''}
    ${(rv.risks || []).length ? `<div class="team-sec-t">风险</div>${list(rv.risks)}` : ''}
    ${(rv.must_fix || []).length ? `<div class="team-sec-t">必须修</div>
      <ul class="team-list bad">${rv.must_fix.map(m => `<li>${escapeHtml(m.item || '')}
        ${escapeHtml(m.why || '')}${m.how ? ` → ${escapeHtml(m.how)}` : ''}</li>`).join('')}</ul>` : ''}
    ${(rv.suggestions || []).length ? `<div class="team-sec-t">建议</div>${list(rv.suggestions)}` : ''}`;
}

/* ---------- 人工介入 ---------- */
function teamRenderInterventions() {
  const box = $('#team-intervene-list');
  if (!box) return;
  const list = (TEAM && TEAM.interventions) || [];
  if (!list.length) {
    box.innerHTML = '<div class="empty">还没有人工指令。跑的过程中发现某个子 Agent 方向不对，'
      + '随时在这里纠正它。</div>';
    return;
  }
  box.innerHTML = list.slice().reverse().map(m => {
    const acked = !!m.consumed_by;
    const target = m.agent === '*' ? '全部角色'
      : `${teamRole(m.agent).emoji} ${teamRole(m.agent).name}`;
    const by = acked ? teamRole(m.consumed_by).name || m.consumed_by : '';
    return `<div class="team-msg ${acked ? 'ack' : 'pending'}">
      <div class="team-msg-head">
        <span>${escapeHtml(String(m.ts || '').replace('T', ' ').slice(5, 19))} · ${escapeHtml(TEAM_INTERVENE_TEXT[m.kind] || m.kind)} → ${escapeHtml(target)}</span>
        <span class="team-msg-state">${acked ? `已由 ${escapeHtml(by)} 收到` : '待生效（当前步骤结束后）'}</span>
      </div>
      ${m.text ? `<div class="team-msg-text">${escapeHtml(m.text)}</div>` : ''}
    </div>`;
  }).join('');
}

async function teamIntervene(kind, opts = {}) {
  if (!TEAM || !TEAM.runId) { toast('还没有运行中的作业', 'warn'); return; }
  if (!TEAM.running && !TEAM.replaying) { toast('作业已结束，指令送不进去', 'warn'); return; }
  if (!TEAM.running) { toast('这是历史记录的回放，指令送不进去', 'warn'); return; }
  const body = {
    kind, agent: opts.agent || '*', text: opts.text || '', item: opts.item || '',
  };
  try {
    const r = await fetch(`/api/team/runs/${encodeURIComponent(TEAM.runId)}/intervene`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    if (kind === 'pause') TEAM.paused = true;
    if (kind === 'resume') TEAM.paused = false;
    setTeamControls();
    toast(data.detail || '指令已送达', kind === 'pause' || kind === 'stop' ? 'warn' : 'ok');
  } catch (e) {
    toast(`指令未送达: ${e}`, 'err');
  }
}

async function teamSendMessage() {
  const input = $('#team-intervene-text');
  const text = (input.value || '').trim();
  if (!text) { toast('请先输入要传达的内容', 'err'); return; }
  const agent = $('#team-intervene-agent').value || '*';
  await teamIntervene('message', { agent, text });
  input.value = '';
}

async function teamAskReplan() {
  const input = $('#team-intervene-text');
  const text = (input.value || '').trim();
  await teamIntervene('replan', { agent: 'planner', text });
  if (text) input.value = '';
}

function setTeamControls() {
  const running = !!(TEAM && TEAM.running);
  const paused = !!(TEAM && TEAM.paused);
  const set = (sel, on) => { const el = $(sel); if (el) el.disabled = !on; };
  set('#btn-team-run', !running);
  set('#btn-team-pause', running && !paused);
  set('#btn-team-resume', running && paused);
  set('#btn-team-replan', running);
  set('#btn-team-skip', running);
  set('#btn-team-stop', running);
  set('#btn-team-send', running);
  const hint = $('#team-intervene-hint');
  if (hint) {
    hint.textContent = running
      ? (paused ? '已暂停 · 等你的指令' : '运行中 · 可随时介入')
      : (TEAM && TEAM.replaying ? '历史回放 · 只读' : '作业未运行');
  }
}

/* ---------- 时间线 ---------- */
function teamLogSetup() {
  const log = $('#team-log');
  if (!log) return;
  log.innerHTML = '<div class="team-lines"></div>';
  log.classList.remove('hidden');
  const btn = $('#btn-team-expand');
  if (btn) btn.classList.remove('hidden');
  if (TEAM) TEAM.filter = TEAM.filter || 'all';
}

function teamLogLine(text, cls = 'dim', agent = '*') {
  const log = $('#team-log');
  if (!log) return;
  let lines = log.querySelector('.team-lines');
  if (!lines) { teamLogSetup(); lines = log.querySelector('.team-lines'); }
  const div = document.createElement('div');
  div.className = `live-line ${cls}`;
  div.dataset.agent = agent || '*';
  const t = document.createElement('span');
  t.className = 't';
  t.textContent = _nowHMS();
  const b = document.createElement('span');
  b.textContent = text;
  div.appendChild(t);
  div.appendChild(b);
  if (TEAM && TEAM.filter !== 'all' && div.dataset.agent !== TEAM.filter) {
    div.classList.add('hidden');
  }
  lines.appendChild(div);
  const near = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
  if (near) log.scrollTop = log.scrollHeight;
}

function teamSetFilter(v) {
  if (TEAM) TEAM.filter = v || 'all';
  document.querySelectorAll('#team-log .live-line').forEach(el => {
    const ag = el.dataset.agent || '*';
    el.classList.toggle('hidden', (TEAM ? TEAM.filter : 'all') !== 'all' && ag !== TEAM.filter);
  });
}

function teamLogEvent(evt) {
  const who = evt.agent ? teamRole(evt.agent) : null;
  const tag = who ? `${who.emoji} ${who.name}` : '';
  const ag = evt.agent || '*';
  const t = evt.type;
  if (t === 'run_start') {
    const run = evt.run || {};
    teamLogLine(`▶ 作业开始：${run.title || ''}（${run.id || ''}）`, 'head');
    (run.agents || []).forEach(a => teamLogLine(`  参与角色：${a.emoji || ''} ${a.name} —— ${a.duty || ''}`, 'dim'));
    return;
  }
  if (t === 'log') { teamLogLine(evt.text || '', evt.level === 'bad' ? 'bad' : evt.level === 'warn' ? 'warn' : evt.level === 'human' ? 'head' : 'dim'); return; }
  if (t === 'agent_status') {
    const st = TEAM_AGENT_STATUS[evt.status] || evt.status;
    teamLogLine(`${tag} ${st}：${evt.current || ''}`, evt.status === 'error' ? 'bad' : evt.status === 'done' ? 'ok' : 'dim', ag);
    return;
  }
  if (t === 'plan') {
    const items = (evt.plan || {}).items || [];
    teamLogLine(`🧭 决策官${evt.revised ? '重新' : ''}拆出 ${items.length} 个工作项`
      + (evt.reason ? `（${evt.reason}）` : ''), 'head', 'planner');
    items.forEach(it => teamLogLine(`  ${it.id} ${it.title}${(it.files || []).length ? `（${it.files.length} 个文件）` : ''}`, 'dim', 'planner'));
    return;
  }
  if (t === 'item_status') {
    const cls = evt.status === 'failed' ? 'bad' : evt.status === 'done' ? 'ok'
      : evt.status === 'skipped' ? 'warn' : 'dim';
    teamLogLine(`工作项 ${evt.item} ${TEAM_ITEM_STATUS[evt.status] || evt.status}${evt.note ? `：${evt.note}` : ''}`,
      cls, evt.status === 'doing' || evt.status === 'done' ? 'coder' : ag);
    return;
  }
  if (t === 'item_files') {
    const files = (evt.files || []).map(f => f.path);
    const cases = (evt.cases || []).length;
    teamLogLine(`${tag} 产出 ${files.length} 个文件${files.length ? '：' + files.join('、') : ''}`
      + (cases ? `；涉及 ${cases} 个用例` : '')
      + ((evt.gaps || []).length ? `；存疑 ${evt.gaps.length} 处` : ''), 'ok', ag);
    return;
  }
  if (t === 'review') {
    teamLogLine(`🔍 复核结论：${TEAM_VERDICT_TEXT[evt.verdict] || evt.verdict}`
      + (evt.summary ? ` —— ${evt.summary}` : ''), evt.verdict === 'block' ? 'bad' : 'ok', 'reviewer');
    (evt.must_fix || []).forEach(m => teamLogLine(`  必须修：${m.item || ''} ${m.why || ''}`, 'warn', 'reviewer'));
    return;
  }
  if (t === 'artifact') {
    teamLogLine(`📦 已生成产出物 ${evt.id}（${evt.file_count} 个文件）——去「产出物」里审 diff 后采纳`, 'head');
    return;
  }
  if (t === 'intervention') {
    const target = evt.agent === '*' ? '全部角色' : `${teamRole(evt.agent).name}`;
    teamLogLine(`✍ 人工 → ${target}：${evt.text || TEAM_INTERVENE_TEXT[evt.kind] || evt.kind}`, 'head', evt.agent);
    return;
  }
  if (t === 'intervention_ack') {
    teamLogLine(`✔ ${tag} 已收到该指令，将在下一步生效`, 'ok', ag);
    return;
  }
  if (t === 'run_status') {
    teamLogLine(evt.detail || `状态 → ${TEAM_RUN_STATUS[evt.status] || evt.status}`,
      evt.status === 'paused' ? 'warn' : 'ok');
    return;
  }
  if (t === 'run_finish') {
    teamLogLine(`■ 作业结束：${TEAM_RUN_STATUS[evt.status] || evt.status}`
      + (evt.summary ? ` —— ${evt.summary}` : ''), evt.status === 'done' ? 'ok' : 'warn');
    return;
  }
  if (t === 'fatal') { teamLogLine(`✗ ${evt.error || '作业异常'}`, 'bad'); return; }
  if (t === 'done') { teamLogLine(`■ 产出 ${evt.payload?.file_count ?? 0} 个文件（${TEAM_RUN_STATUS[evt.payload?.status] || evt.payload?.status || ''}）`, 'ok'); }
}

function openTeamLog() {
  const log = $('#team-log');
  const body = $('#runmodal-body');
  if (!log || !body || !log.innerHTML.trim()) return;
  $('#runmodal-title').textContent = '长任务作业 · 实时时间线';
  body.innerHTML = log.innerHTML;
  body.scrollTop = 0;
  $('#runmodal').classList.remove('hidden');
}

/* ---------- 事件分发 ---------- */
function teamOnEvent(evt) {
  if (!TEAM) TEAM = teamNewState();
  const t = evt.type;
  if (t === 'run_id') {
    TEAM.runId = evt.run_id || '';
    setTeamRunMeta(`运行中 · ${TEAM.runId}`);
    teamLiveBadge(true);
    setTeamControls();
    return;
  }
  if (t === 'agent_delta') { teamStreamDelta(evt.agent, evt.kind, evt.text); return; }
  if (t === 'fatal') throw new Error(evt.error || '作业异常');

  TEAM.events.push(evt);
  if (t === 'run_start') {
    const run = evt.run || {};
    TEAM.runId = run.id || TEAM.runId;
    TEAM.title = run.title || '';
    if (Array.isArray(run.agents) && run.agents.length) TEAM.agents = run.agents;
    teamRenderAgents();
    teamLiveBadge(true);
  } else if (t === 'agent_status') {
    teamUpsertAgent(evt);
  } else if (t === 'plan') {
    TEAM.plan = evt.plan || { items: [] };
    teamRenderPlan();
    teamRenderPlanSummary();
    teamSyncFilterOptions();
  } else if (t === 'item_status') {
    const it = (TEAM.plan.items || []).find(x => x.id === evt.item);
    if (it) { it.status = evt.status; if (evt.note) it.note = evt.note; }
    teamRenderPlan();
    teamRenderPlanSummary();
  } else if (t === 'item_files') {
    const ag = (TEAM.agents || []).find(x => x.id === evt.agent);
    if (ag) {
      const have = new Set(ag.files || []);
      (evt.files || []).forEach(f => { if (f && f.path) have.add(f.path); });
      ag.files = Array.from(have);
      if (t === 'item_files' && ag.status !== 'error') ag.status = 'working';
      teamRenderAgents();
    }
  } else if (t === 'review') {
    TEAM.review = evt;
    teamRenderReview(evt);
  } else if (t === 'artifact') {
    TEAM.artifact = evt.id || '';
  } else if (t === 'intervention') {
    TEAM.interventions.push(evt);
    if (evt.kind === 'pause') TEAM.paused = true;
    if (evt.kind === 'resume') TEAM.paused = false;
    teamRenderInterventions();
    setTeamControls();
  } else if (t === 'intervention_ack') {
    const m = TEAM.interventions.find(x => x.seq === evt.seq);
    if (m) m.consumed_by = evt.agent;
    teamRenderInterventions();
  } else if (t === 'run_status') {
    TEAM.paused = evt.status === 'paused';
    setTeamControls();
  }
  teamLogEvent(evt);
}

function teamSyncFilterOptions() {
  const sel = $('#team-filter');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="all">全部</option>'
    + (TEAM.agents || []).map(a =>
      `<option value="${escapeAttr(a.id)}">${escapeHtml((a.emoji ? a.emoji + ' ' : '') + a.name)}</option>`).join('');
  sel.value = cur || 'all';
}

/* ---------- 启动作业 ---------- */
async function runTeam() {
  if (TEAM && TEAM.running) { toast('已有作业在跑，等它结束或先终止', 'warn'); return; }
  const task = $('#team-task').value.trim();
  if (!task) { toast('请先填写任务描述', 'err'); return; }
  const roles = ['planner', 'coder', 'tester', 'reviewer']
    .filter(r => { const el = $(`#team-role-${r}`); return el && el.checked; });

  TEAM = teamNewState();
  TEAM.running = true;
  TEAM.title = $('#team-title').value.trim() || task.slice(0, 40);
  TEAM.agents = TEAM_ROLES.map(r => ({
    id: r.id, name: r.name, emoji: r.emoji, color: r.color, stage: r.stage, duty: r.duty,
    status: 'idle', current: '待命中', progress: { done: 0, total: 0 }, files: [], notes: [],
  }));
  teamRenderAgents();
  teamLogSetup();
  teamRenderPlan();
  teamRenderPlanSummary();
  teamSyncFilterOptions();
  teamRenderReview(null);
  teamRenderInterventions();
  setTeamRunMeta('启动中…');
  teamLiveBadge(true);
  setTeamControls();
  $('#status').className = 'status-pill running';
  $('#status').textContent = '长任务作业运行中…';

  const body = {
    title: $('#team-title').value.trim(),
    task,
    scope: $('#team-scope').value.trim(),
    framework: $('#team-framework').value,
    notes: $('#team-notes').value.trim(),
    roles,
    max_rounds: Number($('#team-rounds').value) || 0,
  };
  try {
    const doneEvt = await sseConsume('/api/team/stream', body, teamOnEvent);
    if (!doneEvt) throw new Error('连接中断，未收到作业结果（作业可能还在服务端跑，稍后在历史里看）');
    const p = doneEvt.payload || {};
    TEAM.running = false;
    TEAM.paused = false;
    TEAM.artifact = p.artifact || '';
    teamLogLine(`作业${TEAM_RUN_STATUS[p.status] || p.status}：共 ${p.file_count || 0} 个文件、`
      + `${p.item_total || 0} 个工作项`, p.artifact ? 'ok' : 'warn');
    $('#status').className = 'status-pill ' + (p.artifact ? 'ok' : 'error');
    $('#status').textContent = `作业${TEAM_RUN_STATUS[p.status] || p.status} · 产出 ${p.file_count || 0} 个文件`;
    toast(p.artifact ? '作业完成，产出物已生成，去下方审阅后采纳'
      : `作业${TEAM_RUN_STATUS[p.status] || p.status}，但没有产出任何文件`, p.artifact ? 'ok' : 'warn');
  } catch (e) {
    TEAM.running = false;
    teamLogLine(`✗ ${String((e && e.message) || e)}`, 'bad');
    $('#status').className = 'status-pill error';
    $('#status').textContent = '长任务作业失败';
    toast(`作业失败: ${e}`, 'err');
  } finally {
    TEAM.running = false;
    TEAM.paused = false;
    teamLiveBadge(false);
    setTeamControls();
    setTeamRunMeta(TEAM.runId ? `已结束 · ${TEAM.runId}` : '');
    await Promise.all([loadTeamRuns(), loadArtifacts(), loadHealth()]);
  }
}

/* ---------- 历史作业 ---------- */
async function loadTeamRuns() {
  const el = $('#team-history');
  if (!el) return;
  try {
    const r = await fetch('/api/team/runs');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    TEAM_RUNS = await r.json();
    teamRenderHistory();
  } catch (e) {
    el.innerHTML = `<div class="empty">历史作业加载失败: ${escapeHtml(String(e))}</div>`;
  }
}

function teamRenderHistory() {
  const el = $('#team-history');
  if (!el) return;
  const badge = $('#team-history-count');
  if (badge) {
    badge.textContent = String(TEAM_RUNS.length);
    badge.classList.toggle('hidden', !TEAM_RUNS.length);
  }
  if (!TEAM_RUNS.length) {
    el.innerHTML = '<div class="empty">还没有作业记录。跑一次之后，计划、每个角色的动作轨迹与人工介入都会留在这里。</div>';
    return;
  }
  el.innerHTML = TEAM_RUNS.map(teamRunCard).join('');
}

function teamRunCard(x) {
  const cls = x.status === 'done' ? 'applied'
    : (x.status === 'failed' ? 'apply_failed' : 'pending');
  const prog = x.item_total ? `${x.item_done}/${x.item_total} 项` : '—';
  return `<div class="pcard pc-${escapeAttr(cls)}" onclick="openTeamRun('${escapeAttr(x.id)}')">
    <div class="pcard-top">
      <span class="st st-${escapeAttr(cls)}">${escapeHtml(TEAM_RUN_STATUS[x.status] || x.status || '—')}</span>
      ${x.running ? '<span class="team-running-chip">运行中</span>' : ''}
      <span class="pcard-id">${escapeHtml(x.id || '')}</span>
      <span class="pcard-time">${escapeHtml(relTime(x.updated || x.created))}</span>
    </div>
    <div class="pcard-title">${escapeHtml(x.title || '(无标题)')}</div>
    <div class="pcard-meta">
      <span>工作项 ${escapeHtml(prog)}</span>
      <span>${x.file_count || 0} 个文件</span>
      <span>AI ${escapeHtml(x.ai_mode || '—')}</span>
      ${x.interventions ? `<span>人工介入 ${x.interventions} 次</span>` : ''}
    </div>
    ${x.error ? `<div class="pcard-root" style="color:var(--danger)">${escapeHtml(x.error)}</div>`
      : x.summary ? `<div class="pcard-root">${escapeHtml(x.summary)}</div>` : ''}
  </div>`;
}

async function openTeamRun(id) {
  try {
    const r = await fetch(`/api/team/runs/${encodeURIComponent(id)}`);
    const run = await r.json();
    if (!r.ok) throw new Error(r.error || run.detail || `HTTP ${r.status}`);
    teamReplay(run);
    toast('已载入这次作业的记录（只读回放）', 'ok');
  } catch (e) {
    toast(`打开失败: ${e}`, 'err');
  }
}

function teamReplay(run) {
  const live = TEAM && TEAM.running;
  TEAM = teamNewState();
  TEAM.runId = run.id || '';
  TEAM.title = run.title || '';
  TEAM.replaying = true;
  TEAM.agents = (run.agents || []).map(a => ({
    ...a, status: a.status || 'idle', files: a.files || [], notes: a.notes || [],
  }));
  TEAM.plan = run.plan || { items: [] };
  TEAM.review = run.review || null;
  TEAM.interventions = run.interventions || [];
  TEAM.artifact = run.artifact || '';
  TEAM.events = run.events || [];
  // 回放时用历史里的 paused 状态恢复按钮语义
  TEAM.paused = !!(run.live && run.live.paused) && !!run.running;
  TEAM.running = !!run.running && !live;
  teamRenderAgents();
  teamRenderPlan();
  teamRenderPlanSummary();
  teamSyncFilterOptions();
  teamRenderReview(TEAM.review);
  teamRenderInterventions();
  teamLogSetup();
  TEAM.events.forEach(teamLogEvent);
  const items = TEAM.plan.items || [];
  setTeamRunMeta(`${run.id || ''} · ${TEAM_RUN_STATUS[run.status] || run.status}`
    + (items.length ? ` · ${items.filter(i => i.status === 'done').length}/${items.length} 项` : ''));
  teamLiveBadge(!!run.running);
  setTeamControls();
  if (run.running) {
    teamLogLine('⚠ 这次作业其实还在服务端运行，但当前页面没有连着它的实时流：'
      + '进度会在下面的记录里滞后显示，等它结束后再点开一次就能看到完整结果。', 'warn');
  }
}

/* ---------- 初始化 ---------- */
async function teamInit() {
  if (!TEAM) TEAM = teamNewState();
  if (!TEAM_ROLES.length) {
    try {
      const d = await (await fetch('/api/team/roles')).json();
      TEAM_ROLES = (d.roles || []).filter(r => r && r.id);
    } catch (e) { TEAM_ROLES = []; }
    if (!TEAM_ROLES.length) TEAM_ROLES = TEAM_ROLE_FALLBACK.slice();
  }
  if (!TEAM.agents.length && !TEAM.replaying) {
    TEAM.agents = TEAM_ROLES.map(r => ({
      id: r.id, name: r.name, emoji: r.emoji, color: r.color, stage: r.stage, duty: r.duty,
      status: 'idle', current: '待命中', progress: { done: 0, total: 0 }, files: [], notes: [],
    }));
    teamRenderAgents();
    teamRenderPlan();
    teamSyncFilterOptions();
    teamRenderInterventions();
  }
  setTeamControls();
  await loadTeamRuns();
}

/* ================= 问答（随便问 / 顺手改代码） =================
   和其它页签最大的区别：这里没有固定输入形态。用户可能只是问一句「这个配置在哪改」，
   也可能是「顺手把 loading 补上」。所以：
   - 只读：AI 通过后端只读工具（列目录 / 读文件 / 正则搜索）自己去仓库找证据，
     回答里引用代码会带 `文件路径:行号`；
   - 要改代码时，AI 在回答末尾附一段提案，后端把它落成「产出物」——
     与其它任务同一条路径：人工在产出物里点采纳才会写工作区。
   会话直接落在后端 chat_sessions/，前端只负责渲染与切换（刷新不丢）。 */
const CHAT_PREF_KEY = 'chat-prefs';
let chatSessions = [];
let chatCurrent = '';        // '' = 还没落盘的新会话（首次发送时由后端创建）
let chatMessages = [];
let chatBusy = false;
let chatAbort = null;
let chatWired = false;
let chatDraft = { think: '', out: '' };
let chatLiveTools = [];
let chatPendingEl = null;

function chatPrefs() {
  try { return JSON.parse(localStorage.getItem(CHAT_PREF_KEY) || '{}'); } catch { return {}; }
}

function chatInit() {
  const p = chatPrefs();
  const ur = $('#chat-use-repo');
  const ap = $('#chat-allow-patch');
  if (ur) ur.checked = p.useRepo !== false;
  if (ap) ap.checked = p.allowPatch !== false;
  if (!chatWired) {
    chatWired = true;
    [ur, ap].forEach(cb => cb && cb.addEventListener('change', () => {
      try {
        localStorage.setItem(CHAT_PREF_KEY, JSON.stringify({
          useRepo: ur.checked, allowPatch: ap.checked,
        }));
      } catch (e) { /* 隐私模式不记忆即可 */ }
    }));
    const ta = $('#chat-text');
    if (ta) ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); chatSend(); }
    });
  }
  if (!chatBusy) chatLoadSessions();
  if (!chatMessages.length && !chatBusy) chatRender();
}

async function chatLoadSessions() {
  try {
    const r = await fetch('/api/chat/sessions');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    chatSessions = j.sessions || [];
    chatRenderSessions();
  } catch (e) {
    const el = $('#chat-sessions');
    if (el) el.innerHTML = `<span class="muted">会话列表加载失败：${escapeHtml(String(e))}</span>`;
  }
}

function chatRenderSessions() {
  const el = $('#chat-sessions');
  if (!el) return;
  if (!chatSessions.length) {
    el.innerHTML = '<span class="muted">还没有会话 —— 直接在下面提问会自动建一个。</span>';
    return;
  }
  el.innerHTML = chatSessions.map(s => `
    <button class="chat-chip${s.id === chatCurrent ? ' active' : ''}"
            onclick="chatOpenSession('${escapeAttr(s.id)}')" title="${escapeAttr(s.title)}">
      <span class="chat-chip-title">${escapeHtml(s.title || '新会话')}</span>
      <span class="chat-chip-meta">${s.count || 0} 条 · ${escapeHtml(relTime(s.updated))}</span>
    </button>`).join('');
}

async function chatOpenSession(sid) {
  if (chatBusy) { toast('正在回答中，等它结束再切换', 'err'); return; }
  try {
    const r = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    chatCurrent = sid;
    chatMessages = (j.session && j.session.messages) || [];
    chatRenderSessions();
    chatRender(true);
  } catch (e) {
    toast(`打开会话失败：${e}`, 'err');
  }
}

function chatNewSession() {
  if (chatBusy) { toast('正在回答中，等它结束再新建', 'err'); return; }
  chatCurrent = '';
  chatMessages = [];
  chatRender(true);
  const ta = $('#chat-text');
  if (ta) ta.focus();
  toast('已开一个新会话，提问后才会落盘');
}

async function chatClearSession() {
  if (chatBusy) { toast('正在回答中', 'err'); return; }
  if (!chatCurrent) { chatMessages = []; chatRender(true); return; }
  if (!confirm('清空当前会话的消息？（会话本身保留）')) return;
  try {
    const r = await fetch(`/api/chat/sessions/${encodeURIComponent(chatCurrent)}/clear`, { method: 'POST' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    chatMessages = [];
    chatRender(true);
    chatLoadSessions();
    toast('已清空');
  } catch (e) { toast(`清空失败：${e}`, 'err'); }
}

async function chatDeleteSession() {
  if (chatBusy) { toast('正在回答中', 'err'); return; }
  if (!chatCurrent) { toast('当前是尚未保存的新会话', 'err'); return; }
  if (!confirm('删除这个会话？删除后不可恢复。')) return;
  try {
    const r = await fetch(`/api/chat/sessions/${encodeURIComponent(chatCurrent)}/delete`, { method: 'POST' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    chatCurrent = '';
    chatMessages = [];
    chatRender(true);
    chatLoadSessions();
    toast('已删除会话');
  } catch (e) { toast(`删除失败：${e}`, 'err'); }
}

/* ---- 渲染 ---- */
function chatNearBottom() {
  const box = $('#chat-stream');
  if (!box) return true;
  return box.scrollHeight - box.scrollTop - box.clientHeight < 140;
}

function chatScroll(stick) {
  const box = $('#chat-stream');
  if (box && stick) box.scrollTop = box.scrollHeight;
}

function chatMsgHtml(m) {
  const isUser = m.role === 'user';
  const meta = m.meta || {};
  let extra = '';
  const tools = meta.tools || [];
  if (tools.length && !isUser) {
    extra += '<details class="chat-think"><summary>查了仓库 '
      + tools.length + ' 次</summary><ul>'
      + tools.map(t => `<li><code>${escapeHtml(t.name || '')}</code> `
          + `<span class="chat-args">${escapeHtml(JSON.stringify(t.arguments || {}))}</span>`
          + ` <span class="muted">${t.chars || 0} 字符</span></li>`).join('')
      + '</ul></details>';
  }
  if (meta.artifact && !isUser) {
    const fc = (meta.artifactFiles || []).length;
    extra += `<div class="chat-art">
      <div class="chat-art-head">已生成改动提案：${escapeHtml(meta.artifactTitle || '')}</div>
      <div class="chat-art-files">${(meta.artifactFiles || []).map(f =>
        `<code>${escapeHtml(f)}</code>`).join('')}</div>
      <div class="chat-art-note">${fc} 个文件 · 还没有写入仓库，采纳才会动工作区</div>
      <button class="btn-primary btn-sm" onclick="openArtifact('${escapeAttr(meta.artifact)}')">查看 / 采纳</button>
    </div>`;
  }
  if (meta.error && !isUser) {
    extra += `<div class="chat-err">${escapeHtml(meta.error)}</div>`;
  }
  const body = isUser
    ? escapeHtml(m.content).replace(/\n/g, '<br>')
    : renderMarkdown(m.content || '');
  return `<div class="chat-msg ${isUser ? 'me' : 'ai'}">
    <div class="chat-role">${isUser ? '你' : '助手'}<span class="chat-time">${escapeHtml(relTime(m.ts || ''))}</span></div>
    <div class="chat-body">${body}</div>
    ${extra}
  </div>`;
}

function chatRender(force) {
  const box = $('#chat-stream');
  if (!box) return;
  const stick = force || chatNearBottom();
  if (!chatMessages.length) {
    box.innerHTML = `<div class="chat-empty">
      <div class="chat-empty-title">随便问点什么，或者顺手让我改点代码</div>
      <div class="chat-empty-tips">
        <span>这个项目的路由是怎么配的？</span>
        <span>这个报错可能是什么原因？（把日志粘进来）</span>
        <span>把 xxx 组件的 loading 状态补上</span>
      </div>
      <div class="chat-empty-note">开启「允许查仓库」时，我会自己列目录 / 读文件 / 正则搜索去找证据；引用代码会带 <code>文件路径:行号</code>。</div>
    </div>`;
  } else {
    box.innerHTML = chatMessages.map(chatMsgHtml).join('');
  }
  chatScroll(stick);
}

function chatAddPending() {
  const box = $('#chat-stream');
  if (!box) return;
  const el = document.createElement('div');
  el.className = 'chat-msg ai pending';
  el.innerHTML = `<div class="chat-role">助手<span class="chat-time">生成中…</span></div>
    <details class="chat-think hidden"><summary>思考过程</summary><pre></pre></details>
    <details class="chat-think hidden chat-live-tools"><summary>查仓库</summary><ul></ul></details>
    <div class="chat-body"></div>`;
  box.appendChild(el);
  chatPendingEl = el;
  chatScroll(true);
}

function chatRenderPending() {
  if (!chatPendingEl) return;
  const stick = chatNearBottom();
  const think = chatPendingEl.querySelector('.chat-think:not(.chat-live-tools)');
  if (think) {
    think.classList.toggle('hidden', !chatDraft.think);
    const pre = think.querySelector('pre');
    if (pre) pre.textContent = chatDraft.think;
  }
  const tw = chatPendingEl.querySelector('.chat-live-tools');
  if (tw) {
    tw.classList.toggle('hidden', !chatLiveTools.length);
    const ul = tw.querySelector('ul');
    if (ul) ul.innerHTML = chatLiveTools.map(t =>
      `<li><code>${escapeHtml(t.name || '')}</code> ${escapeHtml(JSON.stringify(t.arguments || {}))}</li>`).join('');
  }
  const body = chatPendingEl.querySelector('.chat-body');
  if (body) {
    body.innerHTML = chatDraft.out
      ? renderMarkdown(chatDraft.out) + '<span class="chat-caret"></span>'
      : '<span class="muted">…</span>';
  }
  chatScroll(stick);
}

function chatSetStatus(t) {
  const el = $('#chat-status');
  if (el) el.textContent = t || '';
}
function chatSetTools(t) {
  const el = $('#chat-tools');
  if (el) el.textContent = t || '';
}

/* ---- 发送 / 停止 ---- */
function chatOnEvent(evt) {
  if (!evt) return;
  if (evt.type === 'stage') { chatSetStatus(evt.stage || ''); return; }
  if (evt.type === 'tool') {
    chatLiveTools.push({ name: evt.tool, arguments: evt.arguments, chars: evt.chars });
    chatSetTools(`已检索仓库 ${chatLiveTools.length} 次`);
    chatRenderPending();
    return;
  }
  if (evt.type === 'ai_delta') {
    if (evt.kind === 'reasoning') chatDraft.think += evt.text || '';
    else chatDraft.out += evt.text || '';
    chatRenderPending();
    return;
  }
  if (evt.type === 'fatal') chatSetStatus(`出错了：${evt.error || ''}`);
}

async function chatSend() {
  if (chatBusy) return;
  const ta = $('#chat-text');
  const text = ((ta && ta.value) || '').trim();
  if (!text) { toast('先写点什么', 'err'); return; }
  if (ta) ta.value = '';
  chatMessages.push({ role: 'user', content: text, ts: new Date().toISOString() });
  chatRender(true);
  chatBusy = true;
  chatDraft = { think: '', out: '' };
  chatLiveTools = [];
  chatSetTools('');
  const sendBtn = $('#btn-chat-send');
  if (sendBtn) sendBtn.disabled = true;
  const stopBtn = $('#btn-chat-stop');
  if (stopBtn) stopBtn.classList.remove('hidden');
  chatSetStatus('已提交，等待模型响应…');
  chatAddPending();
  const ctrl = new AbortController();
  chatAbort = ctrl;
  try {
    const done = await sseConsume('/api/chat/stream', {
      session_id: chatCurrent,
      message: text,
      use_repo: $('#chat-use-repo') ? $('#chat-use-repo').checked : true,
      allow_patch: $('#chat-allow-patch') ? $('#chat-allow-patch').checked : true,
    }, chatOnEvent, ctrl.signal);
    if (done) chatFinish(done.payload);
    else chatFinish(null, '连接中断了（服务端可能仍在跑完这次调用，稍后刷新能看到结果）');
  } catch (e) {
    const aborted = e && (e.name === 'AbortError' || String(e).includes('abort'));
    chatFinish(null, aborted
      ? '已停止接收输出。服务端会把这一次跑完并存进会话，稍后刷新会话即可看到。'
      : String((e && e.message) || e));
  }
}

function chatStop() {
  if (chatAbort) {
    try { chatAbort.abort(); } catch (e) { /* 已经断开 */ }
  }
}

function chatFinish(payload, errMsg) {
  chatBusy = false;
  chatAbort = null;
  const sendBtn = $('#btn-chat-send');
  if (sendBtn) sendBtn.disabled = false;
  const stopBtn = $('#btn-chat-stop');
  if (stopBtn) stopBtn.classList.add('hidden');
  chatSetStatus('');
  chatSetTools('');
  if (chatPendingEl) { chatPendingEl.remove(); chatPendingEl = null; }
  if (payload) {
    if (payload.session_id) chatCurrent = payload.session_id;
    const art = payload.artifact || null;
    chatMessages.push({
      role: 'assistant',
      content: payload.reply || '（没有产出内容）',
      ts: new Date().toISOString(),
      meta: {
        tools: payload.tools || [],
        artifact: art ? art.id : '',
        artifactTitle: art ? art.title : '',
        artifactFiles: art ? (art.files || []) : [],
        error: payload.error || '',
        ai_mode: payload.ai_mode || '',
      },
    });
  } else if (errMsg) {
    chatMessages.push({ role: 'assistant', content: `⚠️ ${errMsg}`, ts: new Date().toISOString(), meta: { error: errMsg } });
  }
  chatRender(true);
  chatLoadSessions();
  if (payload && payload.artifact) {
    toast(`已生成改动提案（${payload.artifact.file_count} 个文件），采纳才会写工作区`, 'ok');
    if (activeTab === 'chat') loadArtifacts();
  }
}

/* ================= helpers ================= */
async function loadAll() {
  await loadAiPresets();   // 模板要先到位，否则表单联动拿不到默认值
  await Promise.all([loadHealth(), loadProposals(), loadStats(), loadKb(), loadSettings(), teamInit()]);
}

// 审批状态是共享的：页面可见时定期刷新，避免对着过期状态点采纳
setInterval(() => {
  if (document.hidden) return;
  loadProposals();
  loadArtifacts();
  loadHealth();
  // 停在统计面板时顺带刷新用量，跑长任务时曲线能自己长出来
  if (activeTab === 'stats') loadUsage();
}, 12000);

function renderMarkdown(md) {
  /* 块级解析器：fenced code 必须最先处理（内部空行/井号/星号都不参与其它语法），
     否则 SEARCH/REPLACE 与 diff 里的空行会把代码块腰斩成普通段落。 */
  const lines = String(md ?? '').split('\n');
  const out = [];
  const inline = s => escapeHtml(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    // 模式卡与 INDEX 里的关联缺陷写成 markdown 相对链接（分类/缺陷.md、
    // _patterns/模式.md）。外链直接跳，站内相对链接交给 openKbDoc 路由，
    // 这样正文里点缺陷 id 也能下钻，不必只依赖弹窗头部的关联缺陷链接。
    .replace(/\[([^\]\n]+)\]\(([A-Za-z0-9_./\u4e00-\u9fa5-]+)\)/g, (m, text, url) =>
      /^(https?:)?\/\//.test(url)
        ? `<a class="link" href="${url}" target="_blank" rel="noopener">${text}</a>`
        : `<a class="link" href="#" onclick="openKbDoc('${url}');return false;">${text}</a>`);
  const isTableLine = l => /^\s*\|.*\|\s*$/.test(l);
  const parseRow = l => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```([A-Za-z0-9_+-]*)\s*$/);
    if (fence) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // 闭合 ```（缺失时到结尾也算块结束）
      out.push(`<pre><code>${escapeHtml(buf.join('\n'))}</code></pre>`);
      continue;
    }
    if (isTableLine(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = parseRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableLine(lines[i])) { rows.push(parseRow(lines[i])); i++; }
      out.push('<table><thead><tr>'
        + head.map(h => `<th>${inline(h)}</th>`).join('')
        + '</tr></thead><tbody>'
        + rows.map(r => '<tr>' + head.map((_, ci) => `<td>${inline(r[ci] ?? '')}</td>`).join('') + '</tr>').join('')
        + '</tbody></table>');
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`); i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*\d+\.\s+/, ''))}</li>`); i++;
      }
      out.push(`<ol>${items.join('')}</ol>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim()
           && !/^```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])
           && !isTableLine(lines[i]) && !/^\s*[-*]\s+/.test(lines[i])
           && !/^\s*\d+\.\s+/.test(lines[i])) {
      buf.push(lines[i]); i++;
    }
    out.push(`<p>${buf.map(inline).join('<br>')}</p>`);
  }
  return out.join('\n');
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function escapeAttr(s) { return escapeHtml(s); }
function shortPath(p) {
  if (!p) return '(未配置)';
  return p.length > 50 ? '…' + p.slice(-47) : p;
}

/* ================= fully custom themed dropdown (replaces native select popup) ================= */
let csPanelEl = null;
let csOpenState = null;

function ensureCsPanel() {
  if (!csPanelEl) {
    csPanelEl = document.createElement('div');
    csPanelEl.className = 'cs-panel';
    document.body.appendChild(csPanelEl);
  }
  return csPanelEl;
}

function closeCsPanel() {
  if (!csOpenState) return;
  csPanelEl.classList.remove('open');
  csOpenState.trigger.classList.remove('open');
  csOpenState = null;
}

function positionCsPanel(trigger) {
  const r = trigger.getBoundingClientRect();
  csPanelEl.style.minWidth = r.width + 'px';
  csPanelEl.style.left = Math.min(r.left, window.innerWidth - csPanelEl.offsetWidth - 8) + 'px';
  const spaceBelow = window.innerHeight - r.bottom;
  if (csPanelEl.offsetHeight + 10 <= spaceBelow || spaceBelow >= r.top) {
    csPanelEl.style.top = (r.bottom + 6) + 'px';
  } else {
    csPanelEl.style.top = (r.top - csPanelEl.offsetHeight - 6) + 'px';
  }
}

function openCsPanel(sel, trigger) {
  const panel = ensureCsPanel();
  panel.innerHTML = '';
  [...sel.options].forEach(opt => {
    if (opt.disabled) return;
    const item = document.createElement('div');
    item.className = 'cs-option' + (opt.value === sel.value ? ' selected' : '');
    item.textContent = opt.textContent;
    item.addEventListener('click', () => {
      if (sel.value !== opt.value) {
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      }
      if (sel._csSync) sel._csSync();
      closeCsPanel();
    });
    panel.appendChild(item);
  });
  panel.classList.add('open');
  trigger.classList.add('open');
  csOpenState = { sel, trigger };
  positionCsPanel(trigger);
}

function initCustomSelects() {
  document.querySelectorAll('select').forEach(sel => {
    if (sel._csInit) return;
    sel._csInit = true;
    sel.classList.add('cs-native');
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'cs-trigger';
    trigger.innerHTML = '<span class="cs-label"></span>'
      + '<svg class="cs-caret" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    const label = trigger.querySelector('.cs-label');
    const sync = () => {
      const opt = sel.selectedOptions[0];
      label.textContent = opt ? opt.textContent : '';
      trigger.classList.toggle('is-empty', !sel.value);
    };
    sel._csSync = sync;
    sel.after(trigger);
    sync();
    trigger.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      if (csOpenState && csOpenState.trigger === trigger) closeCsPanel();
      else openCsPanel(sel, trigger);
    });
    // 选项列表被重建（如拉取模板后）时自动刷新显示
    new MutationObserver(sync).observe(sel, { childList: true, subtree: true, characterData: true });
  });
}

document.addEventListener('click', e => {
  if (csOpenState && csPanelEl && !csPanelEl.contains(e.target)) closeCsPanel();
  if (modelSuggestPanel && modelSuggestPanel.classList.contains('open')
      && !modelSuggestPanel.contains(e.target) && e.target !== $('#cfg-ai-model')) closeModelSuggest();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeCsPanel(); closeModelSuggest(); }
});
window.addEventListener('resize', () => { closeCsPanel(); closeModelSuggest(); });
window.addEventListener('scroll', e => {
  const t = e.target;
  if (t === csPanelEl || t === modelSuggestPanel) return;
  closeCsPanel();
  closeModelSuggest();
}, true);

initCustomSelects();

/* ================= model name suggestion list (custom, replaces datalist) ================= */
let modelSuggestList = [];
let modelSuggestPanel = null;

function closeModelSuggest() {
  if (modelSuggestPanel) modelSuggestPanel.classList.remove('open');
}

function showModelSuggest() {
  if (!modelSuggestList.length) return;
  if (!modelSuggestPanel) {
    modelSuggestPanel = document.createElement('div');
    modelSuggestPanel.className = 'cs-panel';
    document.body.appendChild(modelSuggestPanel);
  }
  const input = $('#cfg-ai-model');
  const q = input.value.trim().toLowerCase();
  const items = modelSuggestList.filter(m => !q || m.toLowerCase().includes(q));
  if (!items.length) { closeModelSuggest(); return; }
  modelSuggestPanel.innerHTML = items.map(m =>
    `<div class="cs-option${m === input.value ? ' selected' : ''}" data-m="${escapeAttr(m)}">${escapeHtml(m)}</div>`
  ).join('');
  modelSuggestPanel.classList.add('open');
  const r = input.getBoundingClientRect();
  modelSuggestPanel.style.minWidth = r.width + 'px';
  modelSuggestPanel.style.left = Math.min(r.left, window.innerWidth - modelSuggestPanel.offsetWidth - 8) + 'px';
  const below = window.innerHeight - r.bottom;
  if (modelSuggestPanel.offsetHeight + 10 <= below || below >= r.top) {
    modelSuggestPanel.style.top = (r.bottom + 6) + 'px';
  } else {
    modelSuggestPanel.style.top = (r.top - modelSuggestPanel.offsetHeight - 6) + 'px';
  }
  modelSuggestPanel.querySelectorAll('.cs-option').forEach(el => {
    el.addEventListener('click', () => {
      input.value = el.dataset.m;
      closeModelSuggest();
    });
  });
}

(function initModelSuggest() {
  const input = $('#cfg-ai-model');
  if (!input) return;
  input.addEventListener('focus', showModelSuggest);
  input.addEventListener('input', showModelSuggest);
  input.addEventListener('click', e => { e.stopPropagation(); showModelSuggest(); });
})();

/* ================= 面板折叠（状态记忆在 localStorage） ================= */
const FOLD_KEY = 'panel-fold-state';

function togglePanel(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const collapsed = el.classList.toggle('collapsed');
  try {
    const saved = JSON.parse(localStorage.getItem(FOLD_KEY) || '{}');
    saved[id] = collapsed;
    localStorage.setItem(FOLD_KEY, JSON.stringify(saved));
  } catch { /* 隐私模式等场景下不记忆状态即可 */ }
}

function restoreFolds() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(FOLD_KEY) || '{}'); } catch { return; }
  Object.entries(saved).forEach(([id, collapsed]) => {
    const el = document.getElementById(id);
    if (el && collapsed) el.classList.add('collapsed');
  });
}

switchTab('stats');        // 首屏落在「统计」（第一个页签）
loadAll();
restoreFolds();
setFigmaMode(figmaMode);   // 恢复上次选的取稿通道（token / 浏览器）
initChangelog();           // 拉起未读小圆点（后端是旧代码时静默跳过）
chatInit();                // 问答页签：恢复两个开关 + 拉会话列表（后端旧代码时静默降级）
