"""Local Web UI for the ONES Frontend Dev Copilot (前端研发助手).

Run:  python -m web.server
Then: http://127.0.0.1:8765

Five modules share one safety contract:
- 问答       /api/chat/stream       → 只读仓库；附带的改动提案落成 artifacts
- 缺陷修复   /api/run + /api/probe  → proposals
- 需求开发   /api/tasks/run req     → artifacts（分诊：变更类型 / 影响面 / 是否要设计稿）
- Figma 转代码 /api/tasks/run reqdev → artifacts
- 接口联调   /api/tasks/run apidebug→ artifacts
- 代码测试   /api/tasks/run codetest→ artifacts
（长任务作业 /api/team/stream 同样产出 artifacts）

资源汇总（/api/resources）是唯一一个**不碰仓库、不调模型**的模块：它只是把
需求文档链接与各环境地址存在 resources.json 里，读出来给人点开。

Four endpoints touch the target repository -- approve and undo, on both
/api/proposals/{id}/* and /api/artifacts/{id}/*. None of them ever commits; they
only write the working tree (backups are taken first, undo restores from them).
Every other endpoint is read-only.

The server itself is only an assembly point: `web/urls.py` installs the HTTP
surface (same-origin guard, cache policy, static mounts, entry page) and the per
concern routers under `web/routers/` hold the endpoints. Shared config, stores
and run flags live in `web/state.py`.

数据目录都可用环境变量改写（USAGE_DIR / CHAT_DIR / ARTIFACT_DIR / SETTINGS_FILE），
自检用例靠它们把临时实例指向临时目录，从而不碰真实配置与真实数据。
"""
from fastapi import FastAPI

from web import urls
from web.routers import (chat, config, live, observe, params, pipeline, resources,
                         review, selftest, tasks, team)

app = FastAPI(title="ONES 前端研发助手 UI (Frontend Dev Copilot)")

# 装配顺序有讲究：urls.install() 注册两层中间件 + 静态挂载，必须在各业务 router
# include 之前完成，闸门才能覆盖到所有端点（含后面 include 进来的那些）。
urls.install(app)
app.include_router(config.router)
app.include_router(observe.router)
app.include_router(pipeline.router)
app.include_router(tasks.router)
app.include_router(chat.router)
app.include_router(team.router)
app.include_router(review.router)
# 内嵌浏览器会话（Figma / 任意目标站点的直播预览 + 点选读面板）：依赖受控会话，
# 同一时刻只允许一个，端点都在同源闸门后面
app.include_router(live.router)
# 参数元数据（前端表单渲染器的唯一来源）：只读，放最后，与业务端点无耦合
app.include_router(params.router)
# 资源汇总板（需求文档链接 / 各环境地址）：纯读写一份本地 JSON，与流水线、AI 全无耦合
app.include_router(resources.router)
# 自测（用例 / 需求文档 / Figma 三条链路）：只读采证 + 逐条判定，不写目标仓库
app.include_router(selftest.router)


if __name__ == "__main__":
    import os

    import uvicorn

    # 端口默认 8765；PORT 可覆盖，便于同时开一个临时实例做自检而不打断手头这个。
    _port = int(os.environ.get("PORT") or 8765)
    print(f"[web] http://127.0.0.1:{_port}  (Ctrl+C 退出)")
    uvicorn.run(app, host="127.0.0.1", port=_port, log_level="warning")

