# temporary test fixture: minimal MCP stdio server (echo tool)
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if "id" not in msg:
        continue  # notification
    method = msg.get("method", "")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "echo", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "回显输入",
                             "inputSchema": {"type": "object", "properties": {
                                 "text": {"type": "string"}}}}]}
    elif method == "tools/call":
        text = (msg.get("params", {}).get("arguments") or {}).get("text", "")
        result = {"content": [{"type": "text", "text": f"ECHO:{text}"}], "isError": False}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False), flush=True)
