"""任务模块：仓库文件预览、Figma 取稿（含浏览器兜底通道）、三个生成任务。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from web.state import (SCREENSHOT_DIR, _acquire_run, _as_bool, _astore, _astore_att,
                       _body_json, _busy_detail, _extensions, _load_settings,
                       _mcp_toolkit, _pipeline_config, _release_run,
                       _run_progress, _run_snapshot, _skills_text)
from scripts import usage  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.artifact import ArtifactStore  # noqa: E402
from scripts.chat_attachments import AttachmentError, text_block  # noqa: E402
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


_TASK_KINDS = ("req", "reqdev", "apidebug", "codetest")

# 一次需求最多带几份文档。超过的部分静默丢弃会让用户以为 AI 读到了，
# 所以接口层显式拦（提示「最多 6 份，请分次」）。
REQ_ATT_MAX = 6
# 拼进提示词的需求正文总上限：单份 PDF 已按 3 万字符裁剪，多份叠起来仍可能
# 把上下文吃光。超出时保留尾部（验收标准常在文末），并在开头如实说明。
REQ_DOC_MAX_CHARS = 60000


def _req_doc_with_attachments(doc: str, atts: List[Dict], store) -> str:
    """把「手写/粘贴的需求原文」与「附件抽取的正文」拼成一份完整需求。

    顺序是刻意的：用户亲手写的几句通常是最关键的约束（改哪个模块、别动什么），
    放在最前面模型才不会淹没在 PRD 的模板话里；附件正文在后，各带来源标题。
    """
    parts: List[str] = []
    head = (doc or "").strip()
    if head:
        parts.append(head)
    block = text_block(store, atts)
    if block:
        parts.append(block)
    full = "\n\n".join(parts)
    if len(full) > REQ_DOC_MAX_CHARS:
        full = ("…（需求正文超过 %d 字符，已截断；原始文档仍保留在附件里）…\n\n"
                % REQ_DOC_MAX_CHARS) + full[-REQ_DOC_MAX_CHARS:]
    return full


@router.post("/api/req/attachments")
async def req_upload_attachment(req: Request):
    """需求开发：上传需求文档（md / txt / pdf），服务端抽成文本后返回元数据。

    为什么不复用问答的上传接口：两者的落盘目录与用途不同（问答附件是排障素材，
    需求文档是任务输入），混在一起会让「附件」这个概念在两个模块里语义打架。
    但落盘与解析实现是同一套（ChatAttachmentStore），PDF 的文字层抽取只写一份。
    """
    try:
        form = await req.form()
    except Exception as e:
        raise HTTPException(400, f"读取上传内容失败: {e}")
    upload = form.get("file")
    if upload is None:
        raise HTTPException(400, "没有收到文件（字段名应为 file）")
    if isinstance(upload, str):
        raise HTTPException(400, "file 字段必须是文件内容")
    try:
        data = await upload.read()
    except Exception as e:
        raise HTTPException(400, f"读取文件内容失败: {e}")
    name = str(getattr(upload, "filename", "") or "")
    ctype = str(getattr(upload, "content_type", "") or "")
    store = _astore_att()
    try:
        # PDF 解析是 CPU 活（几十页要几百毫秒到几秒），不能占住事件循环。
        info = await run_in_threadpool(store.save, name, data, ctype)
    except AttachmentError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"保存附件失败: {e}")
    return {"attachment": store.public(info), "preview": store.preview(info)}


@router.post("/api/tasks/run")
async def tasks_run(req: Request):
    """需求开发 / Figma 转代码 / 接口联调 / 代码测试：AI 产出「产出物」，只存 JSON 不写仓库。"""
    # 先窥一眼占用只为把 409 说得清（是谁、跑了多久）；真正的原子占用在下面
    # `_acquire_run` 那一步完成。中间的 MCP 握手可能耗时，两者之间被抢先是允许的，
    # 届时 acquire 返回 None 同样回 409。
    if _run_snapshot().get("running"):
        return JSONResponse({"error": _busy_detail("跑任务")}, status_code=409)
    body = await _body_json(req)
    kind = str(body.get("task") or "").strip()
    if kind not in _TASK_KINDS:
        raise HTTPException(400, f"task 必须是 {'/'.join(_TASK_KINDS)}")

    s = _load_settings()
    ctx = {
        # 需求开发（req）：需求原文是主输入，与 reqdev 的「需求描述」分开存 ——
        # 一个是分诊依据，一个是生成代码时的补充说明，混用会让提示词互相污染。
        "requirement_doc": str(body.get("requirement_doc") or ""),
        # auto=能改就改（默认，把需求做完）；triage=只出方案；code=缺设计稿也先出代码
        "deliver": str(body.get("deliver") or "auto"),
        "source": str(body.get("source") or ""),
        "scope": str(body.get("scope") or ""),
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

    # 需求开发：把仓库根交给任务层，由它自己生成「目录索引」并校验影响面路径。
    # （目录索引只发目录 + 文件数，不是全部路径；见 task_modules._repo_index）
    if kind == "req":
        repo_root = str((s.get("repo") or {}).get("path") or "").strip()
        if repo_root and Path(repo_root).exists():
            ctx["repo_root"] = repo_root
        # 需求附件：md / txt 直接读，PDF 用上传时抽好的正文。附件是**独立输入**
        # 而不是「预填进需求原文的文本」—— 用户改天重新打开页面，手写内容还在，
        # 附件也还在（id 存在同一个 composable 里），不会因为换行/覆盖而丢。
        raw_ids = body.get("attachment_ids")
        if not isinstance(raw_ids, list):
            raw_ids = []
        ids = [str(i).strip() for i in raw_ids if str(i or "").strip()][:REQ_ATT_MAX]
        if ids:
            store = _astore_att()
            atts = store.many(ids)   # 认不出的 id 静默丢弃（与问答同一套宽容口径）
            if atts:
                ctx["requirement_doc"] = _req_doc_with_attachments(
                    ctx["requirement_doc"], atts, store)
                ctx["attachments"] = [store.public(a) for a in atts]

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
    tool_specs, tool_executor = await run_in_threadpool(_mcp_toolkit, s)  # MCP 握手会阻塞
    ctx["skills_used"] = used_skills
    ctx["tools_used"] = [t["name"] for t in tool_specs]

    cancel = _acquire_run("tasks",
                          label=f"{TASK_LABELS.get(kind, kind)} · {title or (ctx.get('requirement') or '')[:40]}",
                          source=str(body.get("source") or ""))
    if cancel is None:
        return JSONResponse({"error": _busy_detail("跑任务"),
                             "run": _run_snapshot()}, status_code=409)
    try:
        if ai.mode == "mock" or ai_cfg.get("mock"):
            payload = mock_task_result(kind, ctx)
            payload["ai_mode"] = ai.mode
        else:
            # AI 调用是同步长请求，丢进线程池避免卡死其它接口。
            # 用量归属放进线程内部设置（run_in_threadpool 不保证上下文跨线程可见）。
            def _job():
                # 需求开发是四阶段流水线（分诊 → 定位 → 改动 → 自检），
                # 单个请求可能要跑几分钟。把阶段写进运行租约，界面才能显示
                # 「现在卡在哪一步」，而不是一个转圈转到结束。
                def _progress(label, detail=""):
                    _run_progress(cancel, stage=str(label), stage_detail=str(detail)[:200])

                with usage.task(kind, title=title or TASK_LABELS.get(kind, kind),
                                step=kind):
                    return run_task(kind, ai, ctx, skills_text=skills_text,
                                    tool_specs=tool_specs, tool_executor=tool_executor,
                                    progress=_progress)

            payload = await run_in_threadpool(_job)
        if not title:
            title = {k: (payload.get("summary") or "")[:60] for k in (kind,)}.get(kind) \
                    or TASK_LABELS.get(kind, kind)
    except TaskError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        return JSONResponse({"error": f"任务执行失败: {e}"}, status_code=500)
    finally:
        _release_run(cancel)

    # 附件清单随产出物落库：过几天回看这次需求时，才说得清「AI 当时读的是哪一版
    # 文档」。放在 create 之前赋值，mock 与真实链路都覆盖到。
    payload["attachments"] = ctx.get("attachments") or []
    artifact = _astore().create(kind, title, payload, ctx)
    return {"ok": not payload.get("error"), "artifact": ArtifactStore.summary(artifact),
            "ai_mode": payload.get("ai_mode", ""),
            # 分诊结论 + 流水线结果直接回给前端：界面的「变更类型 / 是否需要设计稿 /
            # 影响面 / 四个阶段跑到哪 / 自检结论」不该再多一次详情请求才能看到；
            # 其余类型这些字段是空值，前端按同一份结构读。
            "result": {
                "change_type": payload.get("change_type", ""),
                "change_type_label": payload.get("change_type_label", ""),
                "needs_design": bool(payload.get("needs_design")),
                "design_brief": payload.get("design_brief", ""),
                "impacts": payload.get("impacts", []),
                "impacts_corrected": payload.get("impacts_corrected", 0),
                "repo_file_count": payload.get("repo_file_count", 0),
                "endpoints": payload.get("endpoints", []),
                "files": [{"path": f.get("path", ""), "action": f.get("action", ""),
                           "description": f.get("description", ""),
                           "stats": f.get("stats") or {},
                           "edits": len(f.get("edits") or [])}
                          for f in payload.get("files") or []],
                "stages": payload.get("stages", []),
                "changed": bool(payload.get("changed")),
                "selfcheck": payload.get("selfcheck") or {},
                "gaps": payload.get("gaps", []),
                "verify": payload.get("verify", []),
                "unresolved": payload.get("unresolved", []),
                "deliver": payload.get("deliver", ""),
                "summary": payload.get("summary", ""),
                "attachments": payload.get("attachments") or [],
            }}
