"""Stage 5: persist each defect + proposal as a Markdown knowledge-base card.

INDEX.md and _stats.json are *derived* — they are rebuilt from the cards on every
write instead of being appended to, so re-running the pipeline over the same defect
updates the card in place rather than inflating the index forever.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .analyzer import strip_html

_META_CATEGORIES = {"_stats.json", "INDEX.md"}

# 工单描述里小节标记的三种写法：一、/ 1、/ 1. / (1) / ①
_SEC_HEAD_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+[、.]|\d{1,2}[、.)]|[（(]\d{1,2}[)）]|[①-⑳])")
_BULLET_RE = re.compile(r"^\s*[-–—•·*]\s+")
# `【xx】` 后面紧跟正文时补一个换行（``【】`` 开头的行本来就是行首，不受影响）
_MARK_RE = re.compile(r"(?<!\n)(【[^】\n]{1,20}】)")
# 句末标点后紧跟的「【xx】」：同段内的下一节，起新行比留在行尾清楚
_TAIL_MARK_RE = re.compile(r"(?<=[。！？；!?;])\s*(?=【[^】\n]{1,20}】)")
# `【xx】正文` 这种「标记+同行内容」的行，标记后补空行做段落边界
_INLINE_MARK_RE = re.compile(r"^(【[^】\n]{1,20}】)(?=\S)")


def _safe(s) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))[:80]


def _yaml_escape(s: str) -> str:
    return str(s).replace('"', '\\"').replace("\n", " ")


def _fence(info: str, body: str) -> str:
    """Pick a fence longer than any backtick run inside the body so code can't escape."""
    runs = [len(m.group(0)) for m in re.finditer(r"`+", body or "")] or [0]
    ticks = "`" * max(3, max(runs) + 1)
    return f"{ticks}{info}\n{body}\n{ticks}"


class KnowledgeBase:
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.output_dir / "INDEX.md"
        self.stats_path = self.output_dir / "_stats.json"

    # ------------------------------------------------------------------ api
    def record_from_proposal(self, proposal: Dict) -> str:
        """Re-render a card straight from a stored proposal (used after approval)."""
        return self.record(
            proposal.get("defect") or {},
            proposal.get("analysis") or {},
            proposal,
        )

    def record(self, defect: Dict, analysis: Dict, proposal: Dict) -> str:
        category = analysis.get("category") or "其他"
        cat_dir = self.output_dir / _safe(category)
        cat_dir.mkdir(exist_ok=True)

        slug = _safe(defect.get("id") or datetime.now().strftime("%Y%m%d%H%M%S"))
        md_path = cat_dir / f"{slug}.md"

        # 同一缺陷只保留一张卡片：后续提案的分类可能变（逻辑→其他），
        # 旧分类目录里的同名卡片必须搬走，否则知识库出现重复条目。
        for d in self._card_dirs():
            stale = d / f"{slug}.md"
            if stale != md_path and stale.exists():
                stale.unlink()

        status = "proposed" if proposal.get("status") == "pending" else proposal.get("status", "")
        apply_info = proposal.get("apply") or {}
        gate = proposal.get("gate") or {}
        verify = proposal.get("verify") or {}
        patch = proposal.get("patch") or {}
        changes: List[Dict] = patch.get("changes", []) or []

        content = (
            "---\n"
            f"defect_id: {defect.get('id', '')}\n"
            f"title: \"{_yaml_escape(defect.get('title', ''))}\"\n"
            f"category: {category}\n"
            f"priority: {defect.get('priority', '')}\n"
            f"ai_mode: {analysis.get('ai_mode', '')}\n"
            f"proposal_id: {proposal.get('id', '')}\n"
            f"status: {status}\n"
            f"gate_level: {gate.get('level', '')}\n"
            f"commit: {apply_info.get('sha', '')}\n"
            f"created: {proposal.get('created') or datetime.now().isoformat(timespec='seconds')}\n"
            f"updated: {datetime.now().isoformat(timespec='seconds')}\n"
            f"kb_version: 2\n"
            "---\n\n"
            f"# {defect.get('title', slug)}\n\n"
            "## 现象\n"
            # ONES 描述是富文本 HTML：直接塞进卡片会在渲染时显示一坨标签
            f"{_clean_text(defect.get('description') or defect.get('desc')) or '(无描述)'}\n\n"
            "## 根因\n"
            f"{analysis.get('root_cause', '(待补充)') or '(待补充)'}\n\n"
            f"## 说明\n{analysis.get('explanation') or '(模型未提供补充说明)'}\n\n"
            f"## 影响文件\n{_files_table(changes)}\n\n"
            "## 补丁（SEARCH/REPLACE）\n"
            f"{_fence('search-replace', analysis.get('patch_canonical') or '(无可用补丁)')}\n\n"
            "## 建议 diff\n"
            f"{_fence('diff', patch.get('combined_diff') or '(未生成 diff)')}\n\n"
            "## 验收闸门\n"
            f"{_fence('json', json.dumps(gate, ensure_ascii=False, indent=2))}\n\n"
            "## 页面验证\n"
            f"{_fence('json', json.dumps(_verify_slim(verify), ensure_ascii=False, indent=2))}\n\n"
            "## 复现步骤\n"
            f"{_steps(defect)}\n\n"
            "## 预防措施\n"
            f"{analysis.get('prevention', '(待补充)') or '(待补充)'}\n\n"
            "## 关联信息\n"
            f"- 嫌疑文件: {', '.join(analysis.get('suspect_files', [])[:8]) or '—'}\n"
            f"- 关键词: {', '.join(analysis.get('keywords', [])[:12]) or '—'}\n"
            f"- 提案状态: {status}；commit: {apply_info.get('sha') or '—'}\n"
            f"- 提案文件: `proposals/{proposal.get('id', '')}.json`\n"
        )
        md_path.write_text(content, encoding="utf-8")
        self.rebuild()
        return str(md_path)

    # ----------------------------------------------------------- derivation
    def rebuild(self) -> Dict[str, int]:
        cards = list(self._cards())
        # 模式层（同类缺陷的共性沉淀）也是派生视图：放在卡片之后重建，
        # 保证卡片是事实来源、模式只是它的聚合。它坏掉不能拖垮卡片落盘，
        # 因此单独 try——错误会写进 _stats.json 的 pattern_error 里可见。
        patterns: List[Dict] = []
        pattern_error = ""
        try:
            from .kb_patterns import rebuild_patterns
            patterns = rebuild_patterns(self.output_dir)
        except Exception as e:  # noqa: BLE001
            pattern_error = f"{type(e).__name__}: {e}"
        self._write_index(cards, patterns)
        return self._write_stats(cards, patterns, pattern_error)

    def _card_dirs(self) -> List[Path]:
        if not self.output_dir.exists():
            return []
        return [
            d for d in sorted(self.output_dir.iterdir())
            if d.is_dir() and not d.name.startswith((".", "_"))
        ]

    def _cards(self) -> List[Dict]:
        out = []
        for cat_dir in self._card_dirs():
            for f in sorted(cat_dir.glob("*.md")):
                if f.name in _META_CATEGORIES:
                    continue
                front = _parse_front(f.read_text(encoding="utf-8"))
                front.setdefault("category", cat_dir.name)
                front.setdefault("defect_id", f.stem)
                front["_path"] = str(f)
                out.append(front)
        return out

    def _write_index(self, cards: List[Dict],
                     patterns: Optional[List[Dict]] = None) -> None:
        by_cat: Dict[str, List[Dict]] = {}
        for c in cards:
            by_cat.setdefault(c.get("category", "其他"), []).append(c)
        lines = [
            "# 缺陷知识库",
            "",
            "> 由 AI 缺陷修复流水线沉淀。分两层：**模式**是同类缺陷的共性沉淀，"
            "**缺陷卡**是每个工单的事实档案。本文件为自动派生，请勿手工编辑。",
            f"> 共 {len(patterns or [])} 个模式 / {len(cards)} 张卡片，"
            f"最后重建：{datetime.now():%Y-%m-%d %H:%M:%S}",
            "",
        ]
        from .kb_patterns import render_index_section
        lines += render_index_section(list(patterns or []))
        lines += ["## 缺陷卡片", "",
                  "> 一个工单一张卡：现象、根因、补丁、闸门与页面验证结果。"
                  "同一缺陷重跑只会更新卡片，不会重复堆积。", ""]
        for cat in sorted(by_cat):
            lines.append(f"## {cat}（{len(by_cat[cat])}）")
            lines.append("")
            lines.append("| 缺陷 | 标题 | 状态 | Commit | 更新时间 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for c in sorted(by_cat[cat], key=lambda x: x.get("defect_id", "")):
                rel = f"{_safe(cat)}/{_safe(c.get('defect_id'))}.md"
                sha = (c.get("commit") or "")[:8] or "—"
                lines.append(
                    f"| [{c.get('defect_id')}]({rel}) | "
                    f"{_inline(c.get('title', ''))} | {c.get('status') or '—'} | "
                    f"{sha} | {(c.get('updated') or c.get('created') or '')[:16].replace('T', ' ') or '—'} |"
                )
            lines.append("")
        self.index_path.write_text("\n".join(lines), encoding="utf-8")

    def _write_stats(self, cards: List[Dict],
                     patterns: Optional[List[Dict]] = None,
                     pattern_error: str = "") -> Dict[str, int]:
        stats: Dict[str, Dict[str, int]] = {}
        for c in cards:
            cat = c.get("category") or "其他"
            s = stats.setdefault(cat, {"total": 0, "applied": 0, "pending": 0, "rejected": 0})
            s["total"] += 1
            status = c.get("status", "")
            if status == "applied":
                s["applied"] += 1
            elif status in ("pending", "gate_failed", "invalid"):
                s["pending"] += 1
            elif status in ("rejected", "apply_failed"):
                s["rejected"] += 1
        plist = list(patterns or [])
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "cards": len(cards),
            "categories": stats,
            "patterns": {
                "count": len(plist),
                # 复发 ≥2 的模式 = 已经被证明会重复出现的问题，最值得盯
                "recurring": len([p for p in plist if (p.get("recurrence") or 0) >= 2]),
                "list": plist,
            },
        }
        if pattern_error:
            payload["pattern_error"] = pattern_error
        self.stats_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payload


def _inline(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")[:70]


def _clean_text(s) -> str:
    """富文本 → 干净纯文本：剥 HTML、还原段落结构、压掉多余空行。

    卡片正文是按 markdown 渲染的：markdown 里**单个换行不换行**，所以必须
    保留空行或转成列表项，否则工单描述的每个小节会被渲染器拼成一整段
    （表现就是「现象」区全挤在一起）。
    """
    if not s:
        return ""
    text = strip_html(str(s))
    return _markdownize(text)


def _markdownize(text: str) -> str:
    """把工单描述的行结构规范成可稳定渲染的 markdown。

    - `一、` / `1.` / `(1)` 这类小节标记前补空行（markdown 段落边界）
    - `-` / `•` / `·` 开头的行统一成 `- `，连续两行以上补空行保证被识别成列表
    - `【xx】` 出现在行中时起新行，句末标点后的 `【xx】` 也起新行
    """
    if not text:
        return ""
    text = _MARK_RE.sub(r"\n\1", text)
    text = _TAIL_MARK_RE.sub("\n", text)

    lines: List[str] = []
    in_list = False
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            lines.append("")
            in_list = False
            continue
        if _BULLET_RE.match(line):
            line = "- " + _BULLET_RE.sub("", line).strip()
            if lines and not in_list and lines[-1] != "":
                lines.append("")   # 列表与上一段之间要有空行
            in_list = True
        else:
            # 小节起点：一、/1./（1）这类序号，或【xx】标记行（无论后面
            # 有没有跟内容），都让它独立成段——与上一段之间补空行。
            is_head = bool(_SEC_HEAD_RE.match(line)) or bool(line.startswith("【") and "】" in line[:24])
            if is_head and lines and lines[-1] != "":
                lines.append("")
            in_list = False
        lines.append(line)

    # 「标记段 + 紧跟的同一主题内容」之间本来就是空行，块级标签被换掉后
    # 往往只剩单换行（markdown 不认），这里补回空行恢复原始分段。
    merged: List[str] = []
    for line in lines:
        if merged and line and merged[-1] and _INLINE_MARK_RE.match(merged[-1]):
            merged.append("")
        merged.append(line)

    out = re.sub(r"\n{3,}", "\n\n", "\n".join(merged)).strip()
    return out


def _verify_slim(verify: Dict) -> Dict:
    """页面验证结果只留人关心的字段：replay_actions/sample 这类机器字段不进卡片。"""
    keys = ("status", "phase", "route", "route_source", "final_url", "page_title",
            "plan_note", "actions", "actions_log", "console_errors", "page_errors",
            "error", "reason")
    return {k: verify[k] for k in keys if k in verify}


def _files_table(changes: List[Dict]) -> str:
    if not changes:
        return "（无文件改动）"
    rows = ["| 文件 | 补丁块 | +行 | -行 | 状态 | 告警 |", "| --- | --- | --- | --- | --- | --- |"]
    for c in changes:
        stats = c.get("stats") or {}
        state = "❌ " + (c.get("error") or "") if c.get("error") else "✅ 可应用"
        warns = "；".join(c.get("warnings") or []) or "—"
        rows.append(
            f"| `{c.get('file_path')}` | {c.get('applied_blocks', 0)} | "
            f"{stats.get('added', 0)} | {stats.get('removed', 0)} | {state} | {_inline(warns)} |"
        )
    return "\n".join(rows)


def _steps(defect: Dict) -> str:
    steps = defect.get("repro_steps") or defect.get("steps") or []
    if isinstance(steps, str):
        return _clean_text(steps) or "1. （工单未提供复现步骤）"
    if not steps:
        route = defect.get("route") or "/"
        return f"1. 打开 `{route}`\n2. （工单未提供更细复现步骤，需补充）"
    out = []
    for i, s in enumerate(steps, 1):
        if isinstance(s, dict):
            op = s.get("op", "")
            target = str(s.get("target", "") or s.get("selector", ""))
            value = s.get("value")
            line = f"{i}. `{op}` {target}" + (f" ← {value}" if value not in (None, "") else "")
        else:
            line = f"{i}. {_clean_text(s) or s}"
        out.append(line.rstrip())
    return "\n".join(out)


def _parse_front(text: str) -> Dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: Dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out
