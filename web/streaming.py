"""SSE 骨架：POST-SSE 统一的「工作线程 + 队列 + 生成器」桥（15s 心跳，断连即停）。
"""

import asyncio
import json
import threading
from typing import Optional

from fastapi.responses import StreamingResponse

from web.state import _RUN_STATE



def _sse(gen_fn):
    return StreamingResponse(
        gen_fn(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


def _queue_stream(loop, worker, state: Optional[dict] = None,
                  on_finish=None) -> StreamingResponse:
    """SSE 桥：同步 worker 线程把事件经 call_soon_threadsafe 放进 asyncio.Queue，
    异步生成器持续吐出（15s 无事件发心跳注释防中间层掐断）。

    worker 内部是同步重活（ONES 网络 + AI + sync_playwright），绝不能进
    事件循环——这正是 /api/run 踩过的坑，这里从线程起步天然规避。
    ⚠ 不要用 run_in_executor 包阻塞的 queue.get：wait_for 超时会泄漏一个
    永久阻塞在 get() 上的执行器线程，长静默阶段（截图/AI 推理）会耗尽线程池。
    ⚠ `state` 是「占用标记」的归属字典，默认缺陷流水线的 `_RUN_STATE`。问答等
    独立流程必须传自己的字典，否则它跑完会把流水线的占用标记清掉（两条流程
    互不相干，共用一个标记会互相误放行）。
    ⚠ 走「租约」模型的调用方（缺陷流水线）改传 `on_finish`：解除占用必须比对是
    哪一份租约，否则被「强制解除占用」的僵尸线程收尾时会把**新一次运行**的占用清掉。
    """
    st = state if state is not None else _RUN_STATE
    aq: asyncio.Queue = asyncio.Queue()

    def emit(evt: dict) -> None:
        loop.call_soon_threadsafe(aq.put_nowait, dict(evt))

    def finish() -> None:
        if on_finish is not None:
            on_finish()
        else:
            st["running"] = False

    def job():
        try:
            worker(emit)
        except Exception as e:  # 双保险：worker 内部异常也变成 fatal 事件
            try:
                loop.call_soon_threadsafe(
                    aq.put_nowait, {"type": "fatal", "error": str(e)})
            except RuntimeError:
                pass  # 服务退出时 loop 已关，事件发不出去就算了
        finally:
            finish()

    try:
        threading.Thread(target=job, daemon=True, name="pipeline-stream").start()
    except Exception:
        finish()  # 线程没起来也要解除占用
        raise

    async def gen():
        while True:
            try:
                evt = await asyncio.wait_for(aq.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") in ("done", "fatal"):
                break

    return _sse(gen)
