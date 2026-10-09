"""配置面：settings 读写、模型接入与连通性测试、扩展能力（skills / MCP）。

由 web/server.py 按端点分组切出（第三梯队结构性重构）。切分只搬位置、不改行为：
共享状态与配置一律从 web.state 取，端点的路径/入参/返回结构与切分前一致。
"""

from pathlib import Path

from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from web.state import (_as_bool, _as_num, _body_json, _clean_mcp, _clean_skill,
                       _extensions, _load_settings, _merge_ai, _save_settings,
                       _settings_public)
from scripts import mcp_client  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.ai_providers import AIError  # noqa: E402

router = APIRouter()



@router.get("/api/settings")
async def get_settings():
    return _settings_public(_load_settings())


@router.post("/api/settings")
async def update_settings(req: Request):
    body = await _body_json(req)
    s = _load_settings()

    repo = body.get("repo") or {}
    if "path" in repo:
        path = str(repo["path"]).strip().strip('"')
        if path and not Path(path).exists():
            raise HTTPException(400, f"仓库路径不存在: {path}")
        s["repo"]["path"] = path
    if "branch" in repo:
        s["repo"]["branch"] = str(repo["branch"]).strip()

    ai = body.get("ai") or {}
    _merge_ai(s.setdefault("ai", {}), ai)

    ones = body.get("ones") or {}
    for k in ("base_url", "project_uuid", "team_uuid", "email"):
        if k in ones:
            s["ones"][k] = str(ones[k]).strip()
    if ones.get("token"):
        s["ones"]["token"] = ones["token"].strip()
    if ones.get("password"):
        s["ones"]["password"] = ones["password"].strip()

    figma = body.get("figma") or {}
    s.setdefault("figma", {})
    if figma.get("token"):
        s["figma"]["token"] = str(figma["token"]).strip()
    if _as_bool(figma.get("clear_token")):
        s["figma"]["token"] = ""

    pl = body.get("pagelogin") or {}
    s.setdefault("pagelogin", {})
    if "enabled" in pl:
        s["pagelogin"]["enabled"] = bool(pl["enabled"])
    if "email" in pl:
        s["pagelogin"]["email"] = str(pl["email"]).strip()
    if pl.get("password"):
        s["pagelogin"]["password"] = str(pl["password"])
    if _as_bool(pl.get("clear_password")):
        s["pagelogin"]["password"] = ""

    gate = body.get("gate") or {}
    if "commands_text" in gate:
        s["gate"]["commands_text"] = str(gate["commands_text"])
    if "degraded" in gate:
        s["gate"]["degraded"] = bool(gate["degraded"])

    pw = body.get("playwright") or {}
    if "base_url" in pw:
        s["playwright"]["base_url"] = str(pw["base_url"]).strip().rstrip("/")
    if "headless" in pw:
        s["playwright"]["headless"] = bool(pw["headless"])

    ag = body.get("agent") or {}
    if ag:
        cur = s.setdefault("agent", {})
        if "enabled" in ag:
            cur["enabled"] = _as_bool(ag["enabled"], True)
        if "page_read" in ag:
            cur["page_read"] = _as_bool(ag["page_read"], True)
        if "link_node_modules" in ag:
            cur["link_node_modules"] = _as_bool(ag["link_node_modules"], True)
        for key, lo, hi in (("max_rounds", 1, 24), ("deadline_seconds", 60, 1800),
                            ("max_stall", 1, 8), ("per_command_timeout", 15, 1800),
                            ("sandbox_server_ready_timeout", 20, 600),
                            ("sandbox_server_max_checks", 1, 5)):
            if key in ag:
                n = _as_num(ag[key], None)
                if n is not None:
                    cur[key] = int(min(max(n, lo), hi))
        if "sandbox" in ag:
            mode = str(ag["sandbox"]).strip().lower()
            cur["sandbox"] = mode if mode in ("auto", "copy", "worktree") else "auto"
        if "repro" in ag:
            rm = str(ag["repro"]).strip().lower()
            cur["repro"] = rm if rm in ("auto", "on", "off") else "auto"
        if "repro_command" in ag:
            cur["repro_command"] = str(ag["repro_command"]).strip()[:500]
        if "repro_max_rewrite" in ag:
            n = _as_num(ag["repro_max_rewrite"], None)
            if n is not None:
                cur["repro_max_rewrite"] = int(min(max(n, 0), 6))
        if "sandbox_server" in ag:
            ss = str(ag["sandbox_server"]).strip().lower()
            cur["sandbox_server"] = ss if ss in ("auto", "on", "off") else "auto"
        for key, cap in (("sandbox_server_command", 500), ("sandbox_server_cwd", 200)):
            if key in ag:
                cur[key] = str(ag[key]).strip()[:cap]
        if "api_probe" in ag:
            ap = str(ag["api_probe"]).strip().lower()
            cur["api_probe"] = ap if ap in ("auto", "on", "off") else "auto"
        if "api_probe_hosts" in ag:
            cur["api_probe_hosts"] = str(ag["api_probe_hosts"]).strip()[:400]
        if "api_probe_timeout" in ag:
            n = _as_num(ag["api_probe_timeout"], None)
            if n is not None:
                cur["api_probe_timeout"] = int(min(max(n, 3), 60))
        if "precedents" in ag:
            n = _as_num(ag["precedents"], None)
            if n is not None:
                cur["precedents"] = int(min(max(n, 0), 8))
        if "precedents_chars" in ag:
            n = _as_num(ag["precedents_chars"], None)
            if n is not None:
                cur["precedents_chars"] = int(min(max(n, 800), 30000))
        if "sandbox_dir" in ag:
            cur["sandbox_dir"] = str(ag["sandbox_dir"]).strip().strip('"')
        if "command_allow_text" in ag:
            cur["command_allow"] = [ln.strip() for ln in
                                    str(ag["command_allow_text"]).splitlines()
                                    if ln.strip() and not ln.strip().startswith("#")]

    _save_settings(s)
    return _settings_public(s)


# ------------------------------------------------------------------ AI 接入
def _candidate_ai(body: dict) -> dict:
    """Stored ai config overlaid with the (possibly unsaved) values from the form."""
    s = _load_settings()
    merged = dict(s.get("ai") or {})
    incoming = dict(body.get("ai") or body or {})
    _merge_ai(merged, incoming)
    if incoming.get("mock"):
        merged["api_key"] = ""
    return merged


@router.post("/api/ai/test")
async def test_ai(req: Request):
    """Probe the configured endpoint before saving it — the whole point of the
    relay support is that a wrong path/header is visible immediately."""
    body = await _body_json(req)
    ai = _candidate_ai(body)
    model = AIModel(ai)
    if body.get("dry"):
        cfg = model.cfg
        return {"ok": not cfg.validate(), "dry_run": True, "mode": cfg.mode,
                "errors": cfg.validate(), "request": model.describe_request()}
    if model.cfg.is_mock:
        return {"ok": True, "mode": "mock", "mock": True,
                "note": "当前是 Mock 模式（未填 API Key 或勾选了 Mock），不会发起网络请求",
                "errors": [], "request": model.describe_request()}
    # 连通性探测会一直等到模型超时（默认 120s）——必须离开事件循环，
    # 否则「测试连接」期间整个界面（含健康轮询）都会冻住。
    result = await run_in_threadpool(model.probe)
    result["mode"] = model.cfg.mode
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.get("/api/ai/providers")
async def ai_providers():
    """Defaults the UI offers as starting points; user may override every field."""
    return {
        "presets": [
            {
                "id": "anthropic",
                "label": "Anthropic 官方",
                "provider": "anthropic",
                "base_url": "",
                "endpoint": "/v1/messages",
                "models_path": "/v1/models",
                "content_path": "content.0.text",
                "auth_style": "x-api-key",
                "json_mode": False,
                "models": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest",
                           "claude-sonnet-4-5", "claude-opus-4-1"],
            },
            {
                "id": "openai",
                "label": "OpenAI 官方",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "endpoint": "/chat/completions",
                "models_path": "/models",
                "content_path": "choices.0.message.content",
                "auth_style": "bearer",
                "json_mode": True,
                "models": ["gpt-4o", "gpt-4o-mini"],
            },
            {
                "id": "relay",
                "label": "中转站 / One-API 类网关（OpenAI 兼容）",
                "provider": "openai",
                "base_url": "https://your-relay.example.com/v1",
                "endpoint": "/chat/completions",
                "models_path": "/models",
                "content_path": "choices.0.message.content",
                "auth_style": "bearer",
                "json_mode": True,
                "models": [],
            },
            {
                "id": "local",
                "label": "本地推理（Ollama / vLLM / LM Studio）",
                "provider": "openai",
                "base_url": "http://127.0.0.1:11434/v1",
                "endpoint": "/chat/completions",
                "models_path": "/models",
                "content_path": "choices.0.message.content",
                "auth_style": "none",
                "json_mode": True,
                "models": [],
            },
            {
                "id": "custom",
                "label": "完全自定义（自定路径与响应取值）",
                "provider": "custom",
                "base_url": "",
                "endpoint": "/chat/completions",
                "models_path": "/models",
                "content_path": "choices.0.message.content",
                "auth_style": "bearer",
                "json_mode": True,
                "models": [],
            },
        ],
        "auth_styles": [
            {"id": "bearer", "label": "Authorization: Bearer <key>"},
            {"id": "x-api-key", "label": "x-api-key: <key>（Anthropic 风格）"},
            {"id": "header", "label": "自定义请求头携带 key"},
            {"id": "query", "label": "URL query 参数携带 key"},
            {"id": "none", "label": "不带 key（IP 白名单/内网）"},
        ],
    }


@router.post("/api/ai/models")
async def fetch_ai_models(req: Request):
    body = await _body_json(req)
    model = AIModel(_candidate_ai(body))
    if model.cfg.is_mock:
        return JSONResponse({"error": "需要先填写 API Key（当前为 Mock 模式）"}, status_code=400)
    try:
        # 同样走工作线程：列模型是一次真实的 HTTP 请求
        names = await run_in_threadpool(model.available_models)
    except AIError as e:
        return JSONResponse(e.to_dict(), status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"models": names, "count": len(names)}



@router.get("/api/extensions")
async def get_extensions():
    ext = _extensions(_load_settings())
    return {"skills": ext.get("skills", []), "mcp": ext.get("mcp", [])}


@router.post("/api/extensions/skills")
async def set_skills(req: Request):
    """整体替换技能列表（前端每次保存都传全量）。"""
    body = await _body_json(req)
    skills = body.get("skills")
    if not isinstance(skills, list):
        raise HTTPException(400, "skills 必须是数组")
    cleaned = [_clean_skill(x) for x in skills]
    s = _load_settings()
    _extensions(s)["skills"] = cleaned
    _save_settings(s)
    return {"ok": True, "skills": cleaned, "count": len(cleaned)}


@router.post("/api/extensions/mcp")
async def set_mcp(req: Request):
    """整体替换 MCP 服务列表（前端每次保存都传全量）。"""
    body = await _body_json(req)
    servers = body.get("servers")
    if not isinstance(servers, list):
        raise HTTPException(400, "servers 必须是数组")
    cleaned = [_clean_mcp(x) for x in servers]
    s = _load_settings()
    _extensions(s)["mcp"] = cleaned
    _save_settings(s)
    return {"ok": True, "servers": cleaned, "count": len(cleaned)}


@router.post("/api/extensions/mcp/test")
async def test_mcp(req: Request):
    """对未保存/已保存的 MCP 配置做 initialize + tools/list 探测。"""
    body = await _body_json(req)
    try:
        cfg = _clean_mcp(body.get("server") or {})
    except HTTPException as e:
        raise HTTPException(400, str(e.detail))
    # MCP 探测要起子进程 + 走 initialize/tools/list 握手，同样不能占着事件循环
    result = await run_in_threadpool(mcp_client.test_server, cfg)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)
