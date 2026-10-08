# -*- coding: utf-8 -*-
"""同类历史缺陷的先例：从既往提案里挑出「这种问题当时是怎么改的、后来到底有没有用」。

为什么值得做：这套系统每修一条缺陷都在 `proposals/` 与知识库里留一份完整档案（补丁
原文、验证结论、人工是采纳还是拒绝），但修复循环从来不看它们——同一个模块反复出缺陷，
模型每次都是从零猜一遍，人工拒绝过的改法还会被再次端上来。先例是把已经攒下的资产接回
回路里。

三条刻意的设计：

  1. **连「被拒绝的」一起给**，并标明结局。只喂成功案例会让模型重蹈你们已经否掉的改法，
     那比没有先例更浪费；被拒绝的先例带着「为什么被拒」的备注，价值常常更高。
  2. **本工单自己上一次跑的记录也在里面**。同一工单重跑时，上一轮失败的改法是最该避开的
     信息，不能因为「同一张单」就过滤掉。
  3. **注入必须封顶**。历史 diff 动辄几十 KB，全塞进去等于用先例挤掉当前代码的上下文，
     反而更容易改错。默认 3 条 / 6000 字符，超了先丢相关度最低的那条的补丁正文。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_LIMIT = 3
DEFAULT_MAX_CHARS = 6000
SCAN_CAP = 150          # 最多回看最近这么多份提案（按 mtime 倒序）
PATCH_CHARS = 1400      # 单条先例里补丁原文的上限

_APPLIED = ("applied",)
_REJECTED = ("rejected", "apply_failed")

_PATH_TOKEN_STOP = {"src", "lib", "app", "index", "components", "component", "components",
                    "packages", "apps", "common", "utils", "views", "view", "pages",
                    "src", "test", "tests", "dist", "node", "modules", "index"}


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _tokens(defect: Dict) -> Tuple[set, set]:
    """从当前工单里抽出「可比对的词」与「可比对的目录段」。"""
    body = " ".join(_norm(defect.get(k)) for k in ("title", "description", "desc"))
    body = re.sub(r"<[^>]+>", " ", body)
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]{3,}", body)}
    cjk = {body[i:i + 4] for i in range(max(0, len(body) - 3))
           if all("\u4e00" <= ch <= "\u9fff" for ch in body[i:i + 4])}
    paths = {p for p in re.findall(r"[\w-]+(?:/[\w-]+)+", body)}
    segs = set()
    for p in paths:
        for seg in p.split("/"):
            if len(seg) > 3 and seg.lower() not in _PATH_TOKEN_STOP:
                segs.add(seg.lower())
    extra = set()
    for kw in (defect.get("keywords") or []):
        extra.add(_norm(kw).lower())
    return (words | cjk | {e for e in extra if e}, segs)


def _proposal_brief(path: Path) -> Optional[Dict]:
    """读一份提案，折成先例卡片。解析失败或结构不全就返回 None（跳过，不报错）。"""
    try:
        p = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(p, dict) or not p.get("defect"):
        return None
    analysis = p.get("analysis") or {}
    patch = p.get("patch") or {}
    agent = p.get("agent") or {}
    decision = p.get("decision") or {}
    files = [c.get("file_path") for c in (patch.get("changes") or [])
             if c.get("file_path")] or list(patch.get("files") or [])
    patch_text = _norm(analysis.get("patch_canonical") or patch.get("patch_text"))
    if not patch_text:
        patch_text = _norm((patch.get("combined_diff") or ""))[:PATCH_CHARS]
    repro = agent.get("repro") or {}
    status = str(p.get("status") or "")
    if decision.get("approved") is False:
        outcome = "rejected"
    elif status in _APPLIED:
        outcome = "applied"
    elif status in _REJECTED:
        outcome = "rejected"
    elif status in ("pending", "gate_failed", "invalid"):
        outcome = "pending"
    else:
        outcome = status or "unknown"
    return {
        "proposal_id": str(p.get("id") or path.stem),
        "defect_id": str((p.get("defect") or {}).get("id") or ""),
        "title": _norm((p.get("defect") or {}).get("title"))[:120],
        "category": _norm(analysis.get("category"))[:20],
        "root_cause": _norm(analysis.get("root_cause"))[:300],
        "files": [str(f) for f in files[:8]],
        "keywords": [_norm(k)[:40] for k in (analysis.get("keywords") or [])[:12]],
        "outcome": outcome,
        "decision_note": _norm(decision.get("note"))[:200],
        "conclusion": _norm(agent.get("conclusion"))[:40],
        "verified_via": _norm(agent.get("verified_via"))[:30],
        "repro_path": _norm(repro.get("path"))[:120],
        "repro_status": _norm(repro.get("status"))[:20],
        "patch_text": patch_text[:PATCH_CHARS],
        "created": str(p.get("created") or ""),
        "mtime": path.stat().st_mtime,
    }


def score(brief: Dict, words: set, segs: set) -> int:
    """相关度打分。目录段命中权重最高——同一模块的缺陷改法最可迁移。

    ⚠ 结局与证据只能**修饰**一个已经非零的相关度，不能当基础分：否则任何一条
    「人工采纳过」的提案都会凭 +2 越过门槛，把完全无关的先例喂进 prompt
    （登录页图标的改法被拿去修列表崩溃，就是这么来的）。
    """
    base = 0
    kws = {k.lower() for k in brief.get("keywords") or []}
    title = (brief.get("title") or "").lower()
    for w in words:
        lw = w.lower()
        if lw in kws:
            base += 3
        elif len(lw) > 4 and lw in title:
            base += 2
    blob = " ".join(brief.get("files") or []).lower()
    for seg in segs:
        if seg in blob:
            base += 4
        elif seg in title:
            base += 1
    if base <= 0:
        return 0
    if brief.get("outcome") == "applied":
        base += 2                   # 人工确认过的改法更值得参考
    elif brief.get("outcome") == "rejected":
        base += 1                   # 被否的也要给，但要能排得进去
    if brief.get("conclusion") in ("verified", "checks_pass"):
        base += 1
    return base


def load_precedents(proposals_dir: str, defect: Dict, *, limit: int = DEFAULT_LIMIT,
                    exclude_proposal: str = "") -> List[Dict]:
    """挑出与当前工单最相关的历史提案先例（含本工单自己上一次的尝试）。"""
    base = Path(proposals_dir or "")
    if not base.is_dir() or limit <= 0:
        return []
    files = sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)[:SCAN_CAP]
    words, segs = _tokens(defect or {})
    did = str((defect or {}).get("id") or "")
    out: List[Dict] = []
    for f in files:
        brief = _proposal_brief(f)
        if not brief or brief["proposal_id"] == exclude_proposal:
            continue
        # 先排除「同一次运行的自我引用」：新提案在 proposals/ 里落盘后，重跑同一条工单
        # 会把它自己当先例喂回去——那是回声，不是参考
        if brief["defect_id"] == did and brief["outcome"] == "pending":
            continue
        brief["same_defect"] = brief["defect_id"] == did
        brief["score"] = score(brief, words, segs)
        out.append(brief)
    out.sort(key=lambda b: (b["score"], b["mtime"]), reverse=True)
    keep = [b for b in out if b["score"] > 0][:limit]
    return keep


def render_precedents(items: List[Dict], max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """渲染成注入 prompt 的文本，总长封顶。

    超预算时从**相关度最低**的那条开始砍它的补丁正文，保留结论与结局：先例最值钱的
    部分是「当时改法后来有没有被认」，具体 diff 排第二。
    """
    if not items:
        return ""
    ordered = list(items)                     # 调用方已按 score 倒序
    blocks = [_render_one(i, b, with_patch=True) for i, b in enumerate(ordered, 1)]
    text = "\n\n".join(blocks)
    if len(text) <= max_chars:
        return text
    for idx in range(len(ordered) - 1, -1, -1):   # 从最低分的开始去正文
        blocks[idx] = _render_one(idx + 1, ordered[idx], with_patch=False)
        text = "\n\n".join(blocks)
        if len(text) <= max_chars:
            return text
    # 连正文都去了还超：只能整体截断，并明确告诉读者被截了
    return text[:max_chars] + "\n…（先例过长，已截断）"


def _render_one(no: int, b: Dict, with_patch: bool) -> str:
    outcome = {"applied": "人工采纳", "rejected": "人工拒绝"}.get(
        b.get("outcome") or "", "未采纳/待审")
    head = (f"### 先例 {no}：{b.get('title') or '(无标题)'}"
            f"（工单 {b.get('defect_id') or '-'}；"
            + ("本工单上一次尝试；" if b.get("same_defect") else "")
            + f"结局 {outcome}")
    if b.get("conclusion"):
        head += f"；循环结论 {b['conclusion']}"
        if b.get("verified_via"):
            head += f"（证据 {b['verified_via']}）"
    if b.get("repro_path"):
        head += f"；复现用例 {b['repro_path']}[{b.get('repro_status') or '-'}]"
    head += "）"
    lines = [head]
    if b.get("decision_note"):
        lines.append(f"人工备注：{b['decision_note']}")
    if b.get("root_cause"):
        lines.append(f"当时的根因：{b['root_cause']}")
    if b.get("files"):
        lines.append("改动文件：" + "、".join(b["files"]))
    if with_patch and b.get("patch_text"):
        lines.append("补丁原文：\n```\n" + b["patch_text"] + "\n```")
    elif b.get("patch_text"):
        lines.append("（补丁原文因长度上限已省略）")
    return "\n".join(lines)
