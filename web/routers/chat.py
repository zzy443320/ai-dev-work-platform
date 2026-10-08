"""问答：会话增删查、附件上传、流式对话。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

import asyncio
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from web.state import _CHAT_STATE, _as_bool, _astore, _astore_att, _body_json, _cstore, _load_settings, _mcp_toolkit, _pipeline_config, _skills_text
from web.streaming import _queue_stream
from scripts.chat_attachments import (AttachmentError, attach_note,
                                      images_for, text_block)
from scripts import usage  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.artifact import ArtifactStore  # noqa: E402
from scripts.chat import RepoReader, mock_chat_reply, run_chat  # noqa: E402

router = APIRouter()



# ------------------------------------------------------------------ 问答
# 「不一定是开发或修缺陷」时的通用入口：随便问、顺手改。
# 只读仓库（RepoReader），要改代码时只产出「产出物」——写入仍只发生在采纳那一步。
CHAT_TITLE_MAX = 60


@router.get("/api/chat/sessions")
async def chat_list_sessions():
    return {"sessions": _cstore().list(), "running": bool(_CHAT_STATE["running"])}


@router.post("/api/chat/sessions")
async def chat_new_session(req: Request):
    body = await _body_json(req)
    sess = _cstore().create(title=str(body.get("title") or "").strip())
    return {"session": sess}


@router.get("/api/chat/sessions/{sid}")
async def chat_get_session(sid: str):
    sess = _cstore().get(sid)
    if not sess:
        raise HTTPException(404, f"会话不存在: {sid}")
    return {"session": sess}


@router.post("/api/chat/sessions/{sid}/clear")
async def chat_clear_session(sid: str):
    sess = _cstore().clear(sid)
    if not sess:
        raise HTTPException(404, f"会话不存在: {sid}")
    return {"ok": True, "session": sess}


@router.post("/api/chat/sessions/{sid}/delete")
async def chat_delete_session(sid: str):
    if not _cstore().delete(sid):
        raise HTTPException(404, f"会话不存在: {sid}")
    return {"ok": True}


@router.post("/api/chat/attachments")
async def chat_upload_attachments(req: Request):
    """接收问答附件（粘贴的截图 / 上传的日志文件等），落盘后返回元数据。

    用 multipart 而不是 base64 JSON：截图动辄几 MB，JSON 里再 base64 一次会
    膨胀 33%，而且浏览器原生 FormData 就能直接构造，前端不必自己拼。
    落盘文件名由服务端生成（`att-<时间>-<随机>.<白名单后缀>`），用户给的文件名
    只作为展示名——它永远不会参与路径拼接，这是本项目所有落盘目录的一贯做法。
    """
    try:
        form = await req.form()
    except Exception as e:
        raise HTTPException(400, f"读取上传内容失败: {e}")
    upload = form.get("file")
    if upload is None:
        raise HTTPException(400, "没有收到文件（字段名应为 file）")
    if isinstance(upload, str):  # 表单里混了普通文本字段
        raise HTTPException(400, "file 字段必须是文件内容")
    try:
        data = await upload.read()
    except Exception as e:
        raise HTTPException(400, f"读取文件内容失败: {e}")
    name = str(getattr(upload, "filename", "") or "")
    ctype = str(getattr(upload, "content_type", "") or "")
    try:
        info = _astore_att().save(name, data, ctype)
    except AttachmentError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"保存附件失败: {e}")
    return {"attachment": _astore_att().public(info)}


@router.post("/api/chat/stream")
async def chat_stream(req: Request):
    """一问一答（SSE 流式）。只读仓库；需要改代码时落成「产出物」等人工采纳。"""
    if _CHAT_STATE["running"]:
        return JSONResponse({"error": "上一条还在回答，请等它结束"}, status_code=409)
    body = await _body_json(req)
    text = str(body.get("message") or "").strip()
    # 附件：用户在输入框里粘贴的图片 / 上传的文件。认不出的 id 静默丢弃，
    # 不因为一个过期附件把整次提问拦下（前端会把丢弃情况如实提示）。
    att_store = _astore_att()
    raw_ids = body.get("attachment_ids")
    if not isinstance(raw_ids, list):
        raw_ids = []
    infos = att_store.many([str(i) for i in raw_ids])
    if not text and not infos:
        # 「只贴一张图、什么都不说」是真实且常见的用法（截图本身就是问题），
        # 所以这里放行；但两个都空时必须拦住，否则模型只能凭空回答。
        raise HTTPException(400, "请输入内容，或粘贴一张截图 / 上传一个文件")
    att_public = [att_store.public(i) for i in infos]
    dropped = len([i for i in raw_ids if i]) - len(infos)
    use_repo = _as_bool(body.get("use_repo"), True)
    allow_patch = _as_bool(body.get("allow_patch"), True)

    store = _cstore()
    sid = str(body.get("session_id") or "").strip()
    sess = store.get(sid) if sid else None
    if sess is None:
        sess = store.create()
    sid = sess["id"]
    # 历史要在写入本轮提问**之前**取，否则同一个问题会出现两次
    history = list(sess.get("messages") or [])

    s = _load_settings()
    ai = AIModel(dict(_pipeline_config(s)["ai"]))
    root = Path(str(s["repo"]["path"]))
    reader = RepoReader(str(root), tools_enabled=bool(use_repo and root.exists()))
    skills_text = _skills_text(s, "chat")
    # 每个启用的 MCP server 都要做一次 initialize/tools/list 握手（起子进程或走 HTTP），
    # 不能留在事件循环上：否则每条带工具的消息都先卡住整个界面。
    tool_specs, tool_executor = await run_in_threadpool(_mcp_toolkit, s)
    title = (sess.get("title") or text)[:CHAT_TITLE_MAX]
    # 图片在**请求线程**里先转成 base64：多像素图编码不便宜，放进 worker 会白占
    # 一次模型调用的时间窗（且 base64 结果与磁盘状态无关，提前算不会过期）。
    images = images_for(att_store, infos)
    note = attach_note(infos)
    att_text = text_block(att_store, infos)
    _CHAT_STATE["running"] = True

    def worker(emit):
        store.append(sid, "user", text,
                     {"attachments": att_public} if att_public else None)
        emit({"type": "stage", "stage": "已收到，开始处理"})
        if att_public:
            img_n = len([a for a in att_public if a.get("kind") == "image"])
            txt_n = len(att_public) - img_n
            emit({"type": "stage", "stage": (
                f"已附上 {img_n} 张图片" + (f"、{txt_n} 个文本附件" if txt_n else "")
                + ("（图片已随提问发给模型）" if img_n else ""))})
        if dropped:
            emit({"type": "stage",
                  "stage": f"有 {dropped} 个附件已失效被跳过（可能已被清理）"})
        if not use_repo:
            emit({"type": "stage", "stage": "本轮未开启仓库检索，只依据你给的信息回答"})
        elif not root.exists():
            emit({"type": "stage", "stage": f"仓库路径不存在（{root}），已按无检索处理"})

        try:
            if ai.mode == "mock":  # mock 或不配 key 都走这里，绝不假装是真回答
                result = mock_chat_reply(text, allow_patch=allow_patch)
            else:
                def _job():
                    # 用量归属必须在线程内部设置（上下文不跨线程）
                    with usage.task("chat", run_id=sid, title=title, step="chat"):
                        return run_chat(ai, reader, history, text,
                                        skills_text=skills_text,
                                        allow_patch=allow_patch,
                                        extra_specs=tool_specs,
                                        extra_executor=tool_executor,
                                        on_event=emit,
                                        images=images,
                                        attachment_note=note,
                                        attachment_text=att_text)
                result = _job()
        except Exception as e:
            result = {"reply": "", "tools": [], "patch": None,
                      "error": f"问答执行失败: {e}", "ai_mode": ai.mode}

        artifact_summary = None
        reply = result.get("reply") or ""
        if result.get("patch") and not result["patch"].get("error"):
            try:
                # 标题用提案摘要（「这次改什么」）而不是会话标题，产出物列表里更好认
                art_title = (result["patch"].get("summary") or title
                             or "问答改动提案")[:CHAT_TITLE_MAX]
                art = _astore().create(
                    "chat", art_title, result["patch"],
                    {"notes": f"来自问答会话 {sid}", "scope": "问答"})
                artifact_summary = ArtifactStore.summary(art)
                emit({"type": "stage",
                      "stage": f"已生成改动提案（{artifact_summary['file_count']} 个文件），"
                               "在「产出物」里 review 后采纳"})
            except Exception as e:
                emit({"type": "stage", "stage": f"产出物创建失败: {e}"})
        if result.get("error"):
            emit({"type": "stage", "stage": str(result["error"])})

        meta = {"tools": result.get("tools") or [],
                "artifact": (artifact_summary or {}).get("id", ""),
                "ai_mode": result.get("ai_mode", ""),
                "error": result.get("error", ""),
                "rounds": result.get("rounds", 0)}
        if att_public:
            meta["attachments"] = att_public
        saved = store.append(sid, "assistant",
                             reply or f"（没有产出内容：{result.get('error') or '模型未返回正文'}）",
                             meta)
        emit({"type": "done", "payload": {
            "session_id": sid,
            "session": {"id": sid, "title": saved.get("title", ""),
                        "updated": saved.get("updated", ""), "count": len(saved.get("messages") or [])},
            "reply": reply,
            "error": result.get("error", ""),
            "tools": meta["tools"],
            "artifact": artifact_summary,
            "ai_mode": meta["ai_mode"],
            "rounds": meta["rounds"],
            "attachments": att_public,
            "dropped_attachments": dropped,
        }})

    return _queue_stream(asyncio.get_running_loop(), worker, state=_CHAT_STATE)
