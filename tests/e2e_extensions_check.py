"""One-off E2E check for the extensions endpoints (run with venv python)."""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(url, data):
    req = urllib.request.Request(
        BASE + url, json.dumps(data).encode("utf-8"),
        {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def get(url):
    return json.loads(urllib.request.urlopen(BASE + url, timeout=30).read())


for _ in range(15):
    try:
        get("/api/health")
        break
    except Exception:
        time.sleep(1)

# 1. skill
r = post("/api/extensions/skills", {"skills": [{
    "name": "团队前端规范", "description": "组件与请求封装规范",
    "content": "1. 组件一律函数式+hooks；2. 请求必须走 src/utils/request 封装；3. 禁止内联颜色。",
    "scope": ["all"], "enabled": True}]})
print("skills saved:", r["count"])

# 2. mcp server
r = post("/api/extensions/mcp", {"servers": [{
    "name": "echo", "transport": "stdio",
    "command": ".venv/Scripts/python.exe", "args": "tests/fake_mcp_server.py",
    "enabled": True, "timeout": 15}]})
print("mcp saved:", r["count"])
server = r["servers"][0]

# 3. test connection (unsaved payload form)
r = post("/api/extensions/mcp/test", {"server": server})
print("mcp test ok:", r["ok"], "tools:", [t["name"] for t in r.get("tools", [])])

# 4. extension list round-trip
r = get("/api/extensions")
print("extensions:", len(r["skills"]), "skills,", len(r["mcp"]), "mcp")

# 5. real task with skills + tools mounted (AI may or may not call the tool)
r = post("/api/tasks/run", {
    "task": "codetest", "file_path": "packages/admin/extends/api.ts",
    "framework": "vitest",
    "requirements": "验证默认导出结构；可用 echo 工具验证工具链（可选）",
    "title": "扩展链路验证"})
a = r.get("artifact") or {}
print("task ok:", r.get("ok"), "id:", a.get("id"), "files:", a.get("file_count"),
      "error:", a.get("error") or "无")

# 6. detail shows context
d = get(f"/api/artifacts/{a['id']}")
ctx = d.get("context") or {}
print("skills_used:", ctx.get("skills_used"), "tools_used:", ctx.get("tools_used"))

# cleanup: remove test data to leave user a clean slate
post("/api/extensions/skills", {"skills": []})
post("/api/extensions/mcp", {"servers": []})
print("cleaned up test skill/mcp")
