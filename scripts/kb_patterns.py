"""Stage 5b: derive *pattern* cards from the per-defect cards.

缺陷卡回答「这个工单当时怎么修的」；模式卡回答「这类缺陷反复出现时，共性
根因是什么、标准改法是什么」。前者是事实档案（一缺陷一条，不该合并），
后者才是可复用的资产（同类合并，复发次数是最重要的信号）。

聚类是**确定性规则**：症状族（静默失败 / 校验缺失 / …）× 模块前缀，不调 AI。
模式分组必须稳定可复现——否则同一批卡片每次重建都换分组，「复发 N 次」
这个信号就失去意义了。共性根因与改法的文字也是从案例里**抽**出来的
（谁说的、哪一例、是否已验证都标清楚），不做无根据的归纳。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .kb import _parse_front, _safe

PATTERN_SUBDIR = "_patterns"

# 症状族：(族 id, 中文名, 这类缺陷长什么样, 命中词)
# 命中词同时扫标题、根因、现象；标题命中权重 ×3（标题是人工写的，最可信）
_SYMPTOM_FAMILIES: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    ("silent_fail", "静默失败 / 无提示",
     "失败路径不抛错、不提示，接口或表单仍返回成功，缺陷只在下游（数据没写进去、"
     "任务没触发）才暴露。排查时前端抓包「看起来一切正常」，最容易漏。",
     ("静默", "无任何提示", "无提示", "没有任何提示", "无反馈", "无错误提示",
      "未提示", "不提示", "直接关闭", "保存成功但", "导入0条", "导入 0 条",
      "无任何报错", "提交无效")),
    ("validate_missing", "校验缺失 / 非法值可提交",
     "提交前校验不完整：必填项、互斥条件或长度上限没拦住，数据以非法状态落库，"
     "往往到下游使用（无法触发、展示为空）才炸。",
     ("必填", "校验", "未校验", "为空也能提交", "长度上限", "超出长度", "超长",
      "非法值", "没有拦截", "允许提交", "不拦截")),
    ("query_ineffective", "查询条件不生效",
     "查询参数发出去了但结果不对：筛选、时间范围或分页条件在匹配时丢失或被忽略，"
     "表现为「选了条件结果恒为 0」或「条件怎么改都一样」。",
     ("不生效", "筛选", "恒为0", "恒为 0", "查询不到", "结果为空", "查不到",
      "无数据", "过滤条件", "条件无效")),
    ("stale_data", "提交后数据未刷新",
     "写操作成功后没有重新拉取或更新本地状态，页面停留在旧数据，用户以为没生效。",
     ("不刷新", "未刷新", "没有刷新", "不更新", "列表没变", "缓存", "重新拉取")),
    ("time_offset", "时间口径不一致",
     "时间在前端展示或提交时经过了错误的时区 / 格式转换，与真实时间不一致。",
     ("早8小时", "早 8 小时", "时区", "时间戳", "时间显示", "时间格式",
      "相差8小时", "utc", "本地时间")),
    ("display_clip", "展示遮挡 / 截断",
     "布局或样式在特定条件下容不下内容，文案被遮挡、截断或错位。",
     ("遮挡", "被遮", "截断", "溢出", "显示不全", "错位", "看不全", "文字被",
      "复用布局")),
    ("parse_fallback", "解析兜底 / 误报告警",
     "解析或兜底逻辑把非预期输入当异常处理，产生噪音告警，甚至误判数据类型。",
     ("json.parse", "解析失败", "failed to parse", "console 告警", "console告警",
      "控制台告警", "兜底", "误报", "try/catch")),
    ("perm_route", "权限 / 路由不匹配",
     "权限点或路由配置与实际页面不一致，导致入口不可达或 404。",
     ("无权限", "权限点", "路由", "404", "白屏", "入口不可见")),
]

_UNCLASSIFIED = ("unclassified", "未归类（待人工归类）",
                 "现有词表没覆盖到这类症状，暂存此处；补充 _SYMPTOM_FAMILIES 后会被重新归族。",
                 ())

# 模块短名兜底：末段过于通用时，用「次末段-末段」更有辨识度
_MODULE_TAIL_GENERIC = {"src", "components", "views", "pages", "common", "utils"}

# 共性线索里要排掉的通用词（出现在每个文件路径里，没有区分度）
_NOISE_TOKENS = {
    "packages", "package", "src", "index", "common", "utils", "types", "type",
    "vue", "tsx", "false", "true", "null", "undefined", "string", "number",
    "object", "function", "return", "const", "import", "export", "default",
    "console", "error", "data", "props", "state", "value", "item", "items",
}

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


# ------------------------------------------------------------------ signals
def _section(text: str, name: str) -> str:
    """取 markdown 二级标题下的正文。"""
    m = re.search(r"^## " + re.escape(name) + r"\s*\n(.*?)(?=^## |\Z)",
                  text or "", re.S | re.M)
    return m.group(1).strip() if m else ""


def _card_paths(body: str) -> List[str]:
    """卡片里出现过的仓库文件路径（影响文件表 + 关联信息的嫌疑文件）。"""
    out: List[str] = []
    out += re.findall(r"\|\s*`([^`]+)`\s*\|", _section(body, "影响文件"))
    for line in _section(body, "关联信息").splitlines():
        line = line.strip()
        if line.startswith("- 嫌疑文件"):
            out += [p.strip() for p in line.split(":", 1)[-1].split(",")]
    return [p for p in dict.fromkeys(out) if p and p != "—" and "/" in p]


def _module_hint(paths: List[str]) -> str:
    """出现次数最多的「目录前缀」（最多 5 段，去掉文件名）。

    取 5 段而不是 4 段：`.../src/pages/schedule/api.ts` 的第 5 段才是
    真正有辨识度的模块名（schedule），截到 `src/pages` 只会得到泛泛的
    「pages」前缀，模式名里看不出是哪个模块反复出问题。
    """
    counter: Dict[str, int] = {}
    for p in paths:
        segs = [s for s in str(p).split("/") if s and s not in (".", "..")]
        if len(segs) < 2:
            continue
        hint = "/".join(segs[:-1][:5])
        if hint:
            counter[hint] = counter.get(hint, 0) + 1
    if not counter:
        return ""
    return max(counter.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]


def _short_module(hint: str) -> str:
    if not hint:
        return ""
    segs = hint.split("/")
    last = segs[-1]
    if last in _MODULE_TAIL_GENERIC and len(segs) >= 2:
        return f"{segs[-2]}-{last}"
    return last


def detect_symptom(title: str, root: str, desc: str) -> Dict:
    """按词表把一张缺陷卡归到某个症状族（命中分最高者）。"""
    hay_title = (title or "").lower()
    hay_rest = f"{root or ''} {desc or ''}".lower()
    best: Optional[Dict] = None
    for fam, name, blurb, words in _SYMPTOM_FAMILIES:
        score, hits = 0, []
        for w in words:
            wl = w.lower()
            c = 3 * hay_title.count(wl) + hay_rest.count(wl)
            if c:
                score += min(c, 6)
                hits.append(w)
        if score and (best is None or score > best["score"]):
            best = {"fam": fam, "name": name, "blurb": blurb,
                    "score": score, "hits": hits}
    # 单次非标题命中（分 1）不足以判定，宁可归到「未归类」也不要错族
    if best and best["score"] >= 2:
        return best
    return {"fam": _UNCLASSIFIED[0], "name": _UNCLASSIFIED[1],
            "blurb": _UNCLASSIFIED[2], "score": 0, "hits": []}


def _clip(s, n: int = 70) -> str:
    """表格单元格用：转义竖线、压掉换行、按长度截断（kb._inline 不带长度参数）。"""
    t = str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")
    return t[:n] + ("…" if len(t) > n else "")


def _dedupe_words(words: List[str]) -> List[str]:
    """去掉被更长词包含的短词（「无提示」⊂「无任何提示」）与空白差异重复项。"""
    norm = [(w, w.replace(" ", "")) for w in words]
    out: List[str] = []
    for i, (w, n) in enumerate(norm):
        if any(i != j and len(m) > len(n) and n in m for j, (_, m) in enumerate(norm)):
            continue
        if n in {m for _, m in out}:
            continue
        out.append((w, n))
    return [w for w, _ in out]


def _first_sentence(text: str, limit: int = 200) -> str:
    """取首句做摘要（根因往往是「X 处因为 Y 所以 Z」的长句）。"""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return ""
    m = re.search(r"[。；;!?！？]\s*", t)
    if m and m.end() > 12:
        t = t[:m.end()].strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def _common_tokens(roots: List[str], top: int = 8) -> List[str]:
    """出现在 ≥2 个案例根因里的标识符——「共性线索」用。"""
    counts: Dict[str, int] = {}
    for r in roots:
        for tok in set(_IDENT_RE.findall(r or "")):
            if tok.lower() in _NOISE_TOKENS:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    shared = [(t, c) for t, c in counts.items() if c >= 2]
    shared.sort(key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in shared[:top]]


# ------------------------------------------------------------------ build
def collect_cards(kb_dir: Path) -> List[Dict]:
    """读全部缺陷卡并抽取聚类所需的信号。"""
    cards: List[Dict] = []
    kb_dir = Path(kb_dir)
    if not kb_dir.exists():
        return cards
    for cat_dir in sorted(kb_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith((".", "_")):
            continue
        for f in sorted(cat_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            front = _parse_front(text)
            title = front.get("title", "") or f.stem
            root = _section(text, "根因")
            desc = _section(text, "现象")
            paths = _card_paths(text)
            sym = detect_symptom(title, root, desc)
            cards.append({
                "id": _clean_id(front.get("defect_id")) or f.stem,
                "category": front.get("category") or cat_dir.name,
                "title": title,
                "status": front.get("status") or "unknown",
                "priority": front.get("priority") or "",
                "commit": (front.get("commit") or "")[:8],
                "proposal_id": front.get("proposal_id") or "",
                "updated": front.get("updated") or front.get("created") or "",
                "root_cause": root,
                "desc": desc,
                "paths": paths,
                "module": _module_hint(paths),
                "symptom": sym,
                "prevention": _section(text, "预防措施"),
                "path": str(f),
            })
    return cards


def _clean_id(v) -> str:
    """frontmatter 里的 defect_id 可能是空串或带引号。"""
    return str(v or "").strip().strip('"').strip("'")


def cluster_cards(cards: List[Dict]) -> List[Dict]:
    """症状族 × 模块前缀 → 模式簇。

    族内先挑「多发子组」（≥2 例共享同一模块前缀）单独成模式，剩下的并成
    该族的通用模式——这样既保留了「同一模块反复出缺陷」这个强信号，
    又不会把三五个不同模块的同类症状拆成一堆只出现一次的模式。
    """
    by_fam: Dict[str, List[Dict]] = {}
    for c in cards:
        by_fam.setdefault(c["symptom"]["fam"], []).append(c)

    clusters: List[Dict] = []
    for fam, group in by_fam.items():
        mod_groups: Dict[str, List[Dict]] = {}
        for c in group:
            if c["module"]:
                mod_groups.setdefault(c["module"], []).append(c)
        hot = {h: g for h, g in mod_groups.items() if len(g) >= 2}
        taken = set()
        for h, g in sorted(hot.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            clusters.append({"fam": fam, "module": h, "cards": g})
            taken.update(id(c) for c in g)
        rest = [c for c in group if id(c) not in taken]
        if rest:
            clusters.append({"fam": fam, "module": "", "cards": rest})

    for cl in clusters:
        cl["cards"].sort(key=lambda c: (c["status"] != "applied", c["id"]))
        first = cl["cards"][0]["symptom"]
        cl["name"] = first["name"]
        cl["blurb"] = first["blurb"]
        short = _short_module(cl["module"])
        cl["id"] = _safe(cl["fam"] + (f"_{short}" if short else "")) or cl["fam"]
        if short:
            cl["name"] = f"{cl['name']} · {short}"
    clusters.sort(key=lambda c: (-len(c["cards"]), c["id"]))
    return clusters


# ------------------------------------------------------------------ render
def _render(cl: Dict) -> str:
    cards = cl["cards"]
    n = len(cards)
    applied = [c for c in cards if c["status"] == "applied"]
    cats = sorted({c["category"] for c in cards})
    ids = [c["id"] for c in cards]

    out: List[str] = []
    out.append("---")
    out.append(f"pattern_id: {cl['id']}")
    out.append(f"name: \"{cl['name']}\"")
    out.append(f"symptom: {cl['fam']}")
    out.append(f"module: {cl['module'] or ''}")
    out.append(f"recurrence: {n}")
    out.append(f"applied: {len(applied)}")
    out.append(f"categories: {', '.join(cats)}")
    out.append(f"defects: {', '.join(ids)}")
    out.append(f"updated: {datetime.now().isoformat(timespec='seconds')}")
    out.append("kb_version: 2")
    out.append("---")
    out.append("")
    out.append(f"# 模式：{cl['name']}")
    out.append("")
    tail = f" · 涉及模块 `{cl['module']}`" if cl["module"] else ""
    out.append(f"> 复发 **{n}** 次 · 已采纳 {len(applied)} 例{tail} · "
               f"分类：{'、'.join(cats)}")
    out.append("")

    out.append("## 这类缺陷长什么样")
    out.append(cl["blurb"])
    if cl["cards"][0]["symptom"]["hits"]:
        out.append("")
        out.append(f"- 命中症状词："
                   f"{'、'.join(_dedupe_words(cl['cards'][0]['symptom']['hits'])[:8])}")
    out.append("")

    out.append("## 共性根因")
    if applied:
        for c in applied[:3]:
            out.append(f"- ✅ **已验证**（[{c['id']}]({c['category']}/{c['id']}.md)，已采纳）："
                       f"{_first_sentence(c['root_cause']) or '（卡片未记录根因）'}")
    else:
        out.append("- ⚠️ 该模式下**尚无已采纳案例**，以下判断均未经验证：")
    for c in [x for x in cards if x["status"] != "applied"][:4]:
        out.append(f"- ⚠️ 待验证（[{c['id']}]({c['category']}/{c['id']}.md)，{c['status']}）："
                   f"{_first_sentence(c['root_cause']) or '（卡片未记录根因）'}")
    tokens = _common_tokens([c["root_cause"] for c in cards])
    if tokens:
        out.append(f"- 共性线索（≥2 例根因均提及）：{'、'.join(tokens)}")
    shared: Dict[str, int] = {}
    for c in cards:
        for p in set(c["paths"]):
            shared[p] = shared.get(p, 0) + 1
    hot_files = [p for p, k in sorted(shared.items(), key=lambda kv: -kv[1]) if k >= 2]
    if hot_files:
        out.append(f"- 反复涉及的文件：{'、'.join(f'`{p}`' for p in hot_files[:6])}")
    out.append("")

    out.append("## 处理方式")
    if applied:
        out.append("### 可复用改法（来自已采纳案例，可直接对照）")
        out.append("")
        out.append("| 案例 | 状态 | 涉及文件 | 改法要点 |")
        out.append("| --- | --- | --- | --- |")
        for c in applied[:5]:
            files = "、".join(f"`{p}`" for p in c["paths"][:3]) or "—"
            out.append(f"| {c['id']} | {c['status']} | {_clip(files)} | "
                       f"{_clip(_first_sentence(c['root_cause'], 160)) or '—'} |")
        if len(applied) >= 2:
            out.append("")
            out.append(f"> 本模式已有 {len(applied)} 例被采纳，改法经过多次验证，"
                       "同类缺陷可优先套用上表第一条。")
    else:
        best = cards[0]
        out.append(f"### 当前最佳判断（未验证）")
        out.append("")
        out.append(f"尚未有已采纳案例。参考置信度最高的一例 "
                   f"[{best['id']}]({best['category']}/{best['id']}.md)：")
        out.append("")
        out.append(_first_sentence(best["root_cause"], 300) or "（卡片未记录根因）")
        if best["paths"]:
            out.append("")
            out.append("嫌疑文件：" + "、".join(f"`{p}`" for p in best["paths"][:5]))
    prev = next((c["prevention"] for c in cards
                 if c["prevention"] and c["prevention"] != "(待补充)"), "")
    if prev:
        out.append("")
        out.append("### 预防措施（来自案例）")
        out.append("")
        out.append(_first_sentence(prev, 300))
    out.append("")

    out.append("## 典型案例")
    out.append("")
    out.append("| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |")
    out.append("| --- | --- | --- | --- | --- |")
    for c in cards:
        out.append(f"| [{c['id']}]({c['category']}/{c['id']}.md) | {c['category']} | "
                   f"{c['status']} | {_clip(c['title'], 46)} | "
                   f"{_clip(_first_sentence(c['root_cause'], 120)) or '—'} |")
    out.append("")

    out.append("## 复发提示")
    if n >= 3:
        out.append(f"- 同类缺陷已出现 **{n} 次**，这是**系统性缺陷**，不是个案："
                   "建议固化为评审 checklist 条目，并在该模块补一条单测或 lint 规则护栏。")
    elif n == 2:
        out.append("- 同类缺陷已出现 **2 次**，建议在评审 checklist 中补一条对应检查项。")
    else:
        out.append("- 首次出现，暂作模式种子保留。下次同类缺陷会自动归入本模式，"
                   "复发次数随之累加——**同一个模式被反复命中，就说明该处缺护栏**。")
    if applied:
        out.append(f"- 复用入口：优先看 [{applied[0]['id']}]({applied[0]['category']}/"
                   f"{applied[0]['id']}.md) 的补丁，其次再看本模式的其它案例差异。")
    out.append("")
    return "\n".join(out)


def rebuild_patterns(kb_dir, cards: Optional[List[Dict]] = None) -> List[Dict]:
    """整目录重建模式卡（幂等：不在当前聚类里的旧模式文件会被删掉）。

    `cards` 允许传 collect_cards() 的富化结果；传 None 或传原始 frontmatter
    （缺 symptom/module 这些聚类信号）时会自己从磁盘重新抽取，避免上层
    拿一组解析度不够的字典进来却静默聚出空结果。
    """
    kb_dir = Path(kb_dir)
    if not cards or any("symptom" not in c for c in cards):
        cards = collect_cards(kb_dir)
    clusters = cluster_cards(cards) if cards else []
    pat_dir = kb_dir / PATTERN_SUBDIR
    pat_dir.mkdir(parents=True, exist_ok=True)
    written = set()
    metas: List[Dict] = []
    for cl in clusters:
        path = pat_dir / f"{cl['id']}.md"
        path.write_text(_render(cl), encoding="utf-8")
        written.add(path.name)
        metas.append({
            "id": cl["id"],
            "name": cl["name"],
            "symptom": cl["fam"],
            "module": cl["module"],
            "recurrence": len(cl["cards"]),
            "applied": len([c for c in cl["cards"] if c["status"] == "applied"]),
            "categories": sorted({c["category"] for c in cl["cards"]}),
            "defects": [c["id"] for c in cl["cards"]],
        })
    for stale in pat_dir.glob("*.md"):
        if stale.name not in written:
            stale.unlink()
    (pat_dir / "_meta.json").write_text(
        json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                    "patterns": metas}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return metas


def render_index_section(metas: List[Dict]) -> List[str]:
    """INDEX.md 顶部的「模式总览」区块（按复发次数排序）。"""
    lines = [f"## 模式总览（{len(metas)}）", "",
             "> 同类缺陷的共性沉淀：复发次数越高，越说明该处缺护栏。"
             "模式是自动派生的，不要手工维护。", ""]
    if not metas:
        lines += ["（暂未形成模式）", ""]
        return lines
    lines += ["| 模式 | 复发 | 已采纳 | 涉及缺陷 | 模块 |",
              "| --- | --- | --- | --- | --- |"]
    for m in sorted(metas, key=lambda x: (-x["recurrence"], x["id"])):
        rel = f"{PATTERN_SUBDIR}/{m['id']}.md"
        lines.append(f"| [{_clip(m['name'], 40)}]({rel}) | **{m['recurrence']}** | "
                     f"{m['applied']} | {_clip('、'.join(m['defects']), 60)} | "
                     f"{_clip(m['module'] or '—', 40)} |")
    lines.append("")
    return lines
