"""共享内核：数据目录、settings 读写与掩码、store 工厂、流水线配置、运行态标记。

被 server.py 与所有 routers 依赖，因此这里只依赖 scripts/*，不引用 app、也不定义任何路由。
"""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

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
        "repro": "auto",             # auto|on|off —— 先写复现用例跑红，再动手修
        "repro_command": "",         # 探测不准时自定义复现命令（{file} 是用例路径）
        "repro_max_rewrite": 2,      # 复现失败允许重写几次，超过就放弃该环节
        "sandbox_server": "auto",    # auto|on|off：在沙箱里起 dev server 复验页面
        "sandbox_server_command": "",
        "sandbox_server_cwd": "",
        "sandbox_server_ready_timeout": 90,
        "sandbox_server_max_checks": 2,
        "api_probe": "auto",       # 只读接口探测（判后端归因要有真相）；off 关闭
        "api_probe_hosts": "",
        "api_probe_timeout": 15,
        "precedents": 3,             # 同类历史缺陷先例注入条数（0=关）
        "precedents_chars": 6000,
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
        # 复现用例由修复循环挂成产出物等人采纳（见 pipeline._repro_artifact）
        "artifacts": {"output_dir": str(ARTIFACT_DIR)},
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


# ------------------------------------------------------- 流水线占用（租约）
# 这里曾经是裸布尔 `_RUN_STATE = {"running": False}`。它只回答「有没有人在跑」，
# 于是出过一次很难受的现场：一个外部脚本 POST /api/run 跑批量修复，界面上一整段
# 时间只显示「已有流水线在运行，请等待完成」——不知道是哪条工单、不知道跑了多久、
# 不知道是谁发起的，也没有任何停止入口，唯一的出路是重启进程。
# 现在槽位是一份**带身份的租约**：谁申请的（run/probe/tasks/team）、来源、处理到
# 第几条工单、当前阶段、已耗时、有没有被请求取消，全都可读；并且可停。
#
# 三条不变式，都是这次一并修掉的坑：
# ① 检查与占用在 `_RUN_LOCK` 里一次做完（原先 `if running: 409` 与
#    `running = True` 之间隔着 MCP 握手，两个并发请求能双双通过）；
# ② 取消事件**跟着租约走**（每个租约一个 Event 对象）：强制解除占用后，僵尸线程
#    手里的 Event 仍然是「已取消」，而新起的运行拿到的是全新的 Event，互不污染；
# ③ 释放必须比对租约（`_release_run(ev)`）：僵尸线程收尾时不会把别人的占用清掉。
RUN_KIND_LABELS = {
    "run": "缺陷流水线",
    "probe": "试运行（拉取 + AI 定位）",
    "tasks": "任务（需求开发 / 联调 / 测试）",
    "team": "长任务作业",
    "chat": "问答",
}

_RUN_LOCK = threading.RLock()
# 当前租约的字段。空 = 没人跑；`running` 键恒在，便于 `if _RUN_STATE["running"]` 这种老写法
_RUN_STATE: dict = {"running": False}


def _acquire_run(kind: str, *, label: str = "", source: str = "",
                 total: int = 0, defect_id: str = "") -> Optional[threading.Event]:
    """占用流水线槽位。

    成功返回本次租约的**取消事件**（传给干活的线程做协作式中断），已被占用返回
    `None`——调用方据此回 409，并且要用 `_busy_detail()` 说清楚被谁占着。
    """
    with _RUN_LOCK:
        if _RUN_STATE.get("running"):
            return None
        cancel = threading.Event()
        _RUN_STATE.clear()
        _RUN_STATE.update({
            "running": True,
            "kind": kind,
            "kind_label": RUN_KIND_LABELS.get(kind, kind),
            "label": str(label or "")[:160],
            "source": str(source or "")[:80],
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "_t0": time.monotonic(),
            "total": max(0, int(total or 0)),
            "index": 0,
            "defect": str(defect_id or "")[:60],
            "defect_title": "",
            "stage": "",
            "stage_detail": "",
            "cancel_event": cancel,
            "cancel_requested": False,
        })
        return cancel


def _release_run(cancel: Optional[threading.Event] = None) -> None:
    """解除占用。传了 `cancel` 就只解除属于它的那份租约（僵尸线程不清别人的槽位）。"""
    with _RUN_LOCK:
        if cancel is not None and _RUN_STATE.get("cancel_event") is not cancel:
            return
        _RUN_STATE.clear()
        _RUN_STATE["running"] = False


def _run_progress(cancel: Optional[threading.Event], **fields) -> None:
    """由流水线的 emit 镜像调用，更新进度字段（第几单 / 当前阶段）。

    租约已被强制解除或被新运行接管时**静默忽略**：否则上一条被取消的流水线会在
    收尾阶段把新运行的进度条改回自己的工单号。
    """
    with _RUN_LOCK:
        if not _RUN_STATE.get("running"):
            return
        if cancel is not None and _RUN_STATE.get("cancel_event") is not cancel:
            return
        for k, v in fields.items():
            _RUN_STATE[k] = v


def _run_snapshot() -> dict:
    """给界面/健康检查看的运行态（不含 Event 等不可序列化对象）。"""
    with _RUN_LOCK:
        if not _RUN_STATE.get("running"):
            return {"running": False}
        out = {k: v for k, v in _RUN_STATE.items()
               if k not in ("cancel_event", "_t0")}
        out["elapsed_seconds"] = round(time.monotonic() - _RUN_STATE.get("_t0", 0.0), 1)
        return out


def _progress_emitter(cancel: Optional[threading.Event], forward=None):
    """把流水线的 emit 事件镜像进占用租约，必要时再转发给 SSE。

    这一步是「外部脚本发起的批量运行也能在界面上看见」的关键：非流式的
    `/api/run` 以前根本不传 emit，占用又只是个布尔，界面自然什么都不知道。
    镜像失败绝不能反过来搞挂流水线，所以整段吞异常。
    """
    def emit(evt) -> None:
        try:
            kind = (evt or {}).get("type")
            if kind == "defect":
                _run_progress(cancel,
                              index=int(evt.get("index") or 0),
                              total=int(evt.get("total") or 0),
                              defect=str(evt.get("id") or "")[:60],
                              defect_title=str(evt.get("title") or "")[:80],
                              stage="", stage_detail="")
            elif kind == "stage":
                _run_progress(cancel,
                              stage=str(evt.get("stage") or "")[:30],
                              stage_detail=str(evt.get("detail") or "")[:160])
        except Exception:
            pass
        if forward is not None:
            forward(evt)

    return emit


def _busy_detail(action: str = "发起新的运行") -> str:
    """409 的正文：光说「已有流水线在运行」等于没说，得讲清被谁占着、能不能停。"""
    snap = _run_snapshot()
    if not snap.get("running"):
        return f"槽位刚好空出来了，请重试{action}"
    parts = [snap.get("kind_label") or snap.get("kind") or "任务"]
    if snap.get("defect"):
        idx, total = snap.get("index") or 0, snap.get("total") or 0
        seq = f"（第 {idx}/{total} 条）" if idx and total else ""
        parts.append(f"工单 {snap['defect']}{seq}")
    if snap.get("defect_title"):
        parts.append(str(snap["defect_title"])[:40])
    if snap.get("stage"):
        parts.append(f"当前阶段 {snap['stage']}")
    if snap.get("source"):
        parts.append(f"来源 {snap['source']}")
    parts.append(f"已运行 {_elapsed_text(snap.get('elapsed_seconds', 0))}")
    head = "、".join(p for p in parts if p)
    if snap.get("cancel_requested"):
        return f"正在停止：{head}。会在当前阶段边界收口，请稍候。"
    return f"已有任务在运行——{head}。可在运行面板点「停止」，或「强制解除占用」后重试{action}。"


def _busy_response(action: str):
    """槽位被占时的 409 响应：正文说清被谁占着，`run` 字段把租约原样带出去
    （界面据此渲染进度与「停止」按钮）。所有共用这个槽位的路由都用它。"""
    return JSONResponse({"error": _busy_detail(action), "run": _run_snapshot()},
                        status_code=409)


def _busy_gate(action: str):
    """只读地挡一道：槽位被占时返回 409 响应，空闲返回 None。

    给「不该占用槽位、但也不能在流水线跑一半时改仓库」的端点用（采纳/撤销/返工）。
    写法固定为 `r = _busy_gate("采纳"); if r is not None: return r`。
    """
    return _busy_response(action) if _run_snapshot().get("running") else None


def _elapsed_text(seconds) -> str:
    s = max(0, int(seconds or 0))
    return f"{s // 60} 分 {s % 60} 秒" if s >= 60 else f"{s} 秒"


def _request_run_cancel(force: bool = False) -> dict:
    """请求停止当前运行。

    默认是**协作式**的：置起这份租约的取消事件，流水线会在「工单之间」和
    「agentic 修复的每一轮之间」两个边界收口——模型调用与沙箱命令都在子进程里，
    中途硬砍会留下半截沙箱与半截提案。
    `force=True` 额外立刻解除占用：僵尸线程继续收尾（它仍会落自己的提案），
    但界面马上就能发起下一次运行。
    """
    with _RUN_LOCK:
        snap = _run_snapshot()
        if not snap.get("running"):
            return {"ok": False, "running": False,
                    "detail": "当前没有运行中的任务，无需停止"}
        ev = _RUN_STATE.get("cancel_event")
        if ev is not None:
            ev.set()
        _RUN_STATE["cancel_requested"] = True
        if force:
            _RUN_STATE["force_released"] = True
            kind = _RUN_STATE.get("kind_label", "")
            _RUN_STATE.clear()
            _RUN_STATE["running"] = False
            snap = _run_snapshot()
            return {"ok": True, "running": False, "force": True,
                    "detail": f"已强制解除占用（{kind} 的线程可能仍在后台收尾并落提案，"
                              f"但不会再阻塞新的运行）"}
        return {"ok": True, "running": True, "force": False,
                "detail": "已请求停止：会在当前阶段边界生效"
                          "（最迟一轮模型调用 + 一次沙箱验收），无需重启服务"}


# 问答占用标记：与缺陷流水线**各管各的**（可以一边跑流水线一边问问题），
# 但同一时刻只允许一个问答在跑，避免重复提交把会话写乱。
_CHAT_STATE = {"running": False}
