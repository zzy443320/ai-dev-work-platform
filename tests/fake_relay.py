"""A fake relay/gateway used to prove the config-driven transport works.

It deliberately deviates from the official shapes:
  * the answer is at `data.reply` instead of choices[0].message.content
  * auth is a custom header `X-Gw-Token`
  * the chat path is /gw/chat
  * it can be told to return errors, HTML, or malformed JSON
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = {"mode": "ok", "requests": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        STATE["requests"].append({"method": "GET", "path": self.path})
        if self.path.endswith("/gw/models"):
            self._send(200, {"data": [{"id": "gw-claude-pro"}, {"id": "gw-qwen-max"},
                                       {"id": "gpt-4o"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw)
        except Exception:
            body = {"_unparsed": raw[:200].decode("utf-8", "replace")}
        STATE["requests"].append({
            "method": "POST", "path": self.path,
            "auth": self.headers.get("authorization"),
            "gw": self.headers.get("x-gw-token"),
            "anthropic_version": self.headers.get("anthropic-version"),
            "tenant": self.headers.get("x-tenant"),
            "body": body,
        })

        if STATE["mode"] == "html":
            return self._send(200, b"<html>login required</html>", "text/html")
        if STATE["mode"] == "err401":
            return self._send(401, {"error": {"message": "invalid api key"}})
        if STATE["mode"] == "err500":
            return self._send(500, {"error": {"message": "upstream overloaded"}})
        if STATE["mode"] == "notjson":
            return self._send(200, b"\x00\x01garbage")
        if STATE["mode"] == "wrongshape":
            return self._send(200, {"ok": True})

        prompt = ""
        for m in body.get("messages", []):
            if m.get("role") == "user":
                prompt = m.get("content", "")
        if "只回复两个字" in prompt:
            answer = "收到"
        else:
            answer = json.dumps({
                "root_cause": "props.template 可能为 null，直接取 .name 触发 TypeError。",
                "category": "数据",
                "explanation": "补可选链即可消除。",
                "prevention": "接口层加 schema 校验。",
                "patch_blocks": _patch_from_prompt(prompt),
            }, ensure_ascii=False)
        if STATE["mode"] == "fenced":
            answer = "分析如下：\n```json\n" + answer + "\n```\n以上。"
        self._send(200, {"data": {"reply": answer}, "usage": {"prompt_tokens": 7,
                                                              "completion_tokens": 21}})


def _patch_from_prompt(prompt: str) -> str:
    """Echo back a SEARCH/REPLACE built from the file content we were shown."""
    import re

    m = re.search(r"^### (.+?)\n```\n(.*?)\n```", prompt, re.DOTALL | re.MULTILINE)
    if not m:
        return ""
    path, content = m.group(1).strip(), m.group(2)
    for line in content.split("\n"):
        if "?." in line or "//" in line or ".name" not in line:
            continue
        return (f"<<<<<<< SEARCH {path}\n{line}\n=======\n"
                f"{line.replace('.name', '?.name')}\n>>>>>>> REPLACE")
    return ""


def start(port=0):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
