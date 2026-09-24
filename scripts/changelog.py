# -*- coding: utf-8 -*-
"""更新日志解析 —— 把 `CHANGELOG.md` 变成结构化条目。

设计约束
--------
- **只读 + 无副作用**：不写文件、不调 AI。界面上的「哪次改了什么」必须与
  仓库里的文本一字不差，不能靠模型二次归纳（否则就成了另一个真相源）。
- **容错优先**：写日志是件顺手的事，格式太严会让人不愿意写。日期/时间/标签
  顺序任意、都允许缺省；认不出的行并入上一字段而不是丢弃。

条目格式（写 CHANGELOG.md 时请遵守）::

    ## 2026-09-23 16:20 · 新增 · 右上角「更新日志」入口
    - 内容：这次改了什么
    - 做法：具体怎么改的
    - 文件：web/server.py、web/static/app.js
    - 影响：需重启后端

头部三段用 `·` 或 `|` 分隔，其中「日期」形如 `2026-09-23`、「时间」形如
`16:20`、「标签」取自 :data:`TAGS`，剩下的部分拼成标题。
正文字段键支持中英文冒号；缩进/无冒号的续行会并入上一字段。
代码围栏（```）与 HTML 注释（<!-- -->）内的内容一律跳过。
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 标签集合：顺序即界面筛选器顺序（新增在最前，修复次之）
TAGS: Tuple[str, ...] = ("新增", "改进", "修复", "优化", "性能", "重构", "文档", "安全")

# 标签 → 前端样式后缀（前后端契约，改这里要同步 style.css 的 .ch-tag.t-*）
TAG_SLUG: Dict[str, str] = {
    "新增": "new",
    "改进": "improve",
    "修复": "fix",
    "优化": "optimize",
    "性能": "perf",
    "重构": "refactor",
    "文档": "docs",
    "安全": "security",
}

DEFAULT_TAG = "改进"

# 字段同义词 → 规范键
FIELD_KEYS: Dict[str, str] = {
    "内容": "content", "改了什么": "content", "做了什么": "content",
    "做法": "how", "怎么改的": "how", "怎么做的": "how",
    "实现": "how", "方案": "how", "修复做法": "how",
    "文件": "files", "涉及文件": "files", "改动文件": "files",
    "影响": "impact", "影响范围": "impact", "注意": "impact",
    "备注": "note", "说明": "note", "其他": "note",
}

# 规范键的展示名与顺序（前端也按这个顺序渲染）
FIELD_ORDER: Tuple[Tuple[str, str], ...] = (
    ("content", "内容"),
    ("how", "做法"),
    ("files", "文件"),
    ("impact", "影响"),
    ("note", "备注"),
)

_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DATETIME_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[\sT]+(\d{1,2}:\d{2})")
_TIME_RE = re.compile(r"^(\d{1,2}:\d{2})$")
_HEAD_SPLIT_RE = re.compile(r"\s*[·|｜]\s*")
_HEADER_RE = re.compile(r"^\s{0,3}##\s+(.*?)\s*$")
_H1_RE = re.compile(r"^\s{0,3}#\s+")
# 缩进续行：行首的 `·`/`-` 都只是书写习惯，统一换成界面用的 `• `
_SUB_RE = re.compile(r"^\s{2,}(?:[-*+•·]\s*)?(.+?)\s*$")
_FIELD_RE = re.compile(r"^\s*[-*+]\s*([^:：]{1,14})[:：]\s*(.*)$")
_FILE_SPLIT_RE = re.compile(r"[、,，;；\s]+")
_RESTART_HINT = "重启"


def _entry_id(date: str, time: str, title: str) -> str:
    """内容寻址 id：同一条日志怎么重排都不变（界面靠它判断「有没有新的」）。"""
    raw = f"{date}|{time}|{title}".encode("utf-8")
    return f"{date or '0000-00-00'}-{hashlib.md5(raw).hexdigest()[:8]}"


def _norm_date(y: str, m: str, d: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _parse_head(head: str) -> Dict[str, str]:
    """解析 `## 2026-09-23 16:20 · 新增 · 标题` 这一行。"""
    parts = [p for p in _HEAD_SPLIT_RE.split(head or "") if p.strip()]
    date = time = tag = ""
    rest: List[str] = []
    for part in parts:
        part = part.strip()
        if not date:
            m = _DATETIME_RE.match(part)
            if m:
                date = _norm_date(m.group(1), m.group(2), m.group(3))
                time = m.group(4)
                continue
            m = _DATE_RE.match(part)
            if m:
                date = _norm_date(m.group(1), m.group(2), m.group(3))
                continue
        if not time and _TIME_RE.match(part):
            time = part
            continue
        if not tag and part in TAGS:
            tag = part
            continue
        rest.append(part)
    return {"date": date, "time": time, "tag": tag, "title": " · ".join(rest).strip()}


def split_files(value: str) -> List[str]:
    """`文件：a.py、b.js` → ['a.py', 'b.js']（去掉反引号与项目符号）。"""
    out: List[str] = []
    for tok in _FILE_SPLIT_RE.split(value or ""):
        tok = tok.strip().strip("`").strip()
        if tok and tok not in out:
            out.append(tok)
    return out


def parse_changelog(text: str) -> List[Dict[str, str]]:
    """解析 CHANGELOG.md 全文，返回**按文件顺序**（旧 → 新）的条目列表。"""
    entries: List[Dict[str, str]] = []
    cur: Optional[Dict[str, str]] = None
    last_key: Optional[str] = None
    in_fence = False
    in_comment = False

    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()

        # 代码围栏：里面的 `## xxx` 是格式示例，不是条目
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # HTML 注释：格式说明区
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue

        m = _HEADER_RE.match(line)
        if m:
            cur = _parse_head(m.group(1))
            cur["_extra"] = ""
            entries.append(cur)
            last_key = None
            continue
        if _H1_RE.match(line):
            continue
        if cur is None:
            continue

        m = _FIELD_RE.match(line)
        if m:
            key = FIELD_KEYS.get(m.group(1).strip())
            val = m.group(2).strip()
            if key:
                cur[key] = _join(cur.get(key), val)
                last_key = key
            else:
                # 认不出的键原样保留，别让用户白写一行
                cur["_extra"] = _join(cur["_extra"], f"{m.group(1).strip()}：{val}")
                last_key = "_extra"
            continue

        if not line.strip():
            continue

        m = _SUB_RE.match(line)
        if m and last_key:
            cur[last_key] = _join(cur[last_key], f"• {m.group(1).strip()}")
        elif last_key:
            cur[last_key] = _join(cur[last_key], line.strip())
    return entries


def _join(old: Optional[str], new: str) -> str:
    old = (old or "").strip()
    new = (new or "").strip()
    if not old:
        return new
    if not new:
        return old
    return f"{old}\n{new}"


def finalize(entry: Dict[str, str], seq: int = 0) -> Dict[str, str]:
    """补齐默认值、派生展示字段。"""
    out = {k: (entry.get(k) or "") for k in
           ("date", "time", "tag", "title", "content", "how", "files",
            "impact", "note", "_extra")}
    out["title"] = out["title"] or "（未命名改动）"
    out["tag"] = out["tag"] if out["tag"] in TAGS else DEFAULT_TAG
    out["tag_slug"] = TAG_SLUG.get(out["tag"], "other")
    out["when"] = " ".join(x for x in (out["date"], out["time"]) if x)
    out["files_list"] = split_files(out["files"])
    # 「需重启后端」是用户最该先看到的一句话，单独标出来。
    # 只看「影响/备注」——正文里提到「重启」多半是在讲别的（如重启浏览器通道）
    out["restart"] = any(_RESTART_HINT in (out.get(k) or "")
                         for k in ("impact", "note"))
    out["id"] = _entry_id(out["date"], out["time"], out["title"])
    out["seq"] = seq
    return out


def _sort_key(e: Dict[str, str]):
    # 文件是「旧 → 新」追加写的，所以同一时刻里后写的更靠前
    return (e.get("date") or "", e.get("time") or "", e.get("seq") or 0)


def build_payload(text: str, updated_at: str = "", source: str = "") -> Dict:
    """给前端用的完整载荷（条目为**新 → 旧**）。"""
    raw = parse_changelog(text)
    entries = [finalize(e, i) for i, e in enumerate(raw)]
    entries.sort(key=_sort_key, reverse=True)

    counts: Dict[str, int] = {}
    for e in entries:
        counts[e["tag"]] = counts.get(e["tag"], 0) + 1

    groups: List[Dict] = []
    for e in entries:
        day = e["date"] or "（未标日期）"
        if not groups or groups[-1]["date"] != day:
            groups.append({"date": day, "entries": []})
        groups[-1]["entries"].append(e)

    return {
        "source": source,
        "updated_at": updated_at,
        "total": len(entries),
        "days": len(groups),
        "latest": entries[0]["id"] if entries else "",
        "tags": [{"name": t, "slug": TAG_SLUG[t], "count": counts.get(t, 0)}
                 for t in TAGS if counts.get(t)],
        "field_order": [{"key": k, "label": v} for k, v in FIELD_ORDER],
        "entries": entries,
        "groups": groups,
    }


def load_changelog(path, limit: Optional[int] = None,
                   tag: Optional[str] = None) -> Dict:
    """读文件并解析；文件不存在时返回空载荷而不是抛错（界面显示空态）。"""
    p = Path(path)
    text, updated_at = "", ""
    if p.is_file():
        text = p.read_text(encoding="utf-8")
        updated_at = datetime.fromtimestamp(p.stat().st_mtime).isoformat(
            timespec="seconds")
    payload = build_payload(text, updated_at=updated_at, source=p.name)
    if tag:
        entries = [e for e in payload["entries"] if e["tag"] == tag]
        groups: List[Dict] = []
        for e in entries:
            day = e["date"] or "（未标日期）"
            if not groups or groups[-1]["date"] != day:
                groups.append({"date": day, "entries": []})
            groups[-1]["entries"].append(e)
        payload["entries"] = entries
        payload["groups"] = groups
        payload["total"] = len(entries)
        payload["filtered_by"] = tag
    if limit and limit > 0:
        payload["entries"] = payload["entries"][:limit]
        payload["total"] = len(payload["entries"])
    return payload
