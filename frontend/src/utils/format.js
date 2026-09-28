// 展示层纯函数。全部从旧版 app.js 逐条搬运，行为必须一致 ——
// 迁移期的原则是「换框架不换口径」，格式化结果一旦漂移，界面用例和历史截图都会对不上。

/** 千分位整数。旧版 app.js:2831 */
export function fmtInt(n) {
  return (Number(n) || 0).toLocaleString('en-US')
}

/** token 数压成 1.2k / 3M / 1.5B。旧版 app.js:2835 */
export function fmtTok(n) {
  n = Number(n) || 0
  if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M'
  if (n >= 1e4) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + 'k'
  return fmtInt(n)
}

/** 坐标轴刻度取「好看」的整数步长（1/2/2.5/5/10 × 10^n）。旧版 app.js:2844 */
export function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(1, raw))))
  const c = raw / p
  const m = c <= 1 ? 1 : c <= 2 ? 2 : c <= 2.5 ? 2.5 : c <= 5 ? 5 : 10
  return m * p
}

/** 相对时间。旧版 app.js:84 —— 文案与阈值都保持一致 */
export function relTime(iso) {
  if (!iso) return '—'
  const t = new Date(String(iso)).getTime()
  if (Number.isNaN(t)) return String(iso)
  const mins = Math.floor(Math.max(0, Date.now() - t) / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs} 小时前`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days} 天前`
  return String(iso).slice(0, 10)
}

/** 把 ISO 时间戳压成 `MM-DD HH:MM:SS`（去掉 T）。旧版用 replace('T',' ').slice(5,19) */
export function shortTs(ts, start = 5, end = 19) {
  return String(ts || '').replace('T', ' ').slice(start, end)
}

/** 附件体积。旧版 app.js:3856 —— 阈值与小数位保持一致 */
export function fmtSize(n) {
  const b = Number(n) || 0
  if (b < 1024) return `${b} B`
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1048576).toFixed(1)} MB`
}

/** 长路径压成「…+ 末 47 字符」。旧版 app.js:4438（健康行 / AI 摘要用） */
export function shortPath(p) {
  if (!p) return '(未配置)'
  return String(p).length > 50 ? '…' + String(p).slice(-47) : p
}

/** HTML 转义。旧版 app.js:4429 —— 顺序不能动（& 必须最先） */
export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * 极简 markdown → HTML。旧版 app.js:4355 的逐行移植。
 *
 * 保留原实现的原因（而不是换成 marked/markdown-it）：
 *   1. 内网离线可用，少一个运行时依赖；
 *   2. 这段解析器的行为已经被既有界面用例锁定（表格 / 代码块 / 站内相对链接
 *      要路由到 openKbDoc），换库就是改口径。
 *
 * 块级解析器：fenced code 必须最先处理（内部空行/井号/星号都不参与其它语法），
 * 否则 SEARCH/REPLACE 与 diff 里的空行会把代码块腰斩成普通段落。
 *
 * @param {string} md
 * @param {(rel: string) => void} [onLocalLink] 站内相对链接（如 `分类/缺陷.md`）的点击回调
 */
export function renderMarkdown(md, onLocalLink) {
  const lines = String(md ?? '').split('\n')
  const out = []
  const inline = (s) =>
    escapeHtml(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      // 模式卡与 INDEX 里的关联缺陷写成 markdown 相对链接（分类/缺陷.md、
      // _patterns/模式.md）。外链直接跳，站内相对链接交给调用方路由，
      // 这样正文里点缺陷 id 也能下钻，不必只依赖弹窗头部的关联缺陷链接。
      .replace(/\[([^\]\n]+)\]\(([A-Za-z0-9_./\u4e00-\u9fa5-]+)\)/g, (m, text, url) =>
        /^(https?:)?\/\//.test(url)
          ? `<a class="link" href="${url}" target="_blank" rel="noopener">${text}</a>`
          : `<a class="link" href="#" data-kb-link="${escapeHtml(url)}">${text}</a>`
      )
  const isTableLine = (l) => /^\s*\|.*\|\s*$/.test(l)
  const parseRow = (l) =>
    l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const fence = line.match(/^```([A-Za-z0-9_+-]*)\s*$/)
    if (fence) {
      const buf = []
      i++
      while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++ }
      i++ // 闭合 ```（缺失时到结尾也算块结束）
      out.push(`<pre><code>${escapeHtml(buf.join('\n'))}</code></pre>`)
      continue
    }
    if (isTableLine(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = parseRow(line)
      i += 2
      const rows = []
      while (i < lines.length && isTableLine(lines[i])) { rows.push(parseRow(lines[i])); i++ }
      out.push('<table><thead><tr>'
        + head.map((h) => `<th>${inline(h)}</th>`).join('')
        + '</tr></thead><tbody>'
        + rows.map((r) => '<tr>' + head.map((_, ci) => `<td>${inline(r[ci] ?? '')}</td>`).join('') + '</tr>').join('')
        + '</tbody></table>')
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out.push('<hr>'); i++; continue }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`); i++
      }
      out.push(`<ul>${items.join('')}</ul>`)
      continue
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*\d+\.\s+/, ''))}</li>`); i++
      }
      out.push(`<ol>${items.join('')}</ol>`)
      continue
    }
    if (!line.trim()) { i++; continue }
    const buf = [line]
    i++
    while (i < lines.length && lines[i].trim()
           && !/^```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])
           && !isTableLine(lines[i]) && !/^\s*[-*]\s+/.test(lines[i])
           && !/^\s*\d+\.\s+/.test(lines[i])) {
      buf.push(lines[i]); i++
    }
    out.push(`<p>${buf.map(inline).join('<br>')}</p>`)
  }
  return out.join('\n')
}

/**
 * 把 renderMarkdown 的输出里带 `data-kb-link` 的链接接到回调上。
 * 旧版是内联 `onclick="openKbDoc('...')"`；Vue 里用事件委托，
 * 避免把全局函数名写进生成的 HTML（那会绕过框架的事件系统）。
 */
export function wireKbLinks(rootEl, onLocalLink) {
  if (!rootEl || !onLocalLink) return
  rootEl.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[data-kb-link]') : null
    if (!a) return
    e.preventDefault()
    onLocalLink(a.getAttribute('data-kb-link'))
  })
}
