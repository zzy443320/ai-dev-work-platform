"""只读观测面：统计、知识库浏览（INDEX/卡片/模式）、更新日志、用量、工单列表。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from web.state import CHANGELOG_FILE, KB_DIR, _load_settings
from scripts import usage  # noqa: E402
from scripts.mock_data import SAMPLE_DEFECTS  # noqa: E402
from scripts.ones_fetcher import OnesClient  # noqa: E402
from scripts.usage import KIND_LABELS as USAGE_KIND_LABELS  # noqa: E402
from scripts.textutil import parse_frontmatter as _parse_frontmatter

router = APIRouter()



# ------------------------------------------------------------------ stats
@router.get("/api/stats")
async def stats():
    stats_path = KB_DIR / "_stats.json"
    if not stats_path.exists():
        return {"categories": {}, "cards": 0}
    try:
        return json.loads(stats_path.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/index")
async def kb_index():
    idx = KB_DIR / "INDEX.md"
    return {"content": idx.read_text(encoding="utf-8") if idx.exists() else ""}


@router.get("/api/cards")
async def list_cards():
    if not KB_DIR.exists():
        return []
    cards = []
    for cat_dir in sorted(KB_DIR.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith((".", "_")):
            continue
        for f in sorted(cat_dir.glob("*.md")):
            front = _parse_frontmatter(f.read_text(encoding="utf-8"))
            cards.append({
                "category": cat_dir.name,
                "id": f.stem,
                "title": front.get("title", f.stem),
                "priority": front.get("priority", ""),
                "status": front.get("status", ""),
                "gate_level": front.get("gate_level", ""),
                "commit": (front.get("commit") or "")[:8],
                "proposal_id": front.get("proposal_id", ""),
                "created": front.get("created", ""),
                "updated": front.get("updated", front.get("created", "")),
            })
    return sorted(cards, key=lambda c: (c["category"], c["id"]))


@router.get("/api/card/{category}/{card_id}")
async def get_card(category: str, card_id: str):
    path = KB_DIR / category / f"{card_id}.md"
    if not path.exists():
        raise HTTPException(404, "card not found")
    content = path.read_text(encoding="utf-8")
    body = content.split("---", 2)[-1].strip() if content.startswith("---") else content
    return {
        "front": _parse_frontmatter(content),
        "body": body,
        "category": category,
        "id": card_id,
    }


@router.get("/api/patterns")
async def list_patterns():
    """模式列表（同类缺陷的共性沉淀，见 scripts/kb_patterns.py）。

    优先读 _patterns/_meta.json——它跟模式卡一起由重建流程写出，省掉逐张
    解析 markdown；文件缺失（老知识库、模式层还没跑过）时退化为扫描目录，
    再没有就返回空列表让前端显示空态。
    """
    pat_dir = KB_DIR / "_patterns"
    meta_path = pat_dir / "_meta.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            items = data.get("patterns") or []
            return sorted(items, key=lambda p: (-(p.get("recurrence") or 0), p.get("id", "")))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    if not pat_dir.exists():
        return []
    out = []
    for f in sorted(pat_dir.glob("*.md")):
        front = _parse_frontmatter(f.read_text(encoding="utf-8"))
        out.append({
            "id": front.get("pattern_id") or f.stem,
            "name": front.get("name") or f.stem,
            "symptom": front.get("symptom", ""),
            "module": front.get("module", ""),
            "recurrence": int(front.get("recurrence") or 0),
            "applied": int(front.get("applied") or 0),
            "categories": [c for c in (front.get("categories") or "").split(",") if c],
            "defects": [d.strip() for d in (front.get("defects") or "").split(",") if d.strip()],
        })
    return sorted(out, key=lambda p: (-p["recurrence"], p["id"]))


@router.get("/api/pattern/{pattern_id}")
async def get_pattern(pattern_id: str):
    path = KB_DIR / "_patterns" / f"{pattern_id}.md"
    if not path.exists():
        raise HTTPException(404, "pattern not found")
    content = path.read_text(encoding="utf-8")
    body = content.split("---", 2)[-1].strip() if content.startswith("---") else content
    front = _parse_frontmatter(content)
    # 关联缺陷的可点链接需要「卡片 id → 分类目录」，模式卡的 frontmatter
    # 只有缺陷 id，所以这里扫一遍卡片目录补上。
    cases = []
    wanted = [d.strip() for d in (front.get("defects") or "").split(",") if d.strip()]
    if wanted:
        found = {}
        for cat_dir in sorted(KB_DIR.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith((".", "_")):
                continue
            for f in sorted(cat_dir.glob("*.md")):
                if f.stem in wanted:
                    cf = _parse_frontmatter(f.read_text(encoding="utf-8"))
                    found[f.stem] = {
                        "id": f.stem,
                        "category": cat_dir.name,
                        "title": cf.get("title") or f.stem,
                        "status": cf.get("status", ""),
                    }
        cases = [found[d] for d in wanted if d in found]
    return {"front": front, "body": body, "id": pattern_id, "cases": cases}


@router.get("/api/changelog")
async def get_changelog(limit: Optional[int] = None, tag: Optional[str] = None):
    """更新日志（解析项目根 CHANGELOG.md，只读）。

    界面右上角「更新」入口读的就是这里。数据源是仓库里的纯文本，不做模型
    二次归纳——否则「改了什么」会出现第二个真相源，与文件对不上。
    文件缺失时返回空载荷（前端显示空态），不报错。
    """
    from scripts.changelog import load_changelog
    try:
        return await run_in_threadpool(load_changelog, CHANGELOG_FILE, limit, tag)
    except Exception as e:  # 日志格式异常不该让整个页面挂掉
        return JSONResponse({"error": f"解析更新日志失败: {e}", "entries": [],
                             "groups": [], "total": 0}, status_code=200)


@router.get("/api/usage/report")
async def usage_report(days: int = Query(30, ge=1, le=365),
                       granularity: str = Query("day")):
    """Token 用量报表（只读）。

    数据源是 `usage/usage.jsonl` 这个 append-only 账本——每次真实模型调用一行，
    由 `AIModel._record` 写入。聚合口径：

    - 网关回了 usage → 真值；没回（流式最常见）→ 按字符估算并打 `estimated` 标记。
      界面必须把两者区分显示，不能把估算值伪装成账单。
    - mock 调用不入账（不消耗 token），所以面板上的"调用次数"是真实调用次数。
    - 分桶区间内的**空桶照常返回**，前端才能画出真实的趋势（有断档就有断档）。
    """
    if granularity not in usage.GRANULARITIES:
        raise HTTPException(400, f"granularity 必须是 {'/'.join(usage.GRANULARITIES)}")
    try:
        rep = await run_in_threadpool(usage.report, days, granularity)
    except Exception as e:  # 统计是旁路能力，坏账本不该让页面报错
        return JSONResponse({"error": f"读取用量账本失败: {e}", "buckets": [],
                             "kinds": [], "models": [], "tasks": [], "recent": [],
                             "totals": {}, "all_time": {}, "today": {},
                             "this_week": {}, "this_month": {},
                             "range": {"days": days, "granularity": granularity}},
                            status_code=200)
    rep["kind_labels"] = USAGE_KIND_LABELS
    return rep


@router.get("/api/usage/kinds")
async def usage_kinds():
    """任务类型的中文名（前端图例用；避免中英文两份标签各自漂移）。"""
    return {"labels": USAGE_KIND_LABELS, "granularities": list(usage.GRANULARITIES),
            "ledger": str(usage.ledger_path())}


@router.get("/api/defects")
async def list_defects(mock: bool = False, limit: int = 10):
    """示例工单已移除：mock=true 现在返回空列表，绝不返回假工单。

    注意：这里没有配置 ONES 凭据时会直接回空列表，**不会**去调远端
    （否则未配置时前端会挂在超时上等待）。真正的拉取走 /api/probe。
    """
    if mock:
        return SAMPLE_DEFECTS[:limit]
    s = _load_settings()
    if not (s.get("ones") or {}).get("base_url"):
        return []
    try:
        o = s["ones"]
        return OnesClient(o.get("base_url", ""), token=o.get("token", ""),
                          project_uuid=o.get("project_uuid", ""),
                          team_uuid=o.get("team_uuid", ""),
                          email=o.get("email", ""),
                          password=o.get("password", "")).fetch_defects(limit=limit)
    except Exception as e:
        raise HTTPException(502, f"ONES 拉取失败: {e}")



# ------------------------------------------------------------------ helpers

