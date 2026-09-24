"""Local Web UI for the ONES Frontend Dev Copilot (前端研发助手).

Run:  python -m web.server
Then: http://127.0.0.1:8765

Five modules share one safety contract:
- 问答       /api/chat/stream       → 只读仓库；附带的改动提案落成 artifacts
- 缺陷修复   /api/run + /api/probe  → proposals
- 需求开发   /api/tasks/run reqdev  → artifacts
- 接口联调   /api/tasks/run apidebug→ artifacts
- 代码测试   /api/tasks/run codetest→ artifacts
（长任务作业 /api/team/stream 同样产出 artifacts）

Only /api/proposals/{id}/approve and /api/artifacts/{id}/approve may touch the
target repository, and neither ever commits — write to the working tree only.

数据目录都可用环境变量改写（USAGE_DIR / CHAT_DIR / ARTIFACT_DIR / SETTINGS_FILE），
自检用例靠它们把临时实例指向临时目录，从而不碰真实配置与真实数据。
"""
import asyncio
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ai_model import AIModel  # noqa: E402
from scripts.ai_providers import AIConfig, AIError  # noqa: E402
from scripts.artifact import ArtifactApplier, ArtifactStore, diff_against_repo  # noqa: E402
from scripts.chat import ChatStore, RepoReader, mock_chat_reply, run_chat  # noqa: E402
from scripts.figma_fetcher import FigmaClient, FigmaError, parse_figma_url  # noqa: E402
from scripts.gate import resolve_commands  # noqa: E402
from scripts import mcp_client  # noqa: E402
from scripts.mock_data import SAMPLE_DEFECTS  # noqa: E402
from scripts.ones_fetcher import OnesClient  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402
from scripts.proposal import ProposalStore  # noqa: E402
from scripts.task_modules import TASK_LABELS, TaskError, mock_task_result, run_task  # noqa: E402
from scripts.team_bus import KINDS as TEAM_INTERVENTION_KINDS  # noqa: E402
from scripts.team_bus import create_bus, drop_bus, get_bus  # noqa: E402
from scripts.team_roles import public_roles  # noqa: E402
from scripts.team_run import TeamOrchestrator  # noqa: E402
from scripts.team_store import TeamRunStore  # noqa: E402
from scripts import usage  # noqa: E402
from scripts.usage import KIND_LABELS as USAGE_KIND_LABELS  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
# 六个数据目录默认落在项目根，**全部**可由环境变量覆盖——自检用例起临时实例时
# 把这些指到临时目录，就既读不到也写不进你的真实数据（约定见 README「测试」一节：
# 给本文件新增落盘目录时，请一并加一个环境变量开关）。缺目录会在启动时自动建。
def _data_dir(env: str, default: Path) -> Path:
    return Path(os.environ.get(env) or default)


KB_DIR = _data_dir("KB_DIR", PROJECT_ROOT / "knowledge_base")
SCREENSHOT_DIR = _data_dir("SCREENSHOT_DIR", PROJECT_ROOT / "screenshots")
PROPOSAL_DIR = _data_dir("PROPOSAL_DIR", PROJECT_ROOT / "proposals")
ARTIFACT_DIR = _data_dir("ARTIFACT_DIR", PROJECT_ROOT / "artifacts")
# 长任务作业（多子 Agent 协作）的运行记录：一次运行一份 JSON
TEAM_DIR = _data_dir("TEAM_DIR", PROJECT_ROOT / "team_runs")
# 问答会话：一个会话一份 JSON（追加式更新）
CHAT_DIR = _data_dir("CHAT_DIR", PROJECT_ROOT / "chat_sessions")
for _d in (KB_DIR, SCREENSHOT_DIR, PROPOSAL_DIR, ARTIFACT_DIR, TEAM_DIR, CHAT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
# 更新日志：项目根的纯文本，界面右上角「更新」入口的唯一数据源
CHANGELOG_FILE = PROJECT_ROOT / "CHANGELOG.md"
# 设置文件同样可由环境变量覆盖：自检用例起临时实例时指向临时设置，
# 从而**根本不会读到/写到你的真实配置**（此前的测试事故就是临时实例误改了真设置）。
SETTINGS_FILE = Path(os.environ.get("SETTINGS_FILE") or (PROJECT_ROOT / "ui_settings.json"))

DEFAULT_REPO = os.environ.get("REPO_PATH") or str(PROJECT_ROOT.parent / "test-mock-repo")

DEFAULT_SETTINGS = {
    "repo": {"path": DEFAULT_REPO, "branch": "main"},
    "ai": {
        "provider": os.environ.get("AI_PROVIDER", "anthropic"),
        "base_url": os.environ.get("AI_BASE_URL", ""),
        "model": os.environ.get("AI_MODEL", "claude-3-5-sonnet-latest"),
        "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "mock": not bool(os.environ.get("ANTHROPIC_API_KEY")),
        "temperature": 1.0,
        "max_tokens": 4000,
        "top_p": "",
        "timeout": 120,
        "proxy": os.environ.get("HTTPS_PROXY", ""),
        "verify_ssl": True,
        "endpoint": "",
        "models_path": "",
        "content_path": "",
        "auth_style": "",
        "auth_header": "",
        "json_mode": True,
        "max_tokens_field": "",
        "system_role": "",
        "extra_headers": {},
        "extra_body": {},
        "name": "",
    },
    "ones": {
        "base_url": os.environ.get("ONES_BASE_URL", ""),
        "token": os.environ.get("ONES_TOKEN", ""),
        "project_uuid": os.environ.get("ONES_PROJECT_UUID", ""),
        "team_uuid": os.environ.get("ONES_TEAM_UUID", ""),
        "email": os.environ.get("ONES_EMAIL", ""),
        "password": os.environ.get("ONES_PASSWORD", ""),
    },
    "figma": {
        "token": os.environ.get("FIGMA_TOKEN", ""),
    },
    "pagelogin": {
        # 页面验证用的登录凭据（截图前自动登录，见 verifier._login_creds）。
        # 与 Figma / ONES 的凭据彼此独立：这里登的是「被验收的前端应用」。
        "enabled": False,
        "email": os.environ.get("PAGE_LOGIN_EMAIL", ""),
        "password": os.environ.get("PAGE_LOGIN_PASSWORD", ""),
    },
    "extensions": {
        # 团队技能/规范：注入 AI 任务 system prompt
        "skills": [],
        # MCP 服务：stdio/http 配置，启用的服务在任务里作为工具可用
        "mcp": [],
    },
    "gate": {
        # one command per line, e.g. "npm run type-check"
        "commands_text": os.environ.get("GATE_COMMANDS", ""),
        "degraded": True,
    },
    "playwright": {
        "base_url": os.environ.get("APP_BASE_URL", "http://localhost:3000"),
        "headless": True,
    },
}


# --------------------------------------------------------------- settings io
def _mask(s: str) -> str:
    s = s or ""
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:3]}…{s[-4:]} ({len(s)} chars)"


async def _body_json(req: Request) -> dict:
    """Parse JSON body, tolerating GBK-encoded requests (Windows curl)."""
    raw = await req.body()
    if not raw:
        return {}
    for enc in ("utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return {}


def _deep_defaults() -> dict:
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def _load_settings() -> dict:
    merged = _deep_defaults()
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for section, vals in saved.items():
                if isinstance(vals, dict):
                    merged.setdefault(section, {}).update(vals)
                else:
                    merged[section] = vals
        except Exception:
            pass
    return merged


def _save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _settings_public(s: dict) -> dict:
    out = _deep_defaults()
    for section, vals in s.items():
        if isinstance(vals, dict):
            out.setdefault(section, {}).update(vals)
        else:
            out[section] = vals
    out["ai"]["api_key_masked"] = _mask(out["ai"].pop("api_key", ""))
    out["ones"]["token_masked"] = _mask(out["ones"].pop("token", ""))
    out["ones"]["password_masked"] = _mask(out["ones"].pop("password", ""))
    out.setdefault("figma", {})["token_masked"] = _mask(out.get("figma", {}).pop("token", ""))
    out.setdefault("pagelogin", {})
    out["pagelogin"]["password_masked"] = _mask(out["pagelogin"].pop("password", ""))
    out["has_api_key"] = bool(s["ai"].get("api_key"))
    out["has_token"] = bool(s["ones"].get("token"))
    out["has_password"] = bool(s["ones"].get("password"))
    out["has_figma_token"] = bool((s.get("figma") or {}).get("token"))
    out["has_page_password"] = bool((s.get("pagelogin") or {}).get("password"))
    # textareas want text, not nested JSON
    out["ai"]["extra_headers_text"] = json.dumps(
        s["ai"].get("extra_headers") or {}, ensure_ascii=False, indent=2)
    out["ai"]["extra_body_text"] = json.dumps(
        s["ai"].get("extra_body") or {}, ensure_ascii=False, indent=2)
    out["ai"]["resolved"] = _ai_preview(_resolve_ai(s))
    out["gate"]["commands"] = _gate_commands(s)
    return out


def _ai_preview(ai: dict) -> dict:
    cfg = AIConfig.from_dict(ai)
    return {
        "mode": cfg.mode,
        "chat_url": cfg.chat_url,
        "models_url": cfg.models_url,
        "auth_style": cfg.auth_style,
        "content_path": cfg.content_path,
        "endpoint": cfg.endpoint,
        "problems": cfg.validate(),
    }


def _gate_commands(s: dict) -> list:
    """config `gate.commands` entries derived from the editable text area."""
    out = []
    for line in (s.get("gate", {}).get("commands_text") or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split()[0]
        for hint, label in (("tsc", "typecheck"), ("lint", "lint"),
                            ("test", "test"), ("build", "build"), ("check", "typecheck")):
            if hint in line.lower():
                name = label
                break
        out.append({"name": name, "cmd": line})
    return out


def _as_bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "是")
    return bool(v)


def _as_num(v, default, integer=False):
    try:
        if v in (None, ""):
            return default
        n = float(v)
        return int(n) if integer else n
    except (TypeError, ValueError):
        return default


def _as_map(v, label) -> dict:
    """Accept a dict, a JSON object string, or `k: v` / `k=v` lines."""
    if isinstance(v, dict):
        return v
    if v in (None, ""):
        return {}
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
            raise ValueError(f"{label} 需要是 JSON 对象")
        except json.JSONDecodeError:
            out = {}
            for line in s.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                for sep in (":", "="):
                    if sep in line:
                        k, _, val = line.partition(sep)
                        out[k.strip().strip('"\'')] = val.strip().strip('"\'')
                        break
                else:
                    raise ValueError(f"{label} 第 {line[:24]!r} 行无法解析，请用 k: v")
            return out
    raise ValueError(f"{label} 格式不支持")


AI_STR_FIELDS = ("provider", "base_url", "model", "name", "endpoint", "models_path",
                 "content_path", "auth_style", "auth_header", "proxy",
                 "max_tokens_field", "system_role")
AI_BOOL_FIELDS = ("mock", "verify_ssl", "json_mode", "stream_usage")


def _merge_ai(target: dict, incoming: dict) -> None:
    """Apply a settings payload onto the stored ai config, keeping the key if blank."""
    for k in AI_STR_FIELDS:
        if k in incoming:
            target[k] = str(incoming[k] or "").strip()
    for k in AI_BOOL_FIELDS:
        if k in incoming:
            target[k] = _as_bool(incoming[k])
    if "temperature" in incoming:
        target["temperature"] = _as_num(incoming["temperature"], 1.0)
    if "max_tokens" in incoming:
        target["max_tokens"] = _as_num(incoming["max_tokens"], 4000, integer=True)
    if "timeout" in incoming:
        target["timeout"] = _as_num(incoming["timeout"], 120, integer=True)
    if "top_p" in incoming:
        target["top_p"] = _as_num(incoming["top_p"], "") if incoming["top_p"] not in (None, "") else ""
    for k, label in (("extra_headers", "额外请求头"), ("extra_body", "额外请求体")):
        if k in incoming:
            try:
                target[k] = _as_map(incoming[k], label)
            except ValueError as e:
                raise HTTPException(400, str(e))
    key = str(incoming.get("api_key") or "").strip()
    if key:
        target["api_key"] = key
    if _as_bool(incoming.get("clear_api_key")):
        target["api_key"] = ""


def _resolve_ai(s: dict) -> dict:
    """The ai config actually used for calls: key dropped when Mock is on."""
    ai = dict(s.get("ai") or {})
    if ai.get("mock"):
        ai["api_key"] = ""
    return ai


def _page_auth(s: dict) -> dict:
    """页面验证登录凭据 → verifier.auth。未启用/未填全则只留续期用的 storage_state。"""
    pl = s.get("pagelogin") or {}
    auth = {"storage_state": str(SCREENSHOT_DIR / "_page_session.json")}
    if pl.get("enabled") and pl.get("email") and pl.get("password"):
        auth["email"] = str(pl["email"]).strip()
        auth["password"] = str(pl["password"])
    return auth


def _pipeline_config(s: dict) -> dict:
    gate_cfg = {"degraded": bool(s["gate"].get("degraded", True))}
    commands = _gate_commands(s)
    if commands:
        gate_cfg["commands"] = commands
    return {
        "ones": dict(s["ones"]),
        "ai": _resolve_ai(s),
        "repo": dict(s["repo"]),
        "gate": gate_cfg,
        "playwright": {
            "base_url": s["playwright"]["base_url"],
            "headless": bool(s["playwright"].get("headless", True)),
            "screenshot_dir": str(SCREENSHOT_DIR),
            "auth": _page_auth(s),
        },
        "knowledge_base": {"output_dir": str(KB_DIR)},
        "proposals": {"output_dir": str(PROPOSAL_DIR)},
    }


def _store() -> ProposalStore:
    return ProposalStore(str(PROPOSAL_DIR))


def _astore() -> ArtifactStore:
    return ArtifactStore(str(ARTIFACT_DIR))


def _tstore() -> TeamRunStore:
    return TeamRunStore(str(TEAM_DIR))


def _cstore() -> ChatStore:
    return ChatStore(str(CHAT_DIR))


def _applier() -> ArtifactApplier:
    s = _load_settings()
    return ArtifactApplier(s["repo"]["path"], _pipeline_config(s)["gate"])


# ------------------------------------------------------------- extensions io
def _extensions(s: dict) -> dict:
    ext = s.setdefault("extensions", {})
    ext.setdefault("skills", [])
    ext.setdefault("mcp", [])
    return ext


def _clean_skill(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "技能名称不能为空")
    scope = raw.get("scope") or ["all"]
    if not isinstance(scope, list):
        scope = [scope]
    valid = {k for k in ("all", "reqdev", "apidebug", "codetest", "team") if k in scope}
    return {
        "id": str(raw.get("id") or re.sub(r"[^a-zA-Z0-9_-]", "", f"sk-{os.urandom(4).hex()}") or "sk"),
        "name": name,
        "description": str(raw.get("description") or "").strip()[:200],
        "content": str(raw.get("content") or "").strip()[:8000],
        "scope": sorted(valid) or ["all"],
        "enabled": bool(raw.get("enabled", True)),
    }


def _clean_mcp(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "MCP 服务名称不能为空")
    transport = "http" if str(raw.get("transport") or "").lower() == "http" else "stdio"
    out = {
        "id": str(raw.get("id") or re.sub(r"[^a-zA-Z0-9_-]", "", f"mcp-{os.urandom(4).hex()}") or "mcp"),
        "name": name,
        "transport": transport,
        "enabled": bool(raw.get("enabled", True)),
        "timeout": int(raw.get("timeout") or 30),
    }
    if transport == "stdio":
        out["command"] = str(raw.get("command") or "").strip()
        out["args"] = str(raw.get("args") or "").strip()
        out["env"] = str(raw.get("env") or "").strip()
        out["cwd"] = str(raw.get("cwd") or "").strip()
        if not out["command"]:
            raise HTTPException(400, f"MCP 服务「{name}」缺少启动命令")
    else:
        out["url"] = str(raw.get("url") or "").strip()
        out["headers"] = str(raw.get("headers") or "").strip()
        if not out["url"]:
            raise HTTPException(400, f"MCP 服务「{name}」缺少 URL")
    return out


def _skills_text(s: dict, kind: str) -> str:
    """Enabled skills scoped to `kind`, flattened into prompt text."""
    parts = []
    for sk in _extensions(s).get("skills") or []:
        if not sk.get("enabled"):
            continue
        scope = sk.get("scope") or ["all"]
        if "all" not in scope and kind not in scope:
            continue
        parts.append(f"### {sk.get('name', '')}\n{sk.get('content', '')}")
    return "\n\n".join(parts)


def _mcp_toolkit(s: dict):
    """(tool_specs, executor) for all enabled MCP servers; names prefixed with
    the server slug to avoid collisions. ([] , None) when nothing enabled."""
    servers = [m for m in _extensions(s).get("mcp") or [] if m.get("enabled")]
    specs, routes = [], {}
    for m in servers:
        prefix = re.sub(r"[^0-9A-Za-z_]", "_", m.get("name", "mcp")).strip("_") or "mcp"
        try:
            tools = mcp_client.list_tools(m)
        except Exception:
            continue  # 一个服务挂了不影响其他工具
        for t in tools:
            tname = str(t.get("name") or "").strip()
            if not tname:
                continue
            fq = f"{prefix}__{tname}"
            specs.append({"name": fq, "description": t.get("description") or "",
                          "inputSchema": t.get("inputSchema") or {"type": "object"}})
            routes[fq] = (m, tname)
    if not specs:
        return [], None

    def executor(fq: str, args: dict) -> str:
        m, tname = routes[fq]
        return mcp_client.call_tool(m, tname, args)

    return specs, executor


def _fresh_pipeline() -> AIDefectFixerPipeline:
    return AIDefectFixerPipeline(_pipeline_config(_load_settings()))


app = FastAPI(title="ONES 前端研发助手 UI (Frontend Dev Copilot)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOT_DIR)), name="screenshots")

_RUN_STATE = {"running": False}
# 问答占用标记：与缺陷流水线**各管各的**（可以一边跑流水线一边问问题），
# 但同一时刻只允许一个问答在跑，避免重复提交把会话写乱。
_CHAT_STATE = {"running": False}


# ------------------------------------------------------------------- basics
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
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
            pre = _fresh_pipeline().preflight
        except Exception as e:
            pre = {"problems": [str(e)]}
    counts = _store().counts()
    return {
        "ok": True,
        "running": bool(_RUN_STATE["running"]),
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


@app.get("/api/settings")
async def get_settings():
    return _settings_public(_load_settings())


@app.post("/api/settings")
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


@app.post("/api/ai/test")
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
    result = model.probe()
    result["mode"] = model.cfg.mode
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.get("/api/ai/providers")
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


@app.post("/api/ai/models")
async def fetch_ai_models(req: Request):
    body = await _body_json(req)
    model = AIModel(_candidate_ai(body))
    if model.cfg.is_mock:
        return JSONResponse({"error": "需要先填写 API Key（当前为 Mock 模式）"}, status_code=400)
    try:
        names = model.available_models()
    except AIError as e:
        return JSONResponse(e.to_dict(), status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"models": names, "count": len(names)}


# ------------------------------------------------------------------ stats
@app.get("/api/stats")
async def stats():
    stats_path = KB_DIR / "_stats.json"
    if not stats_path.exists():
        return {"categories": {}, "cards": 0}
    try:
        return json.loads(stats_path.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/index")
async def kb_index():
    idx = KB_DIR / "INDEX.md"
    return {"content": idx.read_text(encoding="utf-8") if idx.exists() else ""}


@app.get("/api/cards")
async def list_cards():
    if not KB_DIR.exists():
        return []
    cards = []
    for cat_dir in sorted(KB_DIR.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith((".", "_")):
            continue
        for f in sorted(cat_dir.glob("*.md")):
            front = _parse_frontmatter(f.read_text(encoding="utf-8"))
            cards.append({
                "category": cat_dir.name,
                "id": f.stem,
                "title": front.get("title", f.stem),
                "priority": front.get("priority", ""),
                "status": front.get("status", ""),
                "gate_level": front.get("gate_level", ""),
                "commit": (front.get("commit") or "")[:8],
                "proposal_id": front.get("proposal_id", ""),
                "created": front.get("created", ""),
                "updated": front.get("updated", front.get("created", "")),
            })
    return sorted(cards, key=lambda c: (c["category"], c["id"]))


@app.get("/api/card/{category}/{card_id}")
async def get_card(category: str, card_id: str):
    path = KB_DIR / category / f"{card_id}.md"
    if not path.exists():
        raise HTTPException(404, "card not found")
    content = path.read_text(encoding="utf-8")
    body = content.split("---", 2)[-1].strip() if content.startswith("---") else content
    return {
        "front": _parse_frontmatter(content),
        "body": body,
        "category": category,
        "id": card_id,
    }


@app.get("/api/patterns")
async def list_patterns():
    """模式列表（同类缺陷的共性沉淀，见 scripts/kb_patterns.py）。

    优先读 _patterns/_meta.json——它跟模式卡一起由重建流程写出，省掉逐张
    解析 markdown；文件缺失（老知识库、模式层还没跑过）时退化为扫描目录，
    再没有就返回空列表让前端显示空态。
    """
    pat_dir = KB_DIR / "_patterns"
    meta_path = pat_dir / "_meta.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            items = data.get("patterns") or []
            return sorted(items, key=lambda p: (-(p.get("recurrence") or 0), p.get("id", "")))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    if not pat_dir.exists():
        return []
    out = []
    for f in sorted(pat_dir.glob("*.md")):
        front = _parse_frontmatter(f.read_text(encoding="utf-8"))
        out.append({
            "id": front.get("pattern_id") or f.stem,
            "name": front.get("name") or f.stem,
            "symptom": front.get("symptom", ""),
            "module": front.get("module", ""),
            "recurrence": int(front.get("recurrence") or 0),
            "applied": int(front.get("applied") or 0),
            "categories": [c for c in (front.get("categories") or "").split(",") if c],
            "defects": [d.strip() for d in (front.get("defects") or "").split(",") if d.strip()],
        })
    return sorted(out, key=lambda p: (-p["recurrence"], p["id"]))


@app.get("/api/pattern/{pattern_id}")
async def get_pattern(pattern_id: str):
    path = KB_DIR / "_patterns" / f"{pattern_id}.md"
    if not path.exists():
        raise HTTPException(404, "pattern not found")
    content = path.read_text(encoding="utf-8")
    body = content.split("---", 2)[-1].strip() if content.startswith("---") else content
    front = _parse_frontmatter(content)
    # 关联缺陷的可点链接需要「卡片 id → 分类目录」，模式卡的 frontmatter
    # 只有缺陷 id，所以这里扫一遍卡片目录补上。
    cases = []
    wanted = [d.strip() for d in (front.get("defects") or "").split(",") if d.strip()]
    if wanted:
        found = {}
        for cat_dir in sorted(KB_DIR.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith((".", "_")):
                continue
            for f in sorted(cat_dir.glob("*.md")):
                if f.stem in wanted:
                    cf = _parse_frontmatter(f.read_text(encoding="utf-8"))
                    found[f.stem] = {
                        "id": f.stem,
                        "category": cat_dir.name,
                        "title": cf.get("title") or f.stem,
                        "status": cf.get("status", ""),
                    }
        cases = [found[d] for d in wanted if d in found]
    return {"front": front, "body": body, "id": pattern_id, "cases": cases}


@app.get("/api/changelog")
async def get_changelog(limit: Optional[int] = None, tag: Optional[str] = None):
    """更新日志（解析项目根 CHANGELOG.md，只读）。

    界面右上角「更新」入口读的就是这里。数据源是仓库里的纯文本，不做模型
    二次归纳——否则「改了什么」会出现第二个真相源，与文件对不上。
    文件缺失时返回空载荷（前端显示空态），不报错。
    """
    from scripts.changelog import load_changelog
    try:
        return await run_in_threadpool(load_changelog, CHANGELOG_FILE, limit, tag)
    except Exception as e:  # 日志格式异常不该让整个页面挂掉
        return JSONResponse({"error": f"解析更新日志失败: {e}", "entries": [],
                             "groups": [], "total": 0}, status_code=200)


@app.get("/api/usage/report")
async def usage_report(days: int = Query(30, ge=1, le=365),
                       granularity: str = Query("day")):
    """Token 用量报表（只读）。

    数据源是 `usage/usage.jsonl` 这个 append-only 账本——每次真实模型调用一行，
    由 `AIModel._record` 写入。聚合口径：

    - 网关回了 usage → 真值；没回（流式最常见）→ 按字符估算并打 `estimated` 标记。
      界面必须把两者区分显示，不能把估算值伪装成账单。
    - mock 调用不入账（不消耗 token），所以面板上的"调用次数"是真实调用次数。
    - 分桶区间内的**空桶照常返回**，前端才能画出真实的趋势（有断档就有断档）。
    """
    if granularity not in usage.GRANULARITIES:
        raise HTTPException(400, f"granularity 必须是 {'/'.join(usage.GRANULARITIES)}")
    try:
        rep = await run_in_threadpool(usage.report, days, granularity)
    except Exception as e:  # 统计是旁路能力，坏账本不该让页面报错
        return JSONResponse({"error": f"读取用量账本失败: {e}", "buckets": [],
                             "kinds": [], "models": [], "tasks": [], "recent": [],
                             "totals": {}, "all_time": {}, "today": {},
                             "this_week": {}, "this_month": {},
                             "range": {"days": days, "granularity": granularity}},
                            status_code=200)
    rep["kind_labels"] = USAGE_KIND_LABELS
    return rep


@app.get("/api/usage/kinds")
async def usage_kinds():
    """任务类型的中文名（前端图例用；避免中英文两份标签各自漂移）。"""
    return {"labels": USAGE_KIND_LABELS, "granularities": list(usage.GRANULARITIES),
            "ledger": str(usage.ledger_path())}


@app.get("/api/defects")
async def list_defects(mock: bool = False, limit: int = 10):
    """示例工单已移除：mock=true 现在返回空列表，绝不返回假工单。

    注意：这里没有配置 ONES 凭据时会直接回空列表，**不会**去调远端
    （否则未配置时前端会挂在超时上等待）。真正的拉取走 /api/probe。
    """
    if mock:
        return SAMPLE_DEFECTS[:limit]
    s = _load_settings()
    if not (s.get("ones") or {}).get("base_url"):
        return []
    try:
        o = s["ones"]
        return OnesClient(o.get("base_url", ""), token=o.get("token", ""),
                          project_uuid=o.get("project_uuid", ""),
                          team_uuid=o.get("team_uuid", ""),
                          email=o.get("email", ""),
                          password=o.get("password", "")).fetch_defects(limit=limit)
    except Exception as e:
        raise HTTPException(502, f"ONES 拉取失败: {e}")


# ------------------------------------------------------------------- run
def _summarize_results(pipeline, results: list) -> list:
    """/api/run 与 /api/run/stream 共用的结果摘要（前端渲染契约一致）。"""
    summary = []
    for r in results:
        prop = r.get("proposal") or {}
        patch = prop.get("patch") or r.get("patch") or {}
        summary.append({
            "proposal_id": prop.get("id"),
            "id": (r["defect"] or {}).get("id"),
            "title": str((r["defect"] or {}).get("title", ""))[:60],
            "category": (r["analysis"] or {}).get("category"),
            "locate_empty": bool((r["analysis"] or {}).get("locate_empty")),
            # 老提案没有该字段（analyzer 升级前落盘）→ 传 None，让前端按同样的
            # 信号词兜底重算；写死 False 会让历史提案的「非前端缺陷」结论丢失
            "non_frontend": (bool((r["analysis"] or {}).get("non_frontend"))
                             if "non_frontend" in (r["analysis"] or {}) else None),
            "status": prop.get("status"),
            "root_cause": (r["analysis"] or {}).get("root_cause", "")[:200],
            "files": patch.get("files", []),
            "gate_level": (prop.get("gate") or {}).get("level"),
            "gate_ok": (prop.get("gate") or {}).get("ok"),
            "errors": (patch.get("errors") or [])[:4],
            "warnings": (patch.get("warnings") or [])[:4],
            "verify_status": (r.get("verify") or {}).get("status"),
            "kb_path": r.get("kb_path"),
            "source": r.get("source"),
        })
    return summary


def _run_opts(req_body: dict, **query) -> dict:
    return {
        # 示例工单已移除，mock 恒为 False（运行流水线只处理真实 ONES 工单）；
        # defects 允许调用方（测试/高级用法）显式注入工单内容，优先于拉取
        "mock": False,
        "defects": req_body.get("defects") or None,
        "dry_run": req_body.get("dry_run", False) if query.get("dry_run") is None else query["dry_run"],
        "limit": int(req_body.get("limit", 5) if query.get("limit") is None else query["limit"]),
        "defect_id": req_body.get("defect_id", query.get("defect_id")) or None,
        "skip_verify": bool(req_body.get("skip_verify", False)),
        "mine_only": _as_bool(req_body.get("mine_only"), True),
    }


def _sse(gen_fn):
    return StreamingResponse(
        gen_fn(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


def _queue_stream(loop, worker, state: Optional[dict] = None) -> StreamingResponse:
    """SSE 桥：同步 worker 线程把事件经 call_soon_threadsafe 放进 asyncio.Queue，
    异步生成器持续吐出（15s 无事件发心跳注释防中间层掐断）。

    worker 内部是同步重活（ONES 网络 + AI + sync_playwright），绝不能进
    事件循环——这正是 /api/run 踩过的坑，这里从线程起步天然规避。
    ⚠ 不要用 run_in_executor 包阻塞的 queue.get：wait_for 超时会泄漏一个
    永久阻塞在 get() 上的执行器线程，长静默阶段（截图/AI 推理）会耗尽线程池。
    ⚠ `state` 是「占用标记」的归属字典，默认缺陷流水线的 `_RUN_STATE`。问答等
    独立流程必须传自己的字典，否则它跑完会把流水线的占用标记清掉（两条流程
    互不相干，共用一个标记会互相误放行）。
    """
    st = state if state is not None else _RUN_STATE
    aq: asyncio.Queue = asyncio.Queue()

    def emit(evt: dict) -> None:
        loop.call_soon_threadsafe(aq.put_nowait, dict(evt))

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
            st["running"] = False

    try:
        threading.Thread(target=job, daemon=True, name="pipeline-stream").start()
    except Exception:
        st["running"] = False  # 线程没起来也要解除占用
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


@app.post("/api/run")
async def run_pipeline(
    req: Request,
    mock: Optional[bool] = Query(None),
    dry_run: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)

    body = await _body_json(req)
    opts = _run_opts(body, dry_run=dry_run, limit=limit, defect_id=defect_id)

    _RUN_STATE["running"] = True
    try:
        pipeline = _fresh_pipeline()
        # 必须跑在工作线程里：pipeline.run → verifier.capture → sync_playwright
        # 在 asyncio 事件循环内会直接抛 "Playwright Sync API inside the
        # asyncio loop"，导致页面验收/截图整体 error。
        results = await run_in_threadpool(
            pipeline.run,
            defect_id=opts["defect_id"], mock=opts["mock"],
            dry_run=opts["dry_run"], limit=opts["limit"],
            skip_verify=opts["skip_verify"], mine_only=opts["mine_only"],
            defects=opts["defects"],
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        _RUN_STATE["running"] = False

    summary = _summarize_results(pipeline, results)
    return {
        "count": len(summary),
        "ai_mode": pipeline.ai.mode,
        "wrote_any_files": False,
        "results": summary,
    }


@app.post("/api/run/stream")
async def run_pipeline_stream(
    req: Request,
    dry_run: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """「运行」的流式版本：SSE 实时推送阶段事件与 AI 思考/输出增量。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)
    body = await _body_json(req)
    opts = _run_opts(body, dry_run=dry_run, limit=limit, defect_id=defect_id)
    _RUN_STATE["running"] = True

    def worker(emit):
        pipeline = _fresh_pipeline()
        results = pipeline.run(
            defect_id=opts["defect_id"], mock=opts["mock"],
            dry_run=opts["dry_run"], limit=opts["limit"],
            skip_verify=opts["skip_verify"], mine_only=opts["mine_only"],
            defects=opts["defects"], emit=emit,
        )
        emit({"type": "done", "payload": {
            "count": len(results),
            "ai_mode": pipeline.ai.mode,
            "wrote_any_files": False,
            "results": _summarize_results(pipeline, results),
        }})

    return _queue_stream(asyncio.get_running_loop(), worker)


# ------------------------------------------------------------------ probe
@app.post("/api/probe")
async def probe_ones(
    req: Request,
    mock: Optional[bool] = Query(None),
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """试运行：只拉取 ONES 工单 + AI 定位，不生成提案、不进审批、不写任何文件。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)

    body = await _body_json(req)
    # 示例工单已移除，mock 恒为 False（试运行只拉真实 ONES 工单）
    m = False
    lim = int(body.get("limit", 5) if limit is None else limit)
    did = body.get("defect_id", defect_id) or None
    locate = bool(body.get("locate", True))
    days = int(body.get("days", 0) or 0)
    mine_only = _as_bool(body.get("mine_only"), True)

    _RUN_STATE["running"] = True
    try:
        pipeline = _fresh_pipeline()
        # 同步重活（ONES 网络请求 + AI 定位）丢进线程池，避免阻塞事件循环
        data = await run_in_threadpool(
            pipeline.probe, defect_id=did, mock=m, limit=lim,
            locate=locate, days=days, mine_only=mine_only,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        _RUN_STATE["running"] = False
    return data


@app.post("/api/probe/stream")
async def probe_ones_stream(
    req: Request,
    limit: Optional[int] = Query(None),
    defect_id: Optional[str] = Query(None),
):
    """「试运行」的流式版本：拉取 + AI 定位过程实时可见。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有流水线在运行，请等待完成"}, status_code=409)
    body = await _body_json(req)
    lim = int(body.get("limit", 5) if limit is None else limit)
    did = body.get("defect_id", defect_id) or None
    locate = bool(body.get("locate", True))
    days = int(body.get("days", 0) or 0)
    mine_only = _as_bool(body.get("mine_only"), True)
    _RUN_STATE["running"] = True

    def worker(emit):
        pipeline = _fresh_pipeline()
        data = pipeline.probe(
            defect_id=did, mock=False, limit=lim,
            locate=locate, days=days, mine_only=mine_only, emit=emit,
        )
        emit({"type": "done", "payload": data})

    return _queue_stream(asyncio.get_running_loop(), worker)


# ------------------------------------------------------- figma / tasks / artifacts
@app.get("/api/repo/file")
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


@app.post("/api/figma/fetch")
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


@app.post("/api/tasks/run")
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


# ------------------------------------------------------------------ 问答
# 「不一定是开发或修缺陷」时的通用入口：随便问、顺手改。
# 只读仓库（RepoReader），要改代码时只产出「产出物」——写入仍只发生在采纳那一步。
CHAT_TITLE_MAX = 60


@app.get("/api/chat/sessions")
async def chat_list_sessions():
    return {"sessions": _cstore().list(), "running": bool(_CHAT_STATE["running"])}


@app.post("/api/chat/sessions")
async def chat_new_session(req: Request):
    body = await _body_json(req)
    sess = _cstore().create(title=str(body.get("title") or "").strip())
    return {"session": sess}


@app.get("/api/chat/sessions/{sid}")
async def chat_get_session(sid: str):
    sess = _cstore().get(sid)
    if not sess:
        raise HTTPException(404, f"会话不存在: {sid}")
    return {"session": sess}


@app.post("/api/chat/sessions/{sid}/clear")
async def chat_clear_session(sid: str):
    sess = _cstore().clear(sid)
    if not sess:
        raise HTTPException(404, f"会话不存在: {sid}")
    return {"ok": True, "session": sess}


@app.post("/api/chat/sessions/{sid}/delete")
async def chat_delete_session(sid: str):
    if not _cstore().delete(sid):
        raise HTTPException(404, f"会话不存在: {sid}")
    return {"ok": True}


@app.post("/api/chat/stream")
async def chat_stream(req: Request):
    """一问一答（SSE 流式）。只读仓库；需要改代码时落成「产出物」等人工采纳。"""
    if _CHAT_STATE["running"]:
        return JSONResponse({"error": "上一条还在回答，请等它结束"}, status_code=409)
    body = await _body_json(req)
    text = str(body.get("message") or "").strip()
    if not text:
        raise HTTPException(400, "请输入内容")
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
    tool_specs, tool_executor = _mcp_toolkit(s)
    title = (sess.get("title") or text)[:CHAT_TITLE_MAX]
    _CHAT_STATE["running"] = True

    def worker(emit):
        store.append(sid, "user", text)
        emit({"type": "stage", "stage": "已收到，开始处理"})
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
                                        on_event=emit)
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
        }})

    return _queue_stream(asyncio.get_running_loop(), worker, state=_CHAT_STATE)


@app.get("/api/extensions")
async def get_extensions():
    ext = _extensions(_load_settings())
    return {"skills": ext.get("skills", []), "mcp": ext.get("mcp", [])}


@app.post("/api/extensions/skills")
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


@app.post("/api/extensions/mcp")
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


@app.post("/api/extensions/mcp/test")
async def test_mcp(req: Request):
    """对未保存/已保存的 MCP 配置做 initialize + tools/list 探测。"""
    body = await _body_json(req)
    try:
        cfg = _clean_mcp(body.get("server") or {})
    except HTTPException as e:
        raise HTTPException(400, str(e.detail))
    result = mcp_client.test_server(cfg)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


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


@app.get("/api/team/roles")
async def team_roles():
    """四个子 Agent 的角色卡（职责 / 阶段 / 产出），前端看板按它渲染。"""
    return {"roles": public_roles()}


@app.get("/api/team/runs")
async def team_runs_list():
    runs = _tstore().list()
    # 标出哪些还在跑：刷新页面（SSE 断开）后用户能一眼看出"它其实还在跑"，
    # 而不是误以为作业已经挂了。
    for r in runs:
        r["running"] = bool(get_bus(r.get("id") or ""))
    return runs


@app.get("/api/team/runs/{rid}")
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


@app.post("/api/team/runs/{rid}/intervene")
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


@app.post("/api/team/runs/{rid}/abort")
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


@app.post("/api/team/stream")
async def team_stream(req: Request):
    """启动一次长任务作业，SSE 实时推送：每个子 Agent 的状态、当前动作、
    思考与输出增量，以及计划/工作项/复核结论的流转。"""
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "已有任务在运行，请等待完成"}, status_code=409)
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
    _RUN_STATE["running"] = True

    def worker(emit):
        emit({"type": "run_id", "run_id": rid})
        try:
            # 用量归属必须在**工作线程内部**设置，才能保证每个角色/步骤都被正确记账
            with usage.task("team", run_id=rid,
                            title=spec.get("title") or (spec["task"] or "")[:60]):
                orch = TeamOrchestrator(config, store, bus)
                orch.run(spec, emit=emit)
        finally:
            drop_bus(rid)

    return _queue_stream(asyncio.get_running_loop(), worker)


@app.get("/api/artifacts")
async def list_artifacts(type: Optional[str] = None, status: Optional[str] = None):
    return _astore().list(type_filter=type, status=status)
@app.get("/api/artifacts/{aid}")
async def get_artifact(aid: str):
    a = _astore().get(aid)
    if not a:
        raise HTTPException(404, f"产出物不存在: {aid}")
    s = _load_settings()
    a["repo"] = {"path": s["repo"]["path"], "branch": s["repo"]["branch"]}
    try:
        a["preflight_live"] = _applier().preflight()
    except Exception as e:
        a["preflight_live"] = {"problems": [str(e)]}
    return a


@app.get("/api/artifacts/{aid}/diff")
async def diff_artifact(aid: str):
    """Compare artifact files against the current working tree (read-only).

    Returns per-file aligned line diff so the UI can highlight what changes.
    """
    a = _astore().get(aid)
    if not a:
        raise HTTPException(404, f"产出物不存在: {aid}")
    s = _load_settings()
    repo_root = s["repo"]["path"]
    try:
        return await run_in_threadpool(diff_against_repo, a, repo_root)
    except Exception as e:
        return {"files": [], "error": str(e)}


@app.post("/api/artifacts/{aid}/approve")
async def approve_artifact(aid: str, req: Request):
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "任务正在运行，稍后再采纳"}, status_code=409)
    body = await _body_json(req)
    force = bool(body.get("force_gate"))
    note = str(body.get("note") or "")
    if force and not body.get("confirm_risk"):
        return JSONResponse({"error": "强制采纳需要明确勾选“我已知悉风险”"}, status_code=400)
    store = _astore()
    artifact = store.get(aid)
    if not artifact:
        raise HTTPException(404, f"产出物不存在: {aid}")
    result = _applier().apply(artifact, force_gate=force)
    status = result.get("status", "failed")
    if result.get("ok"):
        from datetime import datetime as _dt
        store.set_status(aid, "applied",
                         apply={"files": result.get("files", []),
                                "backups": result.get("backups", {}),
                                "gate": result.get("gate", {}),
                                "forced": result.get("forced", False),
                                "committed": False,
                                "note": note,
                                "decided_at": _dt.now().isoformat(timespec="seconds")})
    elif status == "apply_failed":
        store.set_status(aid, "apply_failed", apply={"gate": result.get("gate", {})})
    result["artifact_id"] = aid
    code = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=code)


@app.post("/api/artifacts/{aid}/reject")
async def reject_artifact(aid: str, req: Request):
    body = await _body_json(req)
    store = _astore()
    a = store.get(aid)
    if not a:
        raise HTTPException(404, f"产出物不存在: {aid}")
    store.set_status(aid, "rejected",
                     decision={"rejected": True, "note": str(body.get("note") or "")})
    return {"ok": True}


@app.post("/api/artifacts/{aid}/undo")
async def undo_artifact(aid: str):
    store = _astore()
    artifact = store.get(aid)
    if not artifact:
        raise HTTPException(404, f"产出物不存在: {aid}")
    result = _applier().undo(artifact)
    if result.get("ok"):
        store.set_status(aid, "pending", apply={})
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


# --------------------------------------------------------------- proposals
@app.get("/api/proposals")
async def list_proposals(status: Optional[str] = None):
    return _store().list(status=status)


@app.get("/api/proposals/{pid}")
async def get_proposal(pid: str):
    p = _store().get(pid)
    if not p:
        raise HTTPException(404, f"提案不存在: {pid}")
    patch = dict(p.get("patch") or {})
    patch.pop("patched_contents", None)  # bulky and never shown
    p["patch"] = patch
    s = _load_settings()
    p["repo"] = {"path": s["repo"]["path"], "branch": s["repo"]["branch"]}
    try:
        p["preflight_live"] = _fresh_pipeline().preflight
    except Exception as e:
        p["preflight_live"] = {"problems": [str(e)]}
    p["screenshot_base"] = "/screenshots"
    return p


@app.post("/api/proposals/{pid}/approve")
async def approve_proposal(pid: str, req: Request):
    if _RUN_STATE["running"]:
        return JSONResponse({"error": "流水线正在运行，稍后再采纳"}, status_code=409)
    body = await _body_json(req)
    force = bool(body.get("force_gate"))
    note = str(body.get("note") or "")
    if force and not body.get("confirm_risk"):
        return JSONResponse({"error": "强制采纳需要明确勾选“我已知悉风险”"}, status_code=400)

    # approve 会拍「采纳后」截图（sync_playwright），必须跑在工作线程里
    pipeline = _fresh_pipeline()
    result = await run_in_threadpool(pipeline.approve, pid, force_gate=force, note=note)
    code = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=code)


@app.post("/api/proposals/{pid}/reject")
async def reject_proposal(pid: str, req: Request):
    body = await _body_json(req)
    result = _fresh_pipeline().reject(pid, note=str(body.get("note") or ""))
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@app.post("/api/proposals/{pid}/undo")
async def undo_proposal(pid: str):
    result = _fresh_pipeline().undo(pid)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


# ------------------------------------------------------------------ helpers
def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


if __name__ == "__main__":
    import uvicorn

    # 端口默认 8765；PORT 可覆盖，便于同时开一个临时实例做自检而不打断手头这个。
    _port = int(os.environ.get("PORT") or 8765)
    print(f"[web] http://127.0.0.1:{_port}  (Ctrl+C 退出)")
    uvicorn.run(app, host="127.0.0.1", port=_port, log_level="warning")
