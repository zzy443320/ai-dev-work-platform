"""长任务作业（多子 Agent）：角色、运行记录、介入、中止与 SSE 看板。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from web.state import (ARTIFACT_DIR, _acquire_run, _as_num, _body_json,
                       _busy_detail, _gate_commands, _load_settings,
                       _release_run, _resolve_ai, _run_snapshot, _skills_text,
                       _tstore)
from web.streaming import _queue_stream
from scripts import usage  # noqa: E402
from scripts.team_bus import KINDS as TEAM_INTERVENTION_KINDS  # noqa: E402
from scripts.team_bus import create_bus, drop_bus, get_bus  # noqa: E402
from scripts.team_roles import public_roles  # noqa: E402
from scripts.team_run import TeamOrchestrator  # noqa: E402

router = APIRouter()



# ------------------------------------------------ 长任务作业（多子 Agent）
#
# 面向重构 / 组件升级迁移这类「工作量大、跑得久、覆盖面广」的任务：把活拆给
# 决策官 / 编码 / 测试 / 复核四个子 Agent，逐个工作项推进。
#
# 与既有安全契约完全一致：**任何角色都不写目标仓库**，最终只合成一份产出物
# （type=team），照旧走 /api/artifacts/{id}/approve 人工采纳。
# 这里额外提供的能力是「实时介入」：跑的过程中可以追问、纠偏、暂停、跳过、终止。
def _team_config(s: dict) -> dict:
    commands = _gate_commands(s)
    gate_cfg = {"degraded": bool(s["gate"].get("degraded", True))}
    if commands:
        gate_cfg["commands"] = commands
    return {
        "ai": _resolve_ai(s),
        "repo": dict(s["repo"]),
        "gate": gate_cfg,
        "artifacts": {"output_dir": str(ARTIFACT_DIR)},
    }


def _team_spec(body: dict) -> dict:
    s = _load_settings()
    raw_roles = body.get("roles")
    roles = [str(r).strip() for r in raw_roles
             if str(r).strip()] if isinstance(raw_roles, list) else None
    return {
        "title": str(body.get("title") or "").strip()[:120],
        "task": str(body.get("task") or ""),
        "scope": str(body.get("scope") or "").strip()[:400],
        "framework": str(body.get("framework") or "").strip()[:120],
        "notes": str(body.get("notes") or "")[:2000],
        "roles": roles or None,
        "max_rounds": max(0, min(5, int(_as_num(body.get("max_rounds"), 2, integer=True)))),
        "repo_path": s["repo"]["path"],
        # 技能注入：scope 为 all 或 team 的启用的技能
        "skills_text": _skills_text(s, "team"),
    }


@router.get("/api/team/roles")
async def team_roles():
    """四个子 Agent 的角色卡（职责 / 阶段 / 产出），前端看板按它渲染。"""
    return {"roles": public_roles()}


@router.get("/api/team/runs")
async def team_runs_list():
    runs = _tstore().list()
    # 标出哪些还在跑：刷新页面（SSE 断开）后用户能一眼看出"它其实还在跑"，
    # 而不是误以为作业已经挂了。
    for r in runs:
        r["running"] = bool(get_bus(r.get("id") or ""))
    return runs


@router.get("/api/team/runs/{rid}")
async def team_run_detail(rid: str):
    """运行记录（含事件轨迹与人工介入记录），刷新页面后靠它回看。"""
    run = _tstore().get(rid)
    if not run:
        raise HTTPException(404, f"运行记录不存在: {rid}")
    bus = get_bus(rid)
    run["live"] = bus.snapshot() if bus else {"paused": False, "stopped": False,
                                              "pending": []}
    run["running"] = bool(bus and not run.get("finished"))
    return run


@router.post("/api/team/runs/{rid}/intervene")
async def team_intervene(rid: str, req: Request):
    """人工介入：追问 / 纠偏 / 重规划 / 跳过 / 暂停 / 继续 / 终止。

    ⚠ 必须说清楚语义：模型调用是一次**不可中断**的同步请求，所以这些指令都在
    「安全边界」（每个工作项边界、每次模型调用前）才生效。返回里带 `deferred`
    与 `detail`，界面照实显示"会在当前步骤结束后生效"，不假装立刻停住。
    """
    body = await _body_json(req)
    kind = str(body.get("kind") or "message").strip().lower()
    agent = str(body.get("agent") or "*").strip() or "*"
    text = str(body.get("text") or "")
    item = str(body.get("item") or "").strip()
    # 先校验类型再看运行状态：参数写错时要给出「类型不对」，
    # 而不是被"这次运行已结束"盖过去（排查时会以为是运行的问题）。
    if kind not in TEAM_INTERVENTION_KINDS:
        raise HTTPException(400, f"不支持的干预类型: {kind}（"
                                 f"可选 {'/'.join(TEAM_INTERVENTION_KINDS)}）")
    if kind == "message" and not text.strip():
        raise HTTPException(400, "追问/纠偏内容不能为空")

    store = _tstore()
    run = store.get(rid)
    if not run:
        raise HTTPException(404, f"运行记录不存在: {rid}")
    bus = get_bus(rid)
    if bus is None:
        return JSONResponse({
            "error": "这次运行已经结束了，指令送不进去。可以在运行记录里查看它的最终结果，"
                     "或带着结论重新发起一次。",
            "kind": kind, "delivered": False,
        }, status_code=409)
    try:
        rec = bus.post(kind, agent, text=text, item=item)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.add_intervention(rid, rec)
    immediate = kind in ("pause", "resume", "stop")
    return {
        "ok": True, "delivered": True, "record": rec, "kind": kind, "agent": agent,
        # deferred 恒为 True：**所有**指令都在安全边界生效，区别只是控制类指令
        # 会立刻改变总线状态、而 message 要等该角色下一步才读得到。
        "deferred": True,
        "immediate": immediate,
        "detail": ("暂停/终止会在当前步骤结束后生效（模型调用无法中途打断）"
                   if immediate else
                   "指令已投递，会在该角色下一步开始时注入（当前步骤结束后）"),
    }


@router.post("/api/team/runs/{rid}/abort")
async def team_abort(rid: str):
    """终止这次运行。已完成的产出会保留并照常生成产出物。"""
    bus = get_bus(rid)
    if bus is None:
        run = _tstore().get(rid)
        if not run:
            raise HTTPException(404, f"运行记录不存在: {rid}")
        return JSONResponse({"error": "该运行已经结束，无需终止",
                             "status": run.get("status", "")}, status_code=409)
    bus.request_stop()
    return {"ok": True,
            "detail": "已请求终止：会在当前步骤结束后停下并交付已完成的部分"}


@router.post("/api/team/stream")
async def team_stream(req: Request):
    """启动一次长任务作业，SSE 实时推送：每个子 Agent 的状态、当前动作、
    思考与输出增量，以及计划/工作项/复核结论的流转。"""
    # 窥一眼只为把 409 说清；原子占用在下面的 `_acquire_run`
    if _run_snapshot().get("running"):
        return JSONResponse({"error": _busy_detail("启动长任务作业")}, status_code=409)
    body = await _body_json(req)
    spec = _team_spec(body)
    if not spec["task"].strip():
        raise HTTPException(400, "请先填写任务描述")

    store = _tstore()
    config = _team_config(_load_settings())
    # 先分配 run_id 再启动：这样第一个 SSE 事件到达前用户就能介入
    rid = store.reserve_id()
    spec["run_id"] = rid
    bus = create_bus(rid, pause_timeout=1800.0)
    cancel = _acquire_run("team",
                          label=f"{spec.get('title') or spec['task'][:40]} · run {rid}",
                          source=str(body.get("source") or ""))
    if cancel is None:
        drop_bus(rid)
        return JSONResponse({"error": _busy_detail("启动长任务作业"),
                             "run": _run_snapshot()}, status_code=409)

    def worker(emit):
        def forward(evt):
            # 顶栏的「停止」按的是占用租约的取消事件，而这条流程早就有自己的
            # 终止总线（/api/team/runs/{rid}/abort）——把前者翻译成后者，
            # 一个停止按钮才管得住两条路。每个事件都过这里，反应足够快。
            if cancel.is_set():
                bus.request_stop()
            emit(evt)

        forward({"type": "run_id", "run_id": rid})
        try:
            # 用量归属必须在**工作线程内部**设置，才能保证每个角色/步骤都被正确记账
            with usage.task("team", run_id=rid,
                            title=spec.get("title") or (spec["task"] or "")[:60]):
                orch = TeamOrchestrator(config, store, bus)
                orch.run(spec, emit=forward)
        finally:
            drop_bus(rid)

    return _queue_stream(asyncio.get_running_loop(), worker,
                         on_finish=lambda: _release_run(cancel))
