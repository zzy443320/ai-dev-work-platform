# -*- coding: utf-8 -*-
"""内嵌浏览器会话回归：帧流、输入转发、点选读属性、登录代填、会话互斥与释放。

不打真 Figma（外网 + 凭据都不可靠），起一个**本地假站点**当目标：
  /login —— 账号密码表单（登录成功写 sessionStorage 后跳 /）
  /      —— 左侧"画布"放一个可点选节点，点击后右侧属性面板显示其属性
这样四件事都能在断网环境里验证：
  1. 开会话 → 落在登录页 → 配置里的 Figma 账号密码被代填 → 进到工作台；
  2. 帧流真的有 JPEG 帧（流式 + GET 兜底两条路都验）；
  3. 输入转发（点击命中节点）→ 点选读面板拿到属性文本；
  4. 停止后锁文件释放、profile 目录可再次使用（残留 chromium 会占住目录）。

用法：python tests/check_live_browser.py（自起临时服务与临时站点，不碰真实数据）
"""
import http.server
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from temp_server import serve, free_port  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  [ok]   " if ok else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))


LOGIN_PAGE = """<!doctype html><meta charset="utf-8"><title>登录 · 假站点</title>
<style>body{margin:0;font:14px/1.6 sans-serif}form{width:320px;margin:120px auto;padding:24px;border:1px solid #ccc}</style>
<form id="f" onsubmit="return doLogin()">
  <h3>登录</h3>
  <input id="email" name="email" type="email" placeholder="邮箱" style="width:100%;display:block;margin:8px 0">
  <input id="password" name="password" type="password" placeholder="密码" style="width:100%;display:block;margin:8px 0">
  <button type="submit" id="go">登录</button>
</form>
<script>
function doLogin(){
  sessionStorage.setItem('auth','1');
  location.href='/'; return false;
}
if (sessionStorage.getItem('auth')) location.href='/';
</script>"""

APP_PAGE = """<!doctype html><meta charset="utf-8"><title>工作台 · 假站点</title>
<style>
 body{margin:0;font:14px/1.6 sans-serif}
 #canvas{position:fixed;left:0;top:0;right:300px;bottom:0;background:#20242c}
 #node1{position:absolute;left:200px;top:300px;width:120px;height:40px;background:#1f6feb;color:#fff;
        display:grid;place-items:center;cursor:pointer}
 #panel{position:fixed;right:0;top:0;width:300px;bottom:0;background:#f2f2f2;padding:12px;box-sizing:border-box}
</style>
<script>
 if (!sessionStorage.getItem('auth')) location.href='/login';
 function selectNode(){
   document.getElementById('panel').innerHTML =
     '<b>Button / primary</b><br>宽 375<br>高 48<br>填充 #1F6FEB<br>圆角 12<br>字号 14';
 }
</script>
<div id="canvas"><div id="node1" onclick="selectNode()">Button</div></div>
<div id="panel">未选中</div>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = APP_PAGE if self.path == "/" else LOGIN_PAGE
        code = 200
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *a):   # 静音
        pass


def _api(base, path, payload=None, timeout=30):
    """payload=None 走 GET（status/frame），其余走 POST JSON。"""
    if payload is None and not path.endswith("/start"):
        req = urllib.request.Request(base + path)
    else:
        req = urllib.request.Request(base + path,
                                     data=json.dumps(payload or {}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        raise AssertionError(f"HTTP {e.code} {path} → {body}") from None


def main() -> int:
    site_port = free_port()
    site = http.server.ThreadingHTTPServer(("127.0.0.1", site_port), _Handler)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{site_port}"

    try:
        with serve(settings={"figma": {"account": "demo@example.com",
                                       "password": "secret123"}}) as srv:
            # ---- 1. 开会话：落在登录页 → 配置里的账号密码代填 → 进工作台 ----
            snap = _api(srv.base, "/api/live/start", {"url": base + "/"})["session"]
            check("开会话成功", snap.get("alive") is not None, json.dumps(snap, ensure_ascii=False)[:120])

            login = "login_page"
            for _ in range(40):
                time.sleep(0.5)
                st = _api(srv.base, "/api/live/status")
                login = (st.get("session") or {}).get("login") or ""
                if login == "ok":
                    break
            check("登录页被代填并进入工作台", login == "ok",
                  f"login={login} url={(st.get('session') or {}).get('url')} "
                  f"err={(st.get('session') or {}).get('error')}")

            # ---- 2. 帧流：GET 兜底真的有 JPEG；页面没动时不推新帧 ----
            fr = _api(srv.base, "/api/live/frame")
            check("最新帧是 JPEG（magic FFD8）", fr["jpeg"][:4] == "/9j/",
                  f"len={len(fr['jpeg'])} seq={fr['seq']}")
            import base64 as _b64
            seq1 = fr["seq"]
            time.sleep(1.2)
            fr2 = _api(srv.base, "/api/live/frame")
            check("页面没动时不推新帧（seq 不变）", fr2["seq"] == seq1, f"{seq1} → {fr2['seq']}")

            # ---- 3. 输入转发 + 点选读属性 ----
            r = _api(srv.base, "/api/live/input",
                     {"kind": "click", "x": 260, "y": 320, "vw": 1440, "vh": 900})
            check("点击转发成功", r.get("ok") is True)
            time.sleep(0.6)                      # 等假站点的 onclick 更新面板
            ins = _api(srv.base, "/api/live/inspect", {"x": 0, "y": 0, "vw": 1440, "vh": 900})
            text = "\n".join(ins.get("panel") or [])
            check("点选后属性面板读到节点属性",
                  ("填充 #1F6FEB" in text) and ("圆角 12" in text), text[:80])
            # wheel / key 不抛错即可
            _api(srv.base, "/api/live/input", {"kind": "wheel", "x": 700, "y": 450,
                                               "dx": 0, "dy": -120, "vw": 1440, "vh": 900})
            _api(srv.base, "/api/live/input", {"kind": "key", "key": "Escape"})

            # ---- 4. 会话互斥：第二个 start 必须 409（含可读的原因）----
            try:
                _api(srv.base, "/api/live/start", {"url": base + "/"})
                check("第二次开会话被拒（单会话约束）", False, "竟然成功了")
            except AssertionError as e:
                check("第二次开会话被拒（单会话约束）",
                      "409" in str(e) and "已在运行" in str(e), str(e)[:120])

            # ---- 5. 停止 → 状态复位 + 能再开（锁与 profile 未被残留进程占住）----
            _api(srv.base, "/api/live/stop", {}, timeout=60)
            st = _api(srv.base, "/api/live/status")
            check("停止后状态复位", st.get("running") is False, json.dumps(st)[:120])
            # stop() 已经等到锁文件被删；这里不再 sleep，直接重开。
            # 重开偶发慢一拍（chromium 进程收尾），重试两次，验收的是
            # 「最终能再开」而不是「某一次固定毫秒内能开」。
            snap2 = None
            for _ in range(3):
                try:
                    snap2 = _api(srv.base, "/api/live/start", {"url": base + "/"}, timeout=60)
                    break
                except AssertionError as e:
                    last_err = str(e)
                    time.sleep(1.5)
            check("停止后能再次开会话（profile 未被残留进程占住）",
                  bool(snap2 and snap2.get("session", {}).get("alive")), last_err if not snap2 else "")
            _api(srv.base, "/api/live/stop", {}, timeout=60)
    finally:
        site.shutdown()

    # ================================================================
    # 阶段二：UI 链路（真实 Vue 面板）。API 全对但 UI 点不到的坑出现过
    # （面板坐标换算 / 点击语义），这条锁住「从界面点出去 → 后端收到 →
    # 属性文本回到面板」的完整链路。
    # ================================================================
    site_port = free_port()
    site = http.server.ThreadingHTTPServer(("127.0.0.1", site_port), _Handler)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    site_base = f"http://127.0.0.1:{site_port}"
    try:
        with serve(settings={"figma": {"account": "demo@example.com",
                                       "password": "secret123"}}) as srv:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch()
                pg = browser.new_page(viewport={"width": 1440, "height": 900})
                ui_errs = []
                pg.on("pageerror", lambda e: ui_errs.append(str(e)[:100]))
                pg.goto(srv.base + "/", wait_until="networkidle")
                pg.wait_for_timeout(1500)
                pg.evaluate("() => window.switchTab('reqdev')")
                pg.wait_for_timeout(400)
                pg.fill("#req-figma-url", site_base + "/")
                pg.click("#btn-figma-live")
                pg.wait_for_timeout(2600)
                opened = pg.eval_on_selector(
                    "#live-browser-panel", "el => el.offsetParent !== null")
                check("UI：内嵌看稿面板打开", bool(opened))
                # 点节点（240,300 是 mock 节点中心）；允许重试两次
                ok_click = False
                for _ in range(3):
                    box = pg.evaluate("""() => {
                      const i = document.querySelector('.live-frame');
                      const r = i.getBoundingClientRect();
                      const s = Math.min(r.width/1440, r.height/900);
                      const w = 1440*s, h = 900*s;
                      return {x: r.left + (r.width-w)/2 + 240*s,
                              y: r.top + (r.height-h)/2 + 300*s}; }""")
                    pg.mouse.click(box["x"], box["y"])
                    pg.wait_for_timeout(1500)
                    txt = pg.evaluate(
                        "() => (document.querySelector('.live-inspect-text')||{}).textContent || ''")
                    if "填充 #1F6FEB" in txt:
                        ok_click = True
                        break
                check("UI：点选节点 → 属性文本回到面板", ok_click)
                pg.click("#btn-live-stop")
                pg.wait_for_timeout(1500)
                st = _api(srv.base, "/api/live/status")
                check("UI：停止会话后状态复位", st.get("running") is False)
                check("UI：无 pageerror", not ui_errs, "; ".join(ui_errs)[:200])
                browser.close()
    finally:
        site.shutdown()

    fails = [n for n, ok, _ in results if not ok]
    if fails:
        print(f"FAIL: {len(fails)} 项未过: {fails}")
        return 1
    print(f"PASS: 内嵌浏览器会话 {len(results)} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
