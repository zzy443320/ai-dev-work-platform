# -*- coding: utf-8 -*-
"""内嵌浏览器会话（2026-10-14）：受控 Playwright + 截图流 + 输入转发 + 属性面板读取。

为什么要有它：Figma 的画布是 WebGL，整个画布就是一个 <canvas>，DOM 里没有节点——
所以「点画布拿布局」在 DOM 层面做不到；但 Figma 的**右侧属性面板是普通 DOM**，
选中节点后宽高 / 填充 / 字体都能读出来。把"点选"和"读面板"拆成两步，就绕开了
canvas 的黑盒：人（或 agent）在直播画面上点节点 → 转发点击 → 从右栏 DOM 读属性。

形态上这是 Trae 那类「远程浏览器预览」：后端起受控浏览器，页面截图流式推给前端，
前端把点击 / 滚轮 / 键盘转发回来。figma.com 回 X-Frame-Options: SAMEORIGIN，
真 iframe 嵌不进去（实测响应头），所以只能走这条路。

线程模型（⚠️ 踩过：Playwright 同步 API 绑定创建它的线程，greenlet 会拒绝跨线程
切换，"请求线程建会话 + 守护线程截帧"直接炸）。所以这里是**单工作线程**模型：
  * 会话起一个 worker 线程，所有 Playwright 调用（goto / 截帧 / 输入 / 读面板 /
    关闭）都在它上面执行；
  * 其他线程（FastAPI 的事件循环 / 截帧需求）通过 _dispatch() 投递闭包并等结果；
  * worker 空闲时按节拍截帧，有命令先处理命令（点击后的爆发帧由 burst 加速）。

设计约束：
  * **单会话**。同一时刻只允许一个受控会话，文件锁兜底（防两个 chromium 抢
    同一个持久 profile，ProcessSingleton 会直接起不来）。
  * **登录态复用**。持久 profile 与浏览器兜底通道同一个目录（_figma_profile），
    之前人工登录过的 Figma 会话直接续用；配了账号密码时，只在「页面已经明确是
    登录表单」时才代填——不猜登录页，这条纪律沿用 verifier 的做法。
  * **帧只在变化时推**。JPEG 按字节比对，页面没动就不产生流量。
"""
from __future__ import annotations

import concurrent.futures
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from web.state import SCREENSHOT_DIR, _load_settings

# 持久 profile：与 web/routers/tasks.py 的浏览器兜底通道共用（登录态互通）。
PROFILE_DIR = SCREENSHOT_DIR / "_figma_profile"
LOCK_FILE = SCREENSHOT_DIR / "_live_browser.lock"

VIEWPORT = {"width": 1440, "height": 900}
FRAME_INTERVAL = 0.22        # ≈4.5fps；输入后的爆发帧由 burst_until 临时提速
BURST_INTERVAL = 0.09
JPEG_QUALITY = 60
GOTO_TIMEOUT = 30_000

_LOGIN_URL_RE = re.compile(r"/(login|signin|sign-in|auth|sso|passport)(\.\w+)?([/?#]|$)", re.I)


class LiveBrowserError(RuntimeError):
    """给路由层转成 4xx/409 的业务错误。"""


class LiveSession:
    """一个受控浏览器会话：单工作线程执行全部 Playwright 调用。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._calls: "queue.Queue[tuple]" = queue.Queue()
        self.frame: Optional[bytes] = None      # 最近一帧 JPEG（缓冲，跨线程只读它）
        self.frame_seq = 0
        self.burst_until = 0.0
        self.stopped = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.info: Dict = {
            "url": "", "title": "", "login": "unknown",
            "driving": "agent", "error": "", "started_at": 0.0,
        }
        self._autologin_url = ""                # 已对哪个 URL 试过代填（防无限重试）
        # 以下字段只允许 worker 线程碰
        self._pw = None
        self.ctx = None
        self.browser = None
        self.page = None

    # ------------------------------------------------------------- 对外接口
    def start(self, url: str) -> Dict:
        if not (url or "").strip():
            raise LiveBrowserError("缺少要打开的网址")
        full = url if url.startswith("http") else "https://" + url.strip()
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        self.stopped.clear()
        self.info["started_at"] = time.time()
        self.thread = threading.Thread(target=self._worker, args=(full,),
                                       name="live-browser-worker", daemon=True)
        self.thread.start()
        try:
            return self._dispatch(lambda: self.snapshot(), timeout=60)
        except Exception:
            self.stop()
            raise

    def stop(self) -> Dict:
        """关会话并**等到真释放**——残留 chromium 会一直占着 profile 目录。
        正常情况 <1s；worker 正卡在 goto 时最多等一个 goto 超时。
        释放的判据不是线程退出，而是**锁文件被 worker 的收尾删掉**：
        ctx.close() 返回了但进程还没完全退出时，下一个会话仍可能起不来（实测）。"""
        was = self.page is not None
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=GOTO_TIMEOUT / 1000 + 20)
        for _ in range(40):                     # ≤10s：等锁文件消失
            if not LOCK_FILE.exists():
                break
            time.sleep(0.25)
        with self.lock:
            self.page = None
            self.frame = None
            self.frame_seq = 0
            info = dict(self.info)
        return {"ok": True, "was_running": was, "last_url": info.get("url", "")}

    def _acquire_lock(self) -> None:
        """文件锁：防两个 chromium 抢同一个持久 profile。
        锁里写 PID——持有它的进程还活着才算数；进程被强杀（演示时最容易发生）
        立即视为陈旧，而不是让下一个会话干等 10 分钟。"""
        if LOCK_FILE.exists():
            pid = 0
            try:
                pid = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                pid = 0
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    alive = False          # 进程已死 → 锁是残留
            if alive:
                raise LiveBrowserError(
                    "已有一个内嵌浏览器会话在跑。先在界面上停止它再开新的。")
            try:
                LOCK_FILE.unlink()
            except OSError:
                pass
        try:
            LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass

    def _dispatch(self, fn, timeout: float = 30.0):
        """把一个必须跑在 worker 线程上的 Playwright 操作投递过去并等结果。"""
        if not self.thread or not self.thread.is_alive():
            raise LiveBrowserError("会话已结束")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._calls.put((fn, fut))
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise LiveBrowserError(f"浏览器操作超时（>{timeout:.0f}s）")

    def _info_set(self, **kv) -> None:
        """info 会被 worker 线程写、HTTP 线程读——写入必须持锁，
        否则 snapshot() 的 dict(self.info) 会撞上 update() 抛
        RuntimeError: dictionary changed size during iteration（实测 500）。"""
        with self.lock:
            self.info.update(kv)

    # ------------------------------------------------------------- worker 线程
    def _worker(self, full_url: str) -> None:
        """全部 Playwright 调用都发生在这个线程。"""
        try:
            self._do_start(full_url)
        except Exception as e:
            self._info_set(error=str(e), login="error")
            self._teardown()
            return
        next_frame = 0.0
        while not self.stopped.is_set():
            fn = fut = None
            try:
                fn, fut = self._calls.get(timeout=0.08)
            except queue.Empty:
                pass
            if fn is not None:
                try:
                    if fut.set_running_or_notify_cancel():
                        fut.set_result(fn())
                except Exception as e:
                    fut.set_exception(e)
            now = time.time()
            if now >= next_frame:
                try:
                    self._capture()
                except Exception:
                    pass
                next_frame = now + (BURST_INTERVAL if now < self.burst_until
                                    else FRAME_INTERVAL)
        self._teardown()

    def _do_start(self, full_url: str) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR), headless=True, viewport=VIEWPORT)
        except Exception:
            self.browser = self._pw.chromium.launch(headless=True, viewport=VIEWPORT)
            self.ctx = self.browser.new_context(viewport=VIEWPORT)
        self.page = self.ctx.pages[0] if getattr(self.ctx, "pages", None) else self.ctx.new_page()
        try:
            self.page.goto(full_url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
        except Exception as e:
            raise LiveBrowserError(f"打开页面失败: {e}")
        self._info_set(url=full_url)
        self._maybe_autologin()
        self._refresh_info()
        self._capture()

    def _teardown(self) -> None:
        for closer in (lambda: self.ctx and self.ctx.close(),
                       lambda: self.browser and self.browser.close(),
                       lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self.ctx = self.browser = self.page = None
        self._pw = None
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------- 截帧与状态
    def _capture(self) -> None:
        if self.page is None:
            return
        png = self.page.screenshot(type="jpeg", quality=JPEG_QUALITY, timeout=8000)
        if png != self.frame:            # 页面没动就不推帧
            self.frame = png
            self.frame_seq += 1
        self._refresh_info()

    def _refresh_info(self) -> None:
        if self.page is None:
            return
        try:
            url = self.page.url or ""
            title = self.page.title() or ""
        except Exception:
            return
        login = self.info.get("login")
        if login != "error":
            if _LOGIN_URL_RE.search(url) or self.page.locator("input[type=password]").count():
                login = "login_page"
            elif url:
                login = "ok"
        self._info_set(url=url, title=title, login=login)

    # ------------------------------------------------------------- 登录代填
    def _maybe_autologin(self) -> None:
        """只在「页面明确是登录表单」时代填账号密码——不猜登录页（verifier 的纪律）。

        Figma 网页端有账号密码登录表单（与官方 API 不同）；SSO / 验证码场景
        代填不会命中，状态停在 login_page，前端提示人工接管。"""
        s = _load_settings()
        fig = s.get("figma") or {}
        account, password = (fig.get("account") or "").strip(), fig.get("password") or ""
        if not (account and password) or self.page is None:
            return
        try:
            self._autologin_url = self.page.url or ""   # 无论成败，这个 URL 只试一次
            email_el = self.page.locator(
                "input[name=email], input[type=email], input[autocomplete*=username]").first
            pass_el = self.page.locator("input[type=password]").first
            if not (email_el.count() and pass_el.count() and email_el.is_visible()):
                return
            email_el.fill(account, timeout=4000)
            pass_el.fill(password, timeout=4000)
            btn = self.page.locator(
                "button[type=submit], button:has-text('登录'), button:has-text('Log in')").first
            if btn.count():
                btn.click(timeout=4000)
                try:
                    self.page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
        except Exception as e:      # 代填失败不致命：状态留在 login_page，人来接管
            self._info_set(error=f"自动登录未完成：{e}")

    # ------------------------------------------------------------- 输入转发
    def input(self, payload: Dict) -> Dict:
        def job():
            self._input_on_worker(payload)
            return {"ok": True}
        self.burst_until = time.time() + 1.2
        return self._dispatch(job, timeout=20.0)

    def _input_on_worker(self, payload: Dict) -> None:
        if self.page is None:
            raise LiveBrowserError("会话已结束")
        kind = payload.get("kind")
        x, y = self._scale(payload)
        mouse = self.page.mouse
        if kind == "move":
            mouse.move(x, y)
        elif kind == "down":
            mouse.move(x, y)
            mouse.down(button=payload.get("button") or "left")
        elif kind == "up":
            mouse.up(button=payload.get("button") or "left")
        elif kind == "click":
            opts = {"button": "right"} if payload.get("button") == "right" else {}
            if payload.get("dbl"):
                opts["click_count"] = 2
            mouse.click(x, y, **opts)
        elif kind == "wheel":
            mouse.move(x, y)
            mouse.wheel(float(payload.get("dx") or 0), float(payload.get("dy") or 0))
        elif kind == "key":
            self.page.keyboard.press(str(payload.get("key") or ""))
        elif kind == "type":
            self.page.keyboard.type(str(payload.get("text") or ""), delay=12)
        else:
            raise LiveBrowserError(f"未知的输入类型: {kind}")

    def _scale(self, payload: Dict):
        """前端画布坐标 → 后端视口坐标。"""
        vw = float(payload.get("vw") or VIEWPORT["width"])
        vh = float(payload.get("vh") or VIEWPORT["height"])
        sx = VIEWPORT["width"] / max(vw, 1.0)
        sy = VIEWPORT["height"] / max(vh, 1.0)
        x = max(0.0, min(float(payload.get("x") or 0) * sx, VIEWPORT["width"] - 1))
        y = max(0.0, min(float(payload.get("y") or 0) * sy, VIEWPORT["height"] - 1))
        return x, y

    # ------------------------------------------------------------- 属性面板
    def inspect(self, payload: Dict) -> Dict:
        return self._dispatch(lambda: self._inspect_on_worker(payload), timeout=25.0)

    def _inspect_on_worker(self, payload: Dict) -> Dict:
        """点选 → 读右侧属性面板。

        右栏是普通 DOM（canvas 只在中间的画布区），所以从「视口最右侧竖条」
        里收文本。这个读法**不依赖 Figma 的内部类名**（它们是构建期哈希，
        每个版本都会变），退化到任何 Web 应用的右侧栏都能读。"""
        if self.page is None:
            raise LiveBrowserError("会话已结束")
        if payload.get("x") or payload.get("y"):
            ix, iy = self._scale(payload)
            self.page.mouse.click(ix, iy)
            self.page.wait_for_timeout(450)     # 等右栏刷新
        data = self.page.evaluate(
            """() => {
              const W = innerWidth, H = innerHeight;
              // 从最右缘往左找「竖向长条」容器：Figma 右栏宽约 240-320px
              const probe = document.elementFromPoint(W - 12, H * 0.35);
              let panel = probe;
              while (panel && panel !== document.body) {
                const r = panel.getBoundingClientRect();
                if (r.width >= 180 && r.width <= 460 && r.height > H * 0.5
                    && r.right > W - r.width - 8) break;
                panel = panel.parentElement;
              }
              const root = (panel && panel !== document.body) ? panel : document.body;
              const lines = (root.innerText || '').split('\\n')
                .map(s => s.trim()).filter(s => s && s.length <= 60);
              return { text: lines.slice(0, 80), title: document.title, url: location.href };
            }""")
        self.burst_until = time.time() + 1.2
        return {"panel": data["text"], "title": data["title"], "url": data["url"]}

    # ------------------------------------------------------------- 跨线程只读
    def snapshot(self) -> Dict:
        with self.lock:
            info = dict(self.info)
            info["frame_seq"] = self.frame_seq
            info["alive"] = self.page is not None
        return info

    def latest_frame(self) -> Optional[bytes]:
        with self.lock:
            return self.frame


class _Manager:
    """单会话管理器。锁内完成「取旧→停旧→建新」，避免半个旧会话挂着。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Optional[LiveSession] = None

    def start(self, url: str) -> Dict:
        with self._lock:
            if self._session and self._session.page is not None:
                raise LiveBrowserError("内嵌浏览器已在运行；先停止当前会话再开新的。")
            if self._session:
                self._session.stop()
                self._session = None
            s = LiveSession()
            snap = s.start(url)
            self._session = s
            return snap

    def get(self) -> Optional[LiveSession]:
        with self._lock:
            return self._session

    def stop(self) -> Dict:
        with self._lock:
            s, self._session = self._session, None
        if s:
            return s.stop()
        return {"ok": True, "was_running": False}


# 模块级单例：整个进程一个受控会话（路由层都从这里拿）。
manager = _Manager()
