"""Shared helper: browser tests must not depend on whatever AI config is ambient.

A user who pointed the tool at a real relay would otherwise make these tests fire
real network calls. Force Mock for the duration, restore afterwards.
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8765"


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read().decode("utf-8"))


def _get(path: str) -> dict:
    return json.loads(urllib.request.urlopen(BASE + path).read().decode("utf-8"))


def force_mock() -> dict:
    """Switch to Mock, returning the previous ai block so it can be restored."""
    prev = _get("/api/settings").get("ai") or {}
    _post("/api/settings", {"ai": {"mock": True}})
    now = _get("/api/health")
    if now.get("ai_mode") != "mock":
        raise RuntimeError(f"无法切到 Mock 模式，当前 {now.get('ai_mode')}")
    return prev


def restore(prev: dict) -> None:
    if not prev:
        return
    payload = {k: v for k, v in prev.items() if k not in ("api_key_masked", "resolved")}
    # 明文 key 不会从 GET 里拿到，只能靠掩码字段判断是否已存在，故不动 api_key
    payload.pop("api_key", None)
    try:
        _post("/api/settings", {"ai": payload})
    except Exception as e:  # pragma: no cover - best effort cleanup
        print("  [warn] 恢复 AI 配置失败:", e)
