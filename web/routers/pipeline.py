"""缺陷修复流水线：run 与 probe 的同步版与 SSE 版。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。

占用槽位走 web.state 的**租约**（`_acquire_run` / `_release_run`）：一次申请同时
回答「是谁在跑、跑到第几条、跑了多久」，并带回一个取消事件传给流水线做协作式中断。
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from web.state import (_acquire_run, _as_bool, _body_json, _busy_response,
                       _fresh_pipeline, _progress_emitter, _release_run,
                       _request_run_cancel, _run_snapshot)
from web.streaming import _queue_stream

router = APIRouter()


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


def _opts_label(opts: dict) -> str:
    """把运行参数压成一句人话，写进租约——占用别人的槽位时得说清跑的是什么口径。"""
    parts = []
    if opts.get("defect_id"):
        parts.append(f"指定工单 {opts['defect_id']}")
    else:
        parts.append(f"最近 {opts.get('limit')} 条")
        parts.append("只看我负责的" if opts.get("mine_only") else "全部负责人")
    parts.append("跳过页面验收" if opts.get("skip_verify") else "含页面验收")
    return " · ".join(parts)


def _probe_label(lim: int, did, mine_only: bool, locate: bool) -> str:
    """试运行租约的参数口径。

    刻意**不重复**「拉取 + AI 定位」——kind_label 已经说了这件事，再拼一遍
    占用条就是一行长尾冗余（实测那行会折成两行还看不清重点）。
    """
    scope = f"指定工单 {did}" if did else \
        f"最近 {lim} 条 · " + ("只看我负责的" if mine_only else "全部负责人")
    return scope if locate else scope + " · 不定位"



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



@router.post("/api/run")
async def run_pipeline(
    req: Request,
    mock: Optional[bool] = Query(None),
    dry_run: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    body = await _body_json(req)
    opts = _run_opts(body, dry_run=dry_run, limit=limit, defect_id=defect_id)

    # 检查与申请在同一次调用里完成（原先 `if running` 与 `running = True` 分作两步，
    # 并发请求能双双通过）。拿到的是这次租约的取消事件，交给流水线做协作式中断。
    cancel = _acquire_run("run", label=_opts_label(opts),
                          source=str(body.get("source") or ""),
                          defect_id=opts["defect_id"] or "")
    if cancel is None:
        return _busy_response("运行流水线")

    # 非流式路径以前完全不传 emit：外部脚本发起的批量运行在界面上只剩一个
    # 「有流水线在运行」的布尔。现在把进度镜像进租约，界面才看得见跑到哪条工单。
    mirror = _progress_emitter(cancel)
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
            emit=mirror, should_cancel=cancel.is_set,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        _release_run(cancel)

    summary = _summarize_results(pipeline, results)
    return {
        "count": len(summary),
        "ai_mode": pipeline.ai.mode,
        "wrote_any_files": False,
        # 被「停止」打断时如实说：批量脚本据此知道结果不是全集
        "stopped": cancel.is_set(),
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
    body = await _body_json(req)
    opts = _run_opts(body, dry_run=dry_run, limit=limit, defect_id=defect_id)
    cancel = _acquire_run("run", label=_opts_label(opts),
                          source=str(body.get("source") or ""),
                          defect_id=opts["defect_id"] or "")
    if cancel is None:
        return _busy_response("运行流水线")

    def worker(emit):
        # 先镜像进租约再转发给 SSE：客户端断开（刷新页面）时 SSE 这条路断了，
        # 但界面轮询 /api/run/status 仍要知道这条流水线还在跑哪一单。
        forward = _progress_emitter(cancel, forward=emit)
        pipeline = _fresh_pipeline()
        results = pipeline.run(
            defect_id=opts["defect_id"], mock=opts["mock"],
            dry_run=opts["dry_run"], limit=opts["limit"],
            skip_verify=opts["skip_verify"], mine_only=opts["mine_only"],
            defects=opts["defects"], emit=forward, should_cancel=cancel.is_set,
        )
        emit({"type": "done", "payload": {
            "count": len(results),
            "ai_mode": pipeline.ai.mode,
            "wrote_any_files": False,
            "stopped": cancel.is_set(),
            "results": _summarize_results(pipeline, results),
        }})

    return _queue_stream(asyncio.get_running_loop(), worker,
                         on_finish=lambda: _release_run(cancel))


# ------------------------------------------------------------------ probe
@router.post("/api/probe")
async def probe_ones(
    req: Request,
    mock: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """试运行：只拉取 ONES 工单 + AI 定位，不生成提案、不进审批、不写任何文件。"""
    body = await _body_json(req)
    # 示例工单已移除，mock 恒为 False（试运行只拉真实 ONES 工单）
    m = False
    lim = int(body.get("limit", 5) if limit is None else limit)
    did = body.get("defect_id", defect_id) or None
    locate = bool(body.get("locate", True))
    days = int(body.get("days", 0) or 0)
    mine_only = _as_bool(body.get("mine_only"), True)

    cancel = _acquire_run("probe", label=_probe_label(lim, did, mine_only, locate),
                          source=str(body.get("source") or ""), defect_id=did or "")
    if cancel is None:
        return _busy_response("试运行")

    mirror = _progress_emitter(cancel)
    try:
        pipeline = _fresh_pipeline()
        # 同步重活（ONES 网络请求 + AI 定位）丢进线程池，避免阻塞事件循环
        data = await run_in_threadpool(
            pipeline.probe, defect_id=did, mock=m, limit=lim,
            locate=locate, days=days, mine_only=mine_only,
            emit=mirror, should_cancel=cancel.is_set,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        _release_run(cancel)
    data["stopped"] = cancel.is_set()
    return data


@router.post("/api/probe/stream")
async def probe_ones_stream(
    req: Request,
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """「试运行」的流式版本：拉取 + AI 定位过程实时可见。"""
    body = await _body_json(req)
    lim = int(body.get("limit", 5) if limit is None else limit)
    did = body.get("defect_id", defect_id) or None
    locate = bool(body.get("locate", True))
    days = int(body.get("days", 0) or 0)
    mine_only = _as_bool(body.get("mine_only"), True)
    cancel = _acquire_run("probe", label=_probe_label(lim, did, mine_only, locate),
                          source=str(body.get("source") or ""), defect_id=did or "")
    if cancel is None:
        return _busy_response("试运行")

    def worker(emit):
        forward = _progress_emitter(cancel, forward=emit)
        pipeline = _fresh_pipeline()
        data = pipeline.probe(
            defect_id=did, mock=False, limit=lim,
            locate=locate, days=days, mine_only=mine_only,
            emit=forward, should_cancel=cancel.is_set,
        )
        data["stopped"] = cancel.is_set()
        emit({"type": "done", "payload": data})

    return _queue_stream(asyncio.get_running_loop(), worker,
                         on_finish=lambda: _release_run(cancel))


# ------------------------------------------------- 占用槽位：可见 + 可停
@router.get("/api/run/status")
async def run_status():
    """现在占着流水线槽位的是谁、跑到哪一单、跑了多久。空闲时只有 `running: false`。"""
    return _run_snapshot()


@router.post("/api/run/cancel")
async def run_cancel(req: Request):
    """停止当前运行（缺陷流水线 / 任务 / 长任务作业共用这一个槽位）。

    默认是**协作式**的：置起取消事件，流水线在「工单之间」与「agentic 每一轮之间」
    两个边界收口。模型调用与沙箱命令都跑在子进程里，中途硬砍会留下半截沙箱和半截提案。
    传 `{"force": true}` 额外立刻解除占用——后台线程仍会收尾（还会落它自己的提案），
    但界面马上就能发起下一次运行。这是「线程挂死、只能重启进程」那个死局的逃生口。
    """
    body = await _body_json(req)
    force = _as_bool(body.get("force"), False)
    result = _request_run_cancel(force)
    result["run"] = _run_snapshot()
    return result
