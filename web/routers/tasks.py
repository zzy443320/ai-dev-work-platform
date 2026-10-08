"""任务模块：仓库文件预览、Figma 取稿（含浏览器兜底通道）、三个生成任务。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from web.state import SCREENSHOT_DIR, _RUN_STATE, _as_bool, _astore, _body_json, _extensions, _load_settings, _mcp_toolkit, _pipeline_config, _skills_text
from scripts import usage  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.artifact import ArtifactStore  # noqa: E402
from scripts.figma_fetcher import FigmaClient, FigmaError, parse_figma_url  # noqa: E402
from scripts.task_modules import TASK_LABELS, TaskError, mock_task_result, run_task  # noqa: E402

router = APIRouter()



# ------------------------------------------------------- figma / tasks / artifacts
@router.get("/api/repo/file")
async def repo_file(path: str = Query(...)):
    """只读预览仓库内文件（代码测试模块用），不允许越出仓库根。"""
    s = _load_settings()
    root = Path(s["repo"]["path"])
    if not root.exists():
        raise HTTPException(400, f"仓库路径不存在: {root}")
    rel = path.strip().replace("\\", "/").lstrip("/")
    target = root / rel
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(400, "路径越界，必须位于仓库内")
    if not target.is_file():
        raise HTTPException(404, f"文件不存在: {rel}")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = target.read_text(encoding="gbk", errors="replace")
    return {"path": rel, "content": content[:20000], "truncated": len(content) > 20000}


def _looks_like_error_page(title: str, text: str) -> bool:
    """CDN / 网关错误页识别。设计稿页面的可见文本里出现这些字样基本可以判死。"""
    hay = f"{title or ''}\n{text or ''}"[:2000].lower()
    markers = (
        "the request could not be satisfied",
        "cloudfront",
        "access denied",
        "403 forbidden",
        "404 not found",
        "page not found",
        "this page isn't available",
        "something went wrong",
        "error 404", "error 403",
    )
    return any(m in hay for m in markers)


def _figma_fetch_via_browser(url: str, node_id: str) -> dict:
    """没有 Personal Access Token 时的兜底通道：用本机浏览器（含已登录的 Figma
    cookie）打开设计稿页面，回传页面可见文本树 + 截图，供 AI 参考。

    能力**弱于**官方 REST（拿不到节点 id/尺寸/颜色/图层层级），返回里
    `source=browser` 与 `note` 会明说，避免用户误以为等价。"""
    from playwright.sync_api import sync_playwright

    full = url if url.startswith("http") else "https://" + url
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(SCREENSHOT_DIR / "_figma_profile"),
                headless=True,
                viewport={"width": 1600, "height": 1000},
            )
            browser = None
        except Exception:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        child = ctx.pages[0] if getattr(ctx, "pages", None) else ctx.new_page()
        try:
            child.goto(full, wait_until="domcontentloaded", timeout=45000)
            try:
                child.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
        except Exception as e:
            _close_ctx(ctx, browser)
            raise FigmaError(f"浏览器打开设计稿失败: {e}")
        try:
            title = child.title()
            text = (child.evaluate(
                "() => ((document.body && document.body.innerText) || '').trim()") or "")
        except Exception as e:
            _close_ctx(ctx, browser)
            raise FigmaError(f"读取页面内容失败: {e}")
        shot = ""
        try:
            img = SCREENSHOT_DIR / "figma_design.png"
            child.screenshot(path=str(img), full_page=False)
            shot = str(img)
        except Exception:
            pass
        _close_ctx(ctx, browser)
    if len(text) < 80:
        raise FigmaError(
            "没有 Figma Token，且浏览器通道也没能打开设计稿（页面内容为空）。"
            "可能是设计稿不公开、需要登录，或链接带了 node-id 而浏览器侧渲染未完成。"
            "建议在设置里填写 Personal Access Token（只读即可）。")
    # 「页面打开了」不等于「拿到设计稿」：CDN 的 403/404 错误页同样有两三百字符
    # 且 console 干净。放过它会让 AI 拿一段错误文案去生成代码，必须显式拒绝。
    if _looks_like_error_page(title, text):
        raise FigmaError(
            f"浏览器打开的不是设计稿内容，而是错误页（标题「{title[:60]}」）。"
            "常见原因：链接/file key 不存在、设计稿未公开或需要登录、公司网络拦截了 Figma CDN。"
            "请在浏览器里确认该链接能正常打开，或在设置里填写 Personal Access Token。")
    return {
        "file_key": "",
        "file_name": title,
        "node_id": node_id,
        "node_name": title,
        "tree": text[:12000],
        "node_count": 0,
        "last_modified": "",
        "truncated": len(text) > 12000,
        "source": "browser",
        "screenshot": shot,
        "note": ("本次走的是「浏览器通道」（未配置 Personal Access Token）：内容取自设计稿页面"
                 "可见文本，不含图层 id/尺寸/颜色等结构信息，AI 还原度会低于官方接口。"
                 "要拿完整结构请在设置里填写 Figma Token。"),
    }


def _close_ctx(ctx, browser) -> None:
    try:
        ctx.close()
    except Exception:
        pass
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass


@router.post("/api/figma/fetch")
async def figma_fetch(req: Request):
    """拉取 Figma 设计稿，返回 AI 可读的结构化文本树。不写任何文件。

    两条通道：有 token → 官方 REST（推荐）；没 token → 浏览器兜底（弱）。
    两者都是同步阻塞调用，必须丢进线程池。"""
    body = await _body_json(req)
    s = _load_settings()
    token = str(body.get("token") or "").strip() or (s.get("figma") or {}).get("token", "")
    url = str(body.get("url") or "").strip()
    node_id = str(body.get("node_id") or "").strip()
    if not url:
        raise HTTPException(400, "请填写 Figma 设计稿链接")
    try:
        parse_figma_url(url)
    except FigmaError as e:
        raise HTTPException(400, str(e))
    if token:
        try:
            client = FigmaClient(token)
            return await run_in_threadpool(client.fetch, url, node_id)
        except FigmaError as e:
            raise HTTPException(502, f"Figma 拉取失败: {e}")
    if not _as_bool(body.get("browser_fallback"), True):
        raise HTTPException(400, "没有可用的 Figma Token，请在设置里填写后再拉取")
    try:
        return await run_in_threadpool(_figma_fetch_via_browser, url, node_id)
    except FigmaError as e:
        raise HTTPException(502, f"Figma 拉取失败: {e}")
    except Exception as e:
        raise HTTPException(502, f"Figma 浏览器通道失败: {e}")


_TASK_KINDS = ("reqdev", "apidebug", "codetest")


@router.post("/api/tasks/run")
async def tasks_run(req: Request):
    """需求开发 / 接口联调 / 代码测试：AI 产出「产出物」（文件+方案），只存 JSON 不写仓库。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有任务在运行，请等待完成"}, status_code=409)
    body = await _body_json(req)
    kind = str(body.get("task") or "").strip()
    if kind not in _TASK_KINDS:
        raise HTTPException(400, f"task 必须是 {'/'.join(_TASK_KINDS)}")

    s = _load_settings()
    ctx = {
        "requirement": str(body.get("requirement") or ""),
        "framework": str(body.get("framework") or "React + TypeScript"),
        "design": str(body.get("design") or ""),
        "repo_context": str(body.get("repo_context") or ""),
        "api_doc": str(body.get("api_doc") or ""),
        "base_url": str(body.get("base_url") or ""),
        "notes": str(body.get("notes") or ""),
        "file_path": str(body.get("file_path") or ""),
        "requirements": str(body.get("requirements") or ""),
    }
    title = str(body.get("title") or "").strip()

    # 代码测试：从仓库读目标文件内容
    if kind == "codetest":
        rel = ctx["file_path"].strip()
        if not rel:
            raise HTTPException(400, "请填写目标文件路径（相对仓库根）")
        root = Path(s["repo"]["path"])
        if not root.exists():
            raise HTTPException(400, f"仓库路径不存在: {root}")
        target = root / rel.replace("\\", "/").lstrip("/")
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            raise HTTPException(400, "文件路径越界，必须位于仓库内")
        if not target.is_file():
            raise HTTPException(400, f"目标文件不存在: {target}")
        try:
            ctx["file_content"] = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            ctx["file_content"] = target.read_text(encoding="gbk", errors="replace")
        ctx["file_path"] = rel
        title = title or f"测试 {rel}"

    ai_cfg = dict(_pipeline_config(s)["ai"])
    ai = AIModel(ai_cfg)

    # 扩展能力：技能注入 + MCP 工具挂载
    skills_text = _skills_text(s, kind)
    used_skills = [sk.get("name") for sk in _extensions(s).get("skills") or []
                   if sk.get("enabled") and ("all" in (sk.get("scope") or ["all"]) or kind in (sk.get("scope") or []))]
    tool_specs, tool_executor = _mcp_toolkit(s)
    ctx["skills_used"] = used_skills
    ctx["tools_used"] = [t["name"] for t in tool_specs]

    _RUN_STATE["running"] = True
    try:
        if ai.mode == "mock" or ai_cfg.get("mock"):
            payload = mock_task_result(kind, ctx)
            payload["ai_mode"] = ai.mode
        else:
            # AI 调用是同步长请求，丢进线程池避免卡死其它接口。
            # 用量归属放进线程内部设置（run_in_threadpool 不保证上下文跨线程可见）。
            def _job():
                with usage.task(kind, title=title or TASK_LABELS.get(kind, kind),
                                step=kind):
                    return run_task(kind, ai, ctx, skills_text=skills_text,
                                    tool_specs=tool_specs, tool_executor=tool_executor)

            payload = await run_in_threadpool(_job)
        if not title:
            title = {k: (payload.get("summary") or "")[:60] for k in (kind,)}.get(kind) \
                    or TASK_LABELS.get(kind, kind)
    except TaskError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        return JSONResponse({"error": f"任务执行失败: {e}"}, status_code=500)
    finally:
        _RUN_STATE["running"] = False

    artifact = _astore().create(kind, title, payload, ctx)
    return {"ok": not payload.get("error"), "artifact": ArtifactStore.summary(artifact),
            "ai_mode": payload.get("ai_mode", "")}
