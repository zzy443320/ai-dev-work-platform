"""HTTP 表面层：同源闸门、静态资源缓存策略、静态挂载、界面入口与健康检查。

闸门与缓存策略注册在 router 上（FastAPI 的 app 内部就是一个 Router），
所以 web/server.py 只要 include 本模块，中间件与挂载就会生效。
"""

from pathlib import Path
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web.state import ATTACH_DIR, KB_DIR, SCREENSHOT_DIR, STATIC_DIR, VUE_ENTRY, _astore, _gate_commands, _load_settings, _page_auth, _resolve_ai, _run_snapshot, _store, _pipeline_preflight
from scripts.ai_providers import AIConfig  # noqa: E402
from scripts.gate import resolve_commands  # noqa: E402

router = APIRouter()

# ---------------------------------------------------------- 本机访问边界
#
# 这里原先挂的是 CORSMiddleware(allow_origins=["*"])，而那是一个实打实的漏洞。
# 界面和 API 同源（前端所有请求都写相对 URL，见 frontend/src/api/client.js），
# 根本不需要 CORS；通配反而让浏览器里打开的**任意网页**都能对
# http://127.0.0.1:8765 发 POST 并被照常接受（跨源 POST 里 text/plain 与
# application/json 都不触发预检，预检也拦不住"只写不读回"的攻击）。链条是：
#   POST /api/settings 改 gate.commands_text → 采纳提案 → scripts/gate.py
# 以 shell=True 执行它。等价于"访问某个恶意页面 = 在你机器上跑任意命令"。
#
# 换成本机工具的标准 CSRF 闸门，三层，逐条对应真实攻击面：
#   1. Host 必须是回环地址 —— 挡 DNS rebinding（evil.com 解析到 127.0.0.1 时
#      Host 头就是 evil.com，请求会在这里被拒）。
#   2. 带 Origin / Referer 的请求必须同源 —— 挡任意网页发起的跨源读写（浏览器
#      发跨源请求时一定会带 Origin，且伪造不了）。
#   3. Sec-Fetch-Site 存在时必须不是跨源 —— 兜住不发 Origin 的老式表单提交。
#   4. 三个头都不带时放行 —— 本机 curl / requests / 自检用例本来就不发这些头，
#      行为不变（这也是不给"本机进程"再加 token 的原因：能起本地进程的攻击者
#      已经能直接读配置文件里的密钥，加一层只会被测试和脚本绕过去）。
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _origin_tuple(raw: str) -> str:
    """把 Origin / Referer / URL 归一成 scheme://host[:port]，省略默认端口。

    解析不出来源时返回 ""（调用方据此判断"这个请求没带来源信息"）。
    """
    try:
        parts = urlsplit(str(raw).strip().lower())
        if not parts.scheme or not parts.hostname:
            return ""
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        port = parts.port  # 端口非法时（如 host:abc）会抛 ValueError
        if port is None or str(port) == _DEFAULT_PORTS.get(parts.scheme):
            return f"{parts.scheme}://{host}"
        return f"{parts.scheme}://{host}:{port}"
    except ValueError:
        return ""


def _rejected(request: Request, why: str) -> JSONResponse:
    """拒绝原因同时打到控制台——本机工具没有日志框架，print 就是审计日志。"""
    print(f"[web] 403 {request.method} {request.url.path} —— {why}")
    return JSONResponse(
        {"error": f"拒绝访问：{why}。本服务只接受来自本机界面的同源请求，"
                  f"请在浏览器打开 http://127.0.0.1:{request.url.port or 8765}"},
        status_code=403,
    )


def install(app) -> None:
    """把 HTTP 表面装到 app 上：两层中间件 + 三个只读静态挂载 + 本模块的路由。

    中间件与 mount 必须在 **app** 上注册：`APIRouter` 没有 `.middleware()`，
    硬挂 router 会在 import 期就 AttributeError（实测踩到）。注册顺序保持与拆分前一致
    ——后注册的在外层，所以先闸门后缓存策略。
    """
    app.middleware("http")(local_only_guard)
    app.middleware("http")(static_cache_policy)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOT_DIR)), name="screenshots")
    # 问答附件的预览通道（只读）：文件名由服务端生成，用户给的名字只作展示
    app.mount("/attachments", StaticFiles(directory=str(ATTACH_DIR)), name="attachments")
    app.include_router(router)


async def local_only_guard(request: Request, call_next):
    """只放行本机来源的请求；细节见 _LOOPBACK_HOSTS 上方的注释。"""
    url = request.url
    host = (url.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return _rejected(request, f"Host 不是本机地址：{host or '(空)'}")

    headers = request.headers
    requester = _origin_tuple(headers.get("origin") or "")
    if not requester and headers.get("referer"):
        # Referer 只在部分场景发（比如从别的页面跳过来），按同一套规则比。
        requester = _origin_tuple(headers["referer"])
    if requester:
        mine = _origin_tuple(f"{url.scheme}://{url.netloc}")
        if requester != mine:
            return _rejected(request, f"跨源请求：{requester}")

    site = (headers.get("sec-fetch-site") or "").lower()
    # none = 地址栏直接访问/书签，same-origin/same-site = 自己人；其余一律拒。
    if site and site not in ("none", "same-origin", "same-site"):
        return _rejected(request, f"Sec-Fetch-Site：{site}")

    return await call_next(request)


async def static_cache_policy(request: Request, call_next):
    """静态资源缓存策略：治「改了样式浏览器却不变」。

    - /static/vue/ 下是 vite 带 hash 的构建产物，文件名即版本 → 长缓存 immutable；
    - /static/ 其余是手写文件（style.css / v2.html / .vite/manifest.json），没有 hash，
      必须每次协商缓存 revalidate（ETag 未变则 304，开销极小）——否则浏览器启发式
      缓存会拿旧 style.css 用上好几天，新加的规则（如 .chat-drop）全数失效。
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/"):
        if path.startswith("/static/vue/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
    return response



# ------------------------------------------------------------------- basics
@router.get("/")
async def index():
    """唯一界面入口（Vue 3 构建产物）。

    构建产物不存在时给可操作的提示而不是 500 —— 新克隆的仓库还没跑过
    `npm run build` 时就是这种情况。
    """
    if not VUE_ENTRY.is_file():
        raise HTTPException(
            503,
            "前端尚未构建。请在 frontend/ 目录执行：npm install && npm run build",
        )
    # 入口 HTML 引用的是带 hash 的 /static/vue/ 产物：构建后 hash 变了，
    # 但浏览器对没有 Cache-Control 的响应会启发式缓存，导致拿到旧 HTML、
    # 引用已被 clean 掉的旧 chunk（表现为「改了样式看不到/界面缺一块」）。
    # 与 /static/style.css 同策略：入口永远 no-cache，产物本体才 immutable。
    return FileResponse(str(VUE_ENTRY), headers={"Cache-Control": "no-cache"})


@router.get("/api/health")
async def health():
    s = _load_settings()
    repo_ok = bool(s["repo"]["path"]) and Path(s["repo"]["path"]).exists()
    ai_cfg = AIConfig.from_dict(_resolve_ai(s))
    commands = _gate_commands(s)
    detected = resolve_commands(s["repo"]["path"], {"commands": commands}) if repo_ok else []
    level = ("explicit" if commands else ("package_json" if detected else "degraded"))
    pre = {}
    if repo_ok:
        try:
            # 界面每隔几秒就轮询 /api/health，这里内含 git 子进程 → 必须离开事件循环，
            # 否则整页（含流式输出的心跳）都会随轮询周期卡顿。
            pre = await run_in_threadpool(_pipeline_preflight)
        except Exception as e:
            pre = {"problems": [str(e)]}
    counts = _store().counts()
    run = _run_snapshot()
    return {
        "ok": True,
        "running": bool(run.get("running")),
        # 谁占着槽位、跑到哪一单、跑了多久——老前端只读 `running`，新界面读这个
        "run": run,
        "repo": s["repo"]["path"],
        "repo_exists": repo_ok,
        "kb_dir": str(KB_DIR),
        "ai_mode": ai_cfg.mode,
        "ai_model": ai_cfg.model,
        "ai_provider": ai_cfg.provider,
        "ai_endpoint": ai_cfg.chat_url,
        "ai_problems": ai_cfg.validate(),
        "ones_configured": bool(s["ones"]["base_url"] and s["ones"]["token"]
                                and s["ones"]["project_uuid"]),
        "gate_level": level,
        "gate_commands": [c["cmd"] for c in detected],
        "current_branch": pre.get("current_branch", ""),
        "expected_branch": pre.get("expected_branch", ""),
        "dirty": bool(pre.get("dirty")),
        "repo_problems": pre.get("problems", []),
        "app_url": s["playwright"]["base_url"],
        "proposal_counts": counts,
        "artifact_counts": _astore().counts(),
        "figma_configured": bool((s.get("figma") or {}).get("token")),
        "page_login_configured": bool(_page_auth(s).get("password")),
        "pending": counts.get("pending", 0) + counts.get("gate_failed", 0),
    }
