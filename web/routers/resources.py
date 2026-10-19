"""资源汇总板的读写端点（需求文档链接 / 各环境地址）。

    GET  /api/resources   → 整板读出
    POST /api/resources   → 整板写入（界面每改一处就把整份回存）

为什么是「整板回存」而不是逐条增删改
------------------------------------
这是一份**有序**的便签板：分组之间的先后、组内条目的上下，本身就是用户在表达
「哪个入口最常用」。逐条接口要拆成 增/删/改/移动 四条，每条都得处理"组内顺序"，
端点更多、行为更难测，而收益只是少传一屏 JSON（几十 KB 以内，本机回环）。

清洗只做一次、且在后端做（见 ``scripts/resource_board.py`` 的 ``sanitize_board``）：
协议白名单（拒绝 ``javascript:`` 这类能在 href 里执行的地址）、长度截断、空行丢弃。
界面因此不需要重复一遍校验逻辑 —— 校验写两处，迟早两处不一致。

``run_in_threadpool``：读写在磁盘上，不放进事件循环 —— 这块服务同时还挂着四条
SSE 流，一次慢盘 IO 卡住事件循环，所有心跳一起停。
"""

from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, Request

from web.state import _body_json, _rboard

router = APIRouter()


@router.get("/api/resources")
async def get_resources():
    board = await run_in_threadpool(_rboard().load)
    return {"ok": True, "board": board}


@router.post("/api/resources")
async def save_resources(req: Request):
    body = await _body_json(req)
    # 兼容两种写法：{"board": {...}}（界面用）与直接 {...}（curl 手测用）
    payload = body.get("board") if isinstance(body.get("board"), dict) else body
    board = await run_in_threadpool(_rboard().save, payload)
    return {"ok": True, "board": board}
