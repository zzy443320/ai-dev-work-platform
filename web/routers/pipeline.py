"""缺陷修复流水线：run 与 probe 的同步版与 SSE 版。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from web.state import _RUN_STATE, _as_bool, _body_json, _fresh_pipeline
from web.streaming import _queue_stream

router = APIRouter()



# ------------------------------------------------------------------- run
def _summarize_results(pipeline, results: list) -> list:
    """/api/run 与 /api/run/stream 共用的结果摘要（前端渲染契约一致）。"""
    summary = []
    for r in results:
        prop = r.get("proposal") or {}
        patch = prop.get("patch") or r.get("patch") or {}
        summary.append({
            "proposal_id": prop.get("id"),
            "id": (r["defect"] or {}).get("id"),
            "title": str((r["defect"] or {}).get("title", ""))[:60],
            "category": (r["analysis"] or {}).get("category"),
            "locate_empty": bool((r["analysis"] or {}).get("locate_empty")),
            # 老提案没有该字段（analyzer 升级前落盘）→ 传 None，让前端按同样的
            # 信号词兜底重算；写死 False 会让历史提案的「非前端缺陷」结论丢失
            "non_frontend": (bool((r["analysis"] or {}).get("non_frontend"))
                             if "non_frontend" in (r["analysis"] or {}) else None),
            "status": prop.get("status"),
            "root_cause": (r["analysis"] or {}).get("root_cause", "")[:200],
            "files": patch.get("files", []),
            "gate_level": (prop.get("gate") or {}).get("level"),
            "gate_ok": (prop.get("gate") or {}).get("ok"),
            # agentic 修复循环的结论：运行结果表里就要能看出「沙箱真跑过」还是「没跑过」
            "agent_conclusion": (prop.get("agent") or {}).get("conclusion", ""),
            "agent_rounds": (prop.get("agent") or {}).get("rounds", 0),
            "agent_attempts": len((prop.get("agent") or {}).get("attempts") or []),
            "agent_needs_human": bool((prop.get("agent") or {}).get("needs_human")),
            "errors": (patch.get("errors") or [])[:4],
            "warnings": (patch.get("warnings") or [])[:4],
            "verify_status": (r.get("verify") or {}).get("status"),
            "kb_path": r.get("kb_path"),
            "source": r.get("source"),
        })
    return summary


def _run_opts(req_body: dict, **query) -> dict:
    return {
        # 示例工单已移除，mock 恒为 False（运行流水线只处理真实 ONES 工单）；
        # defects 允许调用方（测试/高级用法）显式注入工单内容，优先于拉取
        "mock": False,
        "defects": req_body.get("defects") or None,
        "dry_run": req_body.get("dry_run", False) if query.get("dry_run") is None else query["dry_run"],
        "limit": int(req_body.get("limit", 5) if query.get("limit") is None else query["limit"]),
        "defect_id": req_body.get("defect_id", query.get("defect_id")) or None,
        "skip_verify": bool(req_body.get("skip_verify", False)),
        "mine_only": _as_bool(req_body.get("mine_only"), True),
    }



@router.post("/api/run")
async def run_pipeline(
    req: Request,
    mock: Optional[bool] = Query(None),
    dry_run: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)

    body = await _body_json(req)
    opts = _run_opts(body, dry_run=dry_run, limit=limit, defect_id=defect_id)

    _RUN_STATE["running"] = True
    try:
        pipeline = _fresh_pipeline()
        # 必须跑在工作线程里：pipeline.run → verifier.capture → sync_playwright
        # 在 asyncio 事件循环内会直接抛 "Playwright Sync API inside the
        # asyncio loop"，导致页面验收/截图整体 error。
        results = await run_in_threadpool(
            pipeline.run,
            defect_id=opts["defect_id"], mock=opts["mock"],
            dry_run=opts["dry_run"], limit=opts["limit"],
            skip_verify=opts["skip_verify"], mine_only=opts["mine_only"],
            defects=opts["defects"],
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        _RUN_STATE["running"] = False

    summary = _summarize_results(pipeline, results)
    return {
        "count": len(summary),
        "ai_mode": pipeline.ai.mode,
        "wrote_any_files": False,
        "results": summary,
    }


@router.post("/api/run/stream")
async def run_pipeline_stream(
    req: Request,
    dry_run: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """「运行」的流式版本：SSE 实时推送阶段事件与 AI 思考/输出增量。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)
    body = await _body_json(req)
    opts = _run_opts(body, dry_run=dry_run, limit=limit, defect_id=defect_id)
    _RUN_STATE["running"] = True

    def worker(emit):
        pipeline = _fresh_pipeline()
        results = pipeline.run(
            defect_id=opts["defect_id"], mock=opts["mock"],
            dry_run=opts["dry_run"], limit=opts["limit"],
            skip_verify=opts["skip_verify"], mine_only=opts["mine_only"],
            defects=opts["defects"], emit=emit,
        )
        emit({"type": "done", "payload": {
            "count": len(results),
            "ai_mode": pipeline.ai.mode,
            "wrote_any_files": False,
            "results": _summarize_results(pipeline, results),
        }})

    return _queue_stream(asyncio.get_running_loop(), worker)


# ------------------------------------------------------------------ probe
@router.post("/api/probe")
async def probe_ones(
    req: Request,
    mock: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """试运行：只拉取 ONES 工单 + AI 定位，不生成提案、不进审批、不写任何文件。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)

    body = await _body_json(req)
    # 示例工单已移除，mock 恒为 False（试运行只拉真实 ONES 工单）
    m = False
    lim = int(body.get("limit", 5) if limit is None else limit)
    did = body.get("defect_id", defect_id) or None
    locate = bool(body.get("locate", True))
    days = int(body.get("days", 0) or 0)
    mine_only = _as_bool(body.get("mine_only"), True)

    _RUN_STATE["running"] = True
    try:
        pipeline = _fresh_pipeline()
        # 同步重活（ONES 网络请求 + AI 定位）丢进线程池，避免阻塞事件循环
        data = await run_in_threadpool(
            pipeline.probe, defect_id=did, mock=m, limit=lim,
            locate=locate, days=days, mine_only=mine_only,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        _RUN_STATE["running"] = False
    return data


@router.post("/api/probe/stream")
async def probe_ones_stream(
    req: Request,
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """「试运行」的流式版本：拉取 + AI 定位过程实时可见。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)
    body = await _body_json(req)
    lim = int(body.get("limit", 5) if limit is None else limit)
    did = body.get("defect_id", defect_id) or None
    locate = bool(body.get("locate", True))
    days = int(body.get("days", 0) or 0)
    mine_only = _as_bool(body.get("mine_only"), True)
    _RUN_STATE["running"] = True

    def worker(emit):
        pipeline = _fresh_pipeline()
        data = pipeline.probe(
            defect_id=did, mock=False, limit=lim,
            locate=locate, days=days, mine_only=mine_only, emit=emit,
        )
        emit({"type": "done", "payload": data})

    return _queue_stream(asyncio.get_running_loop(), worker)
