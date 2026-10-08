"""人审写盘面：产出物与提案的 采纳 / 拒绝 / 撤销——唯一会写目标仓库的一组端点。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from web.state import _RUN_STATE, _applier, _astore, _body_json, _fresh_pipeline, _load_settings, _store
from scripts.artifact import diff_against_repo  # noqa: E402

router = APIRouter()



@router.get("/api/artifacts")
async def list_artifacts(type: Optional[str] = None, status: Optional[str] = None):
    return _astore().list(type_filter=type, status=status)
@router.get("/api/artifacts/{aid}")
async def get_artifact(aid: str):
    a = _astore().get(aid)
    if not a:
        raise HTTPException(404, f"产出物不存在: {aid}")
    s = _load_settings()
    a["repo"] = {"path": s["repo"]["path"], "branch": s["repo"]["branch"]}
    try:
        a["preflight_live"] = _applier().preflight()
    except Exception as e:
        a["preflight_live"] = {"problems": [str(e)]}
    return a


@router.get("/api/artifacts/{aid}/diff")
async def diff_artifact(aid: str):
    """Compare artifact files against the current working tree (read-only).

    Returns per-file aligned line diff so the UI can highlight what changes.
    """
    a = _astore().get(aid)
    if not a:
        raise HTTPException(404, f"产出物不存在: {aid}")
    s = _load_settings()
    repo_root = s["repo"]["path"]
    try:
        return await run_in_threadpool(diff_against_repo, a, repo_root)
    except Exception as e:
        return {"files": [], "error": str(e)}


@router.post("/api/artifacts/{aid}/approve")
async def approve_artifact(aid: str, req: Request):
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "任务正在运行，稍后再采纳"}, status_code=409)
    body = await _body_json(req)
    force = bool(body.get("force_gate"))
    note = str(body.get("note") or "")
    if force and not body.get("confirm_risk"):
        return JSONResponse({"error": "强制采纳需要明确勾选“我已知悉风险”"}, status_code=400)
    store = _astore()
    artifact = store.get(aid)
    if not artifact:
        raise HTTPException(404, f"产出物不存在: {aid}")
    result = _applier().apply(artifact, force_gate=force)
    status = result.get("status", "failed")
    if result.get("ok"):
        from datetime import datetime as _dt
        store.set_status(aid, "applied",
                         apply={"files": result.get("files", []),
                                "backups": result.get("backups", {}),
                                "gate": result.get("gate", {}),
                                "forced": result.get("forced", False),
                                "committed": False,
                                "note": note,
                                "decided_at": _dt.now().isoformat(timespec="seconds")})
    elif status == "apply_failed":
        store.set_status(aid, "apply_failed", apply={"gate": result.get("gate", {})})
    result["artifact_id"] = aid
    code = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=code)


@router.post("/api/artifacts/{aid}/reject")
async def reject_artifact(aid: str, req: Request):
    body = await _body_json(req)
    store = _astore()
    a = store.get(aid)
    if not a:
        raise HTTPException(404, f"产出物不存在: {aid}")
    store.set_status(aid, "rejected",
                     decision={"rejected": True, "note": str(body.get("note") or "")})
    return {"ok": True}


@router.post("/api/artifacts/{aid}/undo")
async def undo_artifact(aid: str):
    # 与 proposals/undo 对齐：撤销会写目标仓库，流水线在跑时不能插队。
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "流水线正在运行，稍后再撤销"}, status_code=409)
    store = _astore()
    artifact = store.get(aid)
    if not artifact:
        raise HTTPException(404, f"产出物不存在: {aid}")
    result = await run_in_threadpool(_applier().undo, artifact)
    if result.get("ok"):
        store.set_status(aid, "pending", apply={})
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


# --------------------------------------------------------------- proposals
@router.get("/api/proposals")
async def list_proposals(status: Optional[str] = None):
    return _store().list(status=status)


@router.get("/api/proposals/{pid}")
async def get_proposal(pid: str):
    p = _store().get(pid)
    if not p:
        raise HTTPException(404, f"提案不存在: {pid}")
    patch = dict(p.get("patch") or {})
    patch.pop("patched_contents", None)  # bulky and never shown
    p["patch"] = patch
    s = _load_settings()
    p["repo"] = {"path": s["repo"]["path"], "branch": s["repo"]["branch"]}
    try:
        p["preflight_live"] = _fresh_pipeline().preflight
    except Exception as e:
        p["preflight_live"] = {"problems": [str(e)]}
    p["screenshot_base"] = "/screenshots"
    return p


@router.post("/api/proposals/{pid}/approve")
async def approve_proposal(pid: str, req: Request):
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "流水线正在运行，稍后再采纳"}, status_code=409)
    body = await _body_json(req)
    force = bool(body.get("force_gate"))
    note = str(body.get("note") or "")
    if force and not body.get("confirm_risk"):
        return JSONResponse({"error": "强制采纳需要明确勾选“我已知悉风险”"}, status_code=400)

    # approve 会拍「采纳后」截图（sync_playwright），必须跑在工作线程里
    pipeline = _fresh_pipeline()
    result = await run_in_threadpool(pipeline.approve, pid, force_gate=force, note=note)
    code = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=code)


@router.post("/api/proposals/{pid}/reject")
async def reject_proposal(pid: str, req: Request):
    body = await _body_json(req)
    result = _fresh_pipeline().reject(pid, note=str(body.get("note") or ""))
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@router.post("/api/proposals/{pid}/undo")
async def undo_proposal(pid: str):
    # 撤销也是往目标仓库写文件，所以和采纳一样要先确认流水线没在跑；
    # undo 现在会跑 preflight（内含 git 子进程），因此同样放工作线程。
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "流水线正在运行，稍后再撤销"}, status_code=409)
    pipeline = _fresh_pipeline()
    result = await run_in_threadpool(pipeline.undo, pid)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)
