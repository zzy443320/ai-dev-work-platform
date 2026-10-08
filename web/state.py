"""共享内核：数据目录、settings 读写与掩码、store 工厂、流水线配置、运行态标记。

被 server.py 与所有 routers 依赖，因此这里只依赖 scripts/*，不引用 app、也不定义任何路由。
"""

import json
import os
import re
import sys
from pathlib import Path

from fastapi import HTTPException, Request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ai_providers import AIConfig  # noqa: E402
from scripts.artifact import ArtifactApplier, ArtifactStore  # noqa: E402
from scripts.chat import ChatStore  # noqa: E402
from scripts.chat_attachments import (  # noqa: E402
    ChatAttachmentStore)
from scripts import mcp_client  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402
from scripts.proposal import ProposalStore  # noqa: E402
from scripts.team_store import TeamRunStore  # noqa: E402
from scripts.textutil import mask_secret

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
# 前端构建产物：由 frontend/ 下 `npm run build` 生成，入口 static/v2.html（沿用
# 原文件名，指向 Vue bundle）。**旧版原生界面（index.html + app.js）已在
# Vite + Vue 3 迁移收口时删除**，`/` 直接就是这份产物；改动界面请改 frontend/src。
# 回归旧版请走 git 历史，不要在本仓库里留第二份界面实现。
VUE_ENTRY = STATIC_DIR / "v2.html"
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
# 问答附件（用户粘贴/上传的图片与文本）：一个附件一个文件 + 一份 .meta.json
ATTACH_DIR = _data_dir("ATTACH_DIR", PROJECT_ROOT / "attachments")
for _d in (KB_DIR, SCREENSHOT_DIR, PROPOSAL_DIR, ARTIFACT_DIR, TEAM_DIR, CHAT_DIR,
           ATTACH_DIR):
    _d.mkdir(parents=True, exist_ok=True)
# 更新日志：项目根的纯文本，界面右上角「更新」入口的唯一数据源
CHANGELOG_FILE = PROJECT_ROOT / "CHANGELOG.md"
# 设置文件同样可由环境变量覆盖：自检用例起临时实例时指向临时设置，
# 从而**根本不会读到/写到你的真实配置**（此前的测试事故就是临时实例误改了真设置）。
SETTINGS_FILE = Path(os.environ.get("SETTINGS_FILE") or (PROJECT_ROOT / "ui_settings.json"))

DEFAULT_REPO = os.environ.get("REPO_PATH") or str(PROJECT_ROOT.parent / "test-mock-repo")


def _env_flag(name: str, default: bool) -> bool:
    """环境变量开关：AGENT_ENABLED=0 / false / no 都算关，没设就用默认值。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")

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
    "agent": {
        # agentic 修复循环：读代码 → 出补丁 → 沙箱真跑 → 读报错 → 自我修复
        "enabled": _env_flag("AGENT_ENABLED", True),
        "max_rounds": 8,             # 一次「提交补丁 + 拿真实验证」算一轮
        "deadline_seconds": 360,     # 墙钟预算，超时即收口，绝不挂住整条流水线
        "max_stall": 3,              # 同一验证结果连续 N 次 → 判定不收敛，认输转人工
        "sandbox": "auto",           # auto | worktree | copy
        "sandbox_dir": os.environ.get("SANDBOX_DIR", ""),
        "link_node_modules": True,   # 把源仓 node_modules 链接进沙箱，否则依赖型命令全是假失败
        "per_command_timeout": 240,
        "page_read": True,           # 让模型看修复前截图与 console 报错（多模态视觉判读）
        "command_allow_text": "",    # 追加放行的命令正则，一行一条
    },
    "playwright": {
        "base_url": os.environ.get("APP_BASE_URL", "http://localhost:3000"),
        "headless": True,
    },
}


# --------------------------------------------------------------- settings io
def _mask(s: str) -> str:
    return mask_secret(s)


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
    # 界面上的白名单文本框读这个键（保存时反解回 command_allow 列表）
    allow = (s.get("agent") or {}).get("command_allow") or []
    if isinstance(allow, str):
        allow = [x.strip() for x in allow.splitlines() if x.strip()]
    out.setdefault("agent", {})["command_allow_text"] = "\n".join(str(x) for x in allow)
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


def _agent_config(s: dict) -> dict:
    """Settings 里的 `agent` 段 → 流水线能吃的 agent 配置（含命令白名单文本解析）。"""
    raw = dict(s.get("agent") or {})
    allow = [ln.strip() for ln in (raw.get("command_allow_text") or "").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    raw.pop("command_allow_text", None)
    if allow:
        raw["command_allow"] = allow
    return raw


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
        "agent": _agent_config(s),
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


def _astore_att() -> ChatAttachmentStore:
    return ChatAttachmentStore(str(ATTACH_DIR))


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


def _pipeline_preflight() -> dict:
    """新起一个流水线并取仓库前置检查。

    单独成函数是为了能被 `run_in_threadpool` 直接调：里面至少一次 git 子进程，
    在 async 端点上直接跑会冻住整个界面（健康轮询每几秒就来一次）。
    """
    return _fresh_pipeline().preflight


_RUN_STATE = {"running": False}
# 问答占用标记：与缺陷流水线**各管各的**（可以一边跑流水线一边问问题），
# 但同一时刻只允许一个问答在跑，避免重复提交把会话写乱。
_CHAT_STATE = {"running": False}
