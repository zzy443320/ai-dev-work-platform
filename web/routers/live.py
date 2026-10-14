# -*- coding: utf-8 -*-
"""内嵌浏览器会话端点：开 / 帧流 / 输入转发 / 点选读面板 / 停。

线程模型：截帧在 scripts/live_browser.py 的守护线程里跑，本层只做
「取最新帧 → SSE 推给前端」；输入转发是同步快操作（毫秒级），直接在
事件循环里跑 Playwright 会阻塞整个服务 —— 所以丢进线程池。

安全边界：全部端点都在 app 级同源闸门后面（web/urls.py 的 local_only_guard），
和改 gate.commands 的端点同一道门。会话操作的是**本机**受控浏览器，
能打开的网址由使用者给出 —— 这与「用本机浏览器看网页」的信任级别一致。
"""

import asyncio
import base64
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from scripts.live_browser import LiveBrowserError, manager

router = APIRouter()


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


async def _sleep(sec: float) -> None:
    await asyncio.sleep(sec)


def _err(e: LiveBrowserError, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(e))


class StartBody(BaseModel):
    url: str


class InputBody(BaseModel):
    kind: str = ""          # inspect 不需要 kind，所以给默认值
    x: float = 0.0
    y: float = 0.0
    vw: float = 0.0
    vh: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    key: str = ""
    text: str = ""
    button: str = ""
    dbl: bool = False


# 输入 / 读属性 / 开会话都可能在 worker 线程上等 Playwright（最长一个 goto 超时），
# 所以这几个端点用 **sync def**：FastAPI 自动丢线程池，不阻塞事件循环——
# 否则帧流的 SSE 会被一起卡死（实测）。stop 同理。


@router.post("/api/live/start")
def live_start(body: StartBody):
    try:
        return {"ok": True, "session": manager.start(body.url)}
    except LiveBrowserError as e:
        msg = str(e)
        code = 409 if ("会话在跑" in msg or "已在运行" in msg or "残留锁" in msg) else 400
        raise _err(e, code)


@router.get("/api/live/status")
async def live_status():
    s = manager.get()
    return {"ok": True, "running": bool(s and s.page is not None),
            "session": s.snapshot() if s else None}


@router.get("/api/live/frame")
async def live_frame():
    """最新一帧（JSON）。给自检与前端断线重连兜底用；常态走 SSE 流。"""
    s = manager.get()
    if not s:
        raise HTTPException(status_code=409, detail="没有进行中的内嵌浏览器会话")
    frame = s.latest_frame()
    return {"ok": True, "seq": s.snapshot().get("frame_seq", 0),
            "jpeg": base64.b64encode(frame).decode("ascii") if frame else ""}


@router.post("/api/live/input")
def live_input(body: InputBody):
    s = manager.get()
    if not s:
        raise HTTPException(status_code=409, detail="没有进行中的内嵌浏览器会话")
    try:
        return s.input(body.model_dump())
    except LiveBrowserError as e:
        raise _err(e)


@router.post("/api/live/inspect")
def live_inspect(body: InputBody):
    s = manager.get()
    if not s:
        raise HTTPException(status_code=409, detail="没有进行中的内嵌浏览器会话")
    try:
        return {"ok": True, **s.inspect(body.model_dump())}
    except LiveBrowserError as e:
        raise _err(e)


@router.post("/api/live/stop")
def live_stop():
    """关会话要等真释放（残留 chromium 会一直占 profile 目录）。"""
    return manager.stop()


@router.post("/api/live/stream")
async def live_stream(request: Request):
    """帧流：SSE。帧是 base64 JPEG；status 是会话状态快照；结尾必发 done。

    为什么帧不走 GET+EventSource：前端 useSse.js 的既有约定是 POST + 请求体，
    三条流式接口都这么走，这里保持同构。客户端断开（前端点停止 / 关页签）
    由 request.is_disconnected 感知，循环退出时会话**保留**——预览可以断开重连。"""
    s = manager.get()
    if not s:
        raise HTTPException(status_code=409, detail="没有进行中的内嵌浏览器会话")

    async def gen():
        last_seq = -1
        beats = 0
        while True:
            if await request.is_disconnected():
                break
            snap = s.snapshot()
            if not snap.get("alive"):
                yield f"data: {_j({'type': 'status', 'session': snap})}\n\n"
                break
            if snap["frame_seq"] != last_seq:
                last_seq = snap["frame_seq"]
                frame = s.latest_frame()
                if frame:
                    b64 = base64.b64encode(frame).decode("ascii")
                    # 100KB 以内的帧在 localhost 上毫无压力；页面没动就不发
                    yield f"data: {_j({'type': 'frame', 'seq': last_seq, 'jpeg': b64})}\n\n"
            beats += 1
            if beats % 9 == 0:          # ≈1s 一条状态（循环步长 0.12s）
                yield f"data: {_j({'type': 'status', 'session': snap})}\n\n"
            await _sleep(0.12)
        yield f"data: {_j({'type': 'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
