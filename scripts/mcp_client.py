"""Minimal MCP (Model Context Protocol) client — stdio + streamable HTTP.

Enough of the spec to do the only two things this app needs:
  - tools/list   (also used by the "测试连接" button)
  - tools/call   (executed on demand while an AI task is running)

stdio: launches the server as a subprocess, speaks newline-delimited JSON-RPC
2.0 over stdin/stdout with an initialize handshake. A reader thread feeds a
queue so every request can time out instead of hanging the task.

HTTP: streamable-HTTP transport (POST JSON-RPC, tolerates both plain JSON and
SSE responses, tracks Mcp-Session-Id).

The client never trusts server output: tool text is truncated and callers must
treat it as untrusted data to be shown to the model, not executed locally.
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import subprocess
import threading
import time
from typing import Dict, List, Optional

import requests

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "ones-frontend-copilot", "version": "1.0"}
REQUEST_TIMEOUT = 30
MAX_TOOL_TEXT = 8000


class MCPError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def _rpc(method: str, params: Optional[Dict], rid: int) -> Dict:
    return {"jsonrpc": "2.0", "id": rid, "method": method,
            "params": params if params is not None else {}}


def _extract_result(payload: Dict) -> Dict:
    if "error" in payload and payload["error"]:
        err = payload["error"]
        raise MCPError(f"MCP 错误 {err.get('code')}: {err.get('message', '')[:200]}")
    if "result" not in payload:
        raise MCPError("MCP 响应缺少 result")
    return payload["result"] or {}


def _result_text(result: Dict) -> str:
    """Flatten a tools/call result into plain text for the model."""
    if result.get("isError"):
        pass  # still return text; the model should see the failure
    parts: List[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text", "")))
        else:
            parts.append(json.dumps(item, ensure_ascii=False)[:500])
    text = "\n".join(p for p in parts if p)
    if result.get("isError"):
        text = f"[工具执行出错] {text}"
    if len(text) > MAX_TOOL_TEXT:
        text = text[:MAX_TOOL_TEXT] + f"\n…（超过 {MAX_TOOL_TEXT} 字符已截断）"
    return text


def _server_config(cfg: Dict) -> Dict:
    transport = (cfg.get("transport") or "stdio").strip()
    out = {
        "name": (cfg.get("name") or "mcp").strip(),
        "transport": transport,
        "enabled": bool(cfg.get("enabled", True)),
        "timeout": int(cfg.get("timeout") or REQUEST_TIMEOUT),
    }
    if transport == "stdio":
        out["command"] = (cfg.get("command") or "").strip()
        args = cfg.get("args") or ""
        if isinstance(args, str):
            # shell-ish split, quotes respected
            import shlex
            try:
                out["args"] = shlex.split(args)
            except ValueError:
                out["args"] = args.split()
        else:
            out["args"] = [str(a) for a in args]
        env = cfg.get("env") or {}
        if isinstance(env, str):
            env = _kv_lines(env)
        out["env"] = {str(k): str(v) for k, v in env.items()}
        out["cwd"] = (cfg.get("cwd") or "").strip()
        if not out["command"]:
            raise MCPError(f"MCP 服务「{out['name']}」缺少启动命令")
    else:
        out["url"] = (cfg.get("url") or "").strip()
        headers = cfg.get("headers") or {}
        if isinstance(headers, str):
            headers = _kv_lines(headers)
        out["headers"] = {str(k): str(v) for k, v in headers.items()}
        if not out["url"]:
            raise MCPError(f"MCP 服务「{out['name']}」缺少 URL")
    return out


def _kv_lines(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (":", "="):
            if sep in line:
                k, _, v = line.partition(sep)
                out[k.strip()] = v.strip()
                break
    return out


# ------------------------------------------------------------------ stdio
class StdioSession:
    def __init__(self, cfg: Dict):
        import sys
        self.cfg = cfg
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.update(cfg.get("env") or {})
        try:
            self.proc = subprocess.Popen(
                [cfg["command"], *cfg.get("args", [])],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                env=env, cwd=cfg.get("cwd") or None,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as e:
            raise MCPError(f"无法启动 MCP 服务: {e}")
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._ids = itertools.count(1)
        self.timeout = cfg.get("timeout") or REQUEST_TIMEOUT
        self._initialize()

    def _read_loop(self) -> None:
        for line in self.proc.stdout:  # type: ignore[union-attr]
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, obj: Dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError) as e:
            raise MCPError(f"MCP 服务管道已断开: {e}")

    def _recv(self, rid: int, timeout: float) -> Dict:
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise MCPError(f"MCP 响应超时（{timeout}s）")
            try:
                line = self._lines.get(timeout=remain)
            except queue.Empty:
                raise MCPError(f"MCP 响应超时（{timeout}s）")
            if line is None:
                raise MCPError("MCP 服务进程已退出")
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") != rid:
                continue  # notification / out-of-order response
            return payload

    def request(self, method: str, params: Optional[Dict] = None) -> Dict:
        rid = next(self._ids)
        self._send(_rpc(method, params, rid))
        return _extract_result(self._recv(rid, self.timeout))

    def notify(self, method: str, params: Optional[Dict] = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method,
               "params": params if params is not None else {}}
        self._send(msg)

    def _initialize(self) -> None:
        try:
            self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            })
        except MCPError:
            self.close()
            raise
        self.notify("notifications/initialized")

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass


# ------------------------------------------------------------------ public
def list_tools(cfg: Dict) -> List[Dict]:
    """[{name, description, inputSchema}]"""
    cfg = _server_config(cfg)
    if cfg["transport"] == "stdio":
        with StdioSession(cfg) as s:
            res = s.request("tools/list")
            return res.get("tools") or []
    return _http_rpc(cfg, "tools/list").get("tools") or []


def call_tool(cfg: Dict, tool: str, arguments: Dict) -> str:
    cfg = _server_config(cfg)
    params = {"name": tool, "arguments": arguments or {}}
    if cfg["transport"] == "stdio":
        with StdioSession(cfg) as s:
            return _result_text(s.request("tools/call", params))
    return _result_text(_http_rpc(cfg, "tools/call", params))


def test_server(cfg: Dict) -> Dict:
    """初始化 + tools/list，用于「测试连接」。"""
    t0 = time.time()
    try:
        cfg = _server_config(cfg)
        tools = list_tools(cfg)
        return {"ok": True, "seconds": round(time.time() - t0, 2),
                "name": cfg["name"], "tools": [
                    {"name": t.get("name", ""), "description": (t.get("description") or "")[:120]}
                    for t in tools][:30],
                "tool_count": len(tools)}
    except MCPError as e:
        return {"ok": False, "error": str(e), "name": (cfg or {}).get("name", "")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "name": (cfg or {}).get("name", "")}


# ------------------------------------------------------------------ http
def _http_rpc(cfg: Dict, method: str, params: Optional[Dict] = None,
              session_id: Optional[str] = None, notify: bool = False) -> Dict:
    """One streamable-HTTP JSON-RPC round trip. Returns result dict
    (notifications return {})."""
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
    headers.update(cfg.get("headers") or {})
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body: Dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        body["id"] = 1
    try:
        r = requests.post(cfg["url"], json=body, headers=headers,
                          timeout=cfg.get("timeout") or REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise MCPError(f"MCP HTTP 请求失败: {e}")
    if r.status_code == 404 and session_id:
        raise MCPError("MCP 会话失效（404），请重试")
    if r.status_code >= 400:
        raise MCPError(f"MCP HTTP {r.status_code}: {r.text[:200]}")
    new_session = r.headers.get("Mcp-Session-Id") or session_id
    ctype = r.headers.get("Content-Type", "")
    if notify:
        return {"_session": new_session}
    payload = None
    if "text/event-stream" in ctype:
        for line in r.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if parsed.get("id") is not None:
                    payload = parsed
                    break
    else:
        try:
            payload = r.json()
        except ValueError:
            raise MCPError("MCP HTTP 返回的不是 JSON/SSE")
    if payload is None:
        raise MCPError("MCP HTTP 响应里没有 JSON-RPC 结果")
    out = _extract_result(payload)
    if isinstance(out, dict):
        out["_session"] = new_session
    return out


def _http_rpc_with_session(cfg: Dict, method: str, params: Optional[Dict]) -> Dict:
    """initialize → initialized → request，串完一整套（无状态 HTTP）。"""
    init = _http_rpc(cfg, "initialize", {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": CLIENT_INFO})
    sid = init.get("_session")
    _http_rpc(cfg, "notifications/initialized", None, session_id=sid, notify=True)
    return _http_rpc(cfg, method, params, session_id=sid)


# stdio session supports context manager
def _session_enter(self): return self
def _session_exit(self, *exc): self.close()
StdioSession.__enter__ = _session_enter
StdioSession.__exit__ = _session_exit
