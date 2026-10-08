# -*- coding: utf-8 -*-
"""同源闸门回归：该拒的拒、该放的放，本机脚本与自检用例不受影响。

守护的是 2026-10-08 修掉的漏洞——原先挂着 `allow_origins=["*"]` 且全无鉴权，
浏览器里任意网页都能 POST 到 127.0.0.1:8765 改 gate.commands_text，而采纳链路
会用 shell=True 执行它（scripts/gate.py）。这类边界一旦退化，本地不会有任何
其他信号，所以必须有专门用例盯着。

跑法（不依赖 cwd）： PYTHONUTF8=1 .venv/Scripts/python.exe tests/check_origin_guard.py
"""
import http.client
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))                    # web.server / _origin_tuple
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/：temp_server
import temp_server  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def hit(port, path, *, method="GET", headers=None, body=None):
    """发一个原始请求（用 http.client 才能精确控制 Host 等头）。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        payload = r.read().decode("utf-8", "replace")
        return r.status, {k.lower(): v for k, v in r.getheaders()}, payload
    finally:
        conn.close()


with temp_server.serve() as srv:
    port = srv.port
    mine = f"http://127.0.0.1:{port}"

    # ---- 1. 本机脚本路径不受影响（urllib / curl 都不发 Origin） ----
    code, hdrs, body = hit(port, "/api/health")
    check("不发任何来源头 → 放行", code == 200, f"code={code}")
    check("响应里不再有 CORS 通配", hdrs.get("access-control-allow-origin") != "*",
          str(hdrs.get("access-control-allow-origin")))
    check("健康检查仍然可用", json.loads(body).get("ok") is not False, body[:80])

    code, _, body = hit(port, "/api/settings", method="POST",
                        headers={"Content-Type": "application/json"}, body="{}")
    check("本机 POST 写接口不被闸门误伤", code != 403, f"code={code} {body[:80]}")

    # ---- 2. 同源的真实界面请求照常 ----
    code, _, _ = hit(port, "/api/health", headers={"Origin": mine})
    check("同源 Origin → 放行", code == 200, f"code={code}")
    code, _, _ = hit(port, "/api/health", headers={"Sec-Fetch-Site": "same-origin"})
    check("Sec-Fetch-Site: same-origin → 放行", code == 200, f"code={code}")
    code, _, _ = hit(port, "/", headers={"Sec-Fetch-Site": "none"})
    check("地址栏直接访问（none）→ 放行", code in (200, 503), f"code={code}")

    # ---- 3. 恶意网页的四条来路全部拒 ----
    evil = "http://evil.example"
    for label, headers in (
        ("跨源 GET（带 Origin）", {"Origin": evil}),
        ("跨源 POST（带 Origin，正是驱动 gate shell 的那条链）",
         {"Origin": evil, "Content-Type": "application/json"}),
        ("不发 Origin 的跨源表单提交（靠 Sec-Fetch-Site 兜）",
         {"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"}),
        ("靠 Referer 泄出来源的跳转请求", {"Referer": evil + "/landing"}),
    ):
        code, _, body = hit(port, "/api/settings", method="POST",
                            headers=headers, body="{}")
        check(f"拒：{label}", code == 403, f"code={code} {body[:70]}")

    # 只读的跨源探测同样拒（别以为 GET 就能顺走配置）
    code, _, _ = hit(port, "/api/health", headers={"Origin": evil})
    check("拒：跨源 GET 读配置", code == 403, f"code={code}")

    # ---- 4. DNS rebinding：Host 头不是回环地址 ----
    code, _, body = hit(port, "/api/health", headers={"Host": f"evil.example:{port}"})
    check("拒：Host 指向外域（rebinding）", code == 403, f"code={code} {body[:70]}")

    # ---- 5. localhost 与 127.0.0.1 是两个不同 origin，不互相放行 ----
    code, _, _ = hit(port, "/api/health", headers={"Origin": f"http://localhost:{port}"})
    check("localhost 冒充 127.0.0.1 → 拒", code == 403, f"code={code}")

# ---- 6. 端口归一化的单元测试（默认端口省略不影响比对） ----
from web.server import _origin_tuple  # noqa: E402

check("默认端口归一：http://h:80 → http://h", _origin_tuple("http://127.0.0.1:80")
      == "http://127.0.0.1", _origin_tuple("http://127.0.0.1:80"))
check("非默认端口保留", _origin_tuple("http://127.0.0.1:8765")
      == "http://127.0.0.1:8765", _origin_tuple("http://127.0.0.1:8765"))
check("IPv6 回环带方括号", _origin_tuple("http://[::1]:8765/x")
      == "http://[::1]:8765", _origin_tuple("http://[::1]:8765/x"))
check("垃圾值不炸：port=abc", _origin_tuple("http://127.0.0.1:abc") == "",
      repr(_origin_tuple("http://127.0.0.1:abc")))
check("空值返回空串", _origin_tuple("") == "", repr(_origin_tuple("")))

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
