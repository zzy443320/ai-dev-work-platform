"""Stage 4: browser verification.

Two honest jobs, and it says so when it cannot do them:
  * before/after screenshots of the defect's own route, driven by the ticket's
    repro steps — structured steps when present, otherwise AI-planned actions
    from the ticket text ("复刻操作"). The *same* route + action sequence is
    replayed for the "after" shot so the pixel diff compares like with like.
  * console/pageerror capture + blank-page detection: a blank page is NOT a
    pass. A wrong route (HTML residue like `/strong` used to leak through) or
    a half-dead dev server both look "error-free" to the console listener, so
    the page's text content is probed before any screenshot is trusted.
  * login: when the app needs a session, the ticket's own repro steps are reused
    as a login sequence with the configured credentials substituted in — see
    `_login_creds`. No login page is ever guessed.
"""
import re
import socket
import time

from . import usage
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .analyzer import strip_html

_ROUTE_RE = re.compile(r"(?<![\w:/])(/[A-Za-z0-9_\-./]{2,60})")
# strip_html 把 URL 的协议+域名剥掉后只留无前导斜杠的 path（demo-workspace/schedule），
# 工单正文里的路径提示也常是这个形态 —— 单段 /xxx 误报太多，裸路径要求至少两段。
_BARE_PATH_RE = re.compile(r"(?<![\w@:/.])([A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)+)")
_SKIP_ROUTES = ("/api/", "/static/", "/node_modules/", "/src/", "/dist/")

_PLAN_SYSTEM = "你是前端 E2E 测试工程师，把缺陷复现步骤转成可执行的 Playwright 动作序列。只输出 JSON。"

# 白屏判定：body.innerText 几乎为空。SPA 路由打不开时 console 往往是安静的，
# 文本量比 console 更可靠地回答「页面真的渲染出来了吗」。
_BLANK_PROBE_JS = (
    "() => ({ text: ((document.body && document.body.innerText) || '').trim(),"
    " nodes: document.querySelectorAll('body *').length })"
)
_BLANK_MIN_CHARS = 12

# 登录识别：只在「动作序列已经像登录」时才用设置里的账号密码复刻，不主动猜登录页。
_USER_SEL_RE = re.compile(r"user|account|email|phone|mobile|login[-_]?name|用户名|账号|邮箱|手机",
                          re.I)
_PASS_SEL_RE = re.compile(r"pass|pwd|密码", re.I)
_LOGIN_URL_RE = re.compile(r"/(login|signin|sign-in|auth|sso|passport)(\.\w+)?([/?#]|$)", re.I)
_LOGIN_HINT_RE = re.compile(r"login|signin|sign-in|logon|passport|username|用户|账号|登录", re.I)


class ScreenshotVerifier:
    def __init__(
        self,
        base_url: str,
        headless: bool = True,
        screenshot_dir: str = "./screenshots",
        timeout_ms: int = 10000,
        ai=None,
        auth: Optional[Dict] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_ms = timeout_ms
        self.ai = ai  # AIModel，用于把复现步骤编排成动作序列；可为 None
        self.auth = auth or {}  # 页面登录凭据，见 _auth_context


    # ------------------------------------------------------------- helpers
    def reachable(self, probe_seconds: float = 1.5) -> bool:
        host, port = "localhost", 80
        m = re.match(r"https?://([^:/]+)(?::(\d+))?", self.base_url)
        if m:
            host = m.group(1)
            port = int(m.group(2) or (443 if self.base_url.startswith("https") else 80))
        try:
            with socket.create_connection((host, port), timeout=probe_seconds):
                return True
        except OSError:
            return False

    @staticmethod
    def _bad_route(cand: str) -> bool:
        if any(s in cand for s in _SKIP_ROUTES):
            return True
        if re.search(r"\.\w{2,4}$", cand):
            return True
        return False

    @staticmethod
    def route_for(defect: Dict) -> str:
        """工单 → 页面路由。必须先剥 HTML：`</strong>` 在原文上会被抽成 /strong。"""
        explicit = str(defect.get("route") or "").strip()
        if explicit:
            return explicit
        text = " ".join(
            str(defect.get(k, "") or "") for k in ("url", "path", "title", "description", "desc")
        )
        text = strip_html(text)
        for m in _ROUTE_RE.finditer(text):
            cand = m.group(1)
            if not ScreenshotVerifier._bad_route(cand):
                return cand
        for m in _BARE_PATH_RE.finditer(text):
            cand = "/" + m.group(1)
            if not ScreenshotVerifier._bad_route(cand):
                return cand
        return ""

    @staticmethod
    def actions_for(defect: Dict, route: str) -> List[Tuple[str, str, Optional[str]]]:
        """Turn the ticket's *structured* repro steps into Playwright actions."""
        actions: List[Tuple[str, str, Optional[str]]] = [("goto", route, None)]
        steps = defect.get("repro_steps") or defect.get("steps") or []
        if isinstance(steps, str):
            steps = [s.strip(" -.") for s in steps.splitlines() if s.strip()]
        for step in steps:
            if not isinstance(step, dict):
                continue
            op = str(step.get("op", "")).lower()
            target = str(step.get("target", step.get("selector", "")))
            value = step.get("value")
            if op in ("goto", "click", "fill", "wait", "wait_for"):
                actions.append((op, target or route, value))
        return actions

    # ------------------------------------------------------------ planning
    def plan_actions(
        self, defect: Dict, route: str, on_delta=None
    ) -> Tuple[List[Tuple[str, str, object]], str]:
        """复刻操作：结构化步骤优先 → AI 编排 → 退化为只 goto（并明说不够验收）。"""
        structured = self.actions_for(defect, route)
        if len(structured) > 1:
            return structured, "使用工单自带的结构化复现步骤"
        cfg = getattr(getattr(self, "ai", None), "cfg", None)
        if self.ai is not None and cfg is not None and not getattr(cfg, "is_mock", True):
            planned, note = self._ai_plan(defect, route, on_delta=on_delta)
            if planned:
                return planned, note or "AI 根据工单描述编排的复现操作"
        return structured, "工单没有可解析的复现步骤，仅打开路由截图（不构成完整验收）"

    def _ai_plan(self, defect: Dict, route: str, on_delta=None) -> Tuple[Optional[List], str]:
        title = str(defect.get("title") or "")
        desc = strip_html(str(defect.get("description") or defect.get("desc") or ""))[:1200]
        steps = defect.get("repro_steps") or defect.get("steps") or []
        if isinstance(steps, str):
            steps_text = steps[:800]
        else:
            steps_text = "\n".join(str(s) for s in steps)[:800]
        prompt = (
            "把下面的前端缺陷复现步骤转成 Playwright 动作序列，用于在浏览器里复刻操作并前后截图对比。\n\n"
            f"缺陷标题：{title}\n"
            f"页面路由（已知起点）：{route or '/'}\n"
            f"复现步骤原文：\n{steps_text or desc}\n\n"
            "可用动作（只能用这几种）：\n"
            '- {"op":"goto","target":"/路径"} 打开页面（第一个动作通常是它）\n'
            '- {"op":"click","target":"CSS选择器"}\n'
            '- {"op":"fill","target":"CSS选择器","value":"要输入的文本"}\n'
            '- {"op":"wait","value":"等待毫秒"}\n'
            '- {"op":"wait_for","target":"CSS选择器","value":"超时毫秒"}\n\n'
            "要求：\n"
            '1. 只输出 JSON：{"route":"/起点路径","actions":[...]}\n'
            "2. 最多 10 个动作；CSS 选择器没有把握时宁可用 wait_for 探测或省略该步\n"
            "3. 不要安排截图动作，截图由系统自动完成"
        )
        try:
            with usage.step("verify"):
                result = self.ai.complete(prompt, system=_PLAN_SYSTEM, max_tokens=8000)
        except Exception as e:  # AI 层的任何意外都不该炸掉截图流程
            return None, f"AI 编排异常：{e}"
        if result.get("error"):
            return None, f"AI 编排失败：{result['error']}"
        raw = result.get("actions")
        if not isinstance(raw, list) or not raw:
            return None, "AI 未给出动作序列"
        cleaned: List[Tuple[str, str, object]] = []
        for a in raw[:10]:
            if not isinstance(a, dict):
                continue
            op = str(a.get("op", "")).lower()
            if op not in ("goto", "click", "fill", "wait", "wait_for"):
                continue
            target = str(a.get("target", "") or "").strip()
            value = a.get("value")
            if op in ("goto", "click", "wait_for") and not target and op != "goto":
                continue
            cleaned.append((op, target, value))
        if not cleaned:
            return None, "AI 动作序列全部不合法"
        if cleaned[0][0] != "goto":
            cleaned.insert(0, ("goto", route or "/", None))
        return cleaned, "AI 根据工单描述编排的复现操作（选择器可能不准，逐步留痕）"

    # --------------------------------------------------------------- public
    def capture(
        self,
        label: str,
        phase: str,
        defect: Dict,
        route: Optional[str] = None,
        actions: Optional[List[Tuple[str, str, object]]] = None,
        on_delta=None,
    ) -> Dict:
        """拍一张。route/actions 显式传入时是重放（after 必须与 before 同页面同操作）。"""
        replay = route is not None or actions is not None
        route = route or self.route_for(defect)
        route_source = "replay" if replay else ("ticket" if route else "default")
        if not route:
            route = "/"
        if actions is not None:
            plan_note = "重放提案生成时的同一操作序列（保证前后可对比）"
        else:
            actions, plan_note = self.plan_actions(defect, route, on_delta=on_delta)
        path = self.screenshot_dir / f"{_safe(label)}_{phase}.png"

        if not self.reachable():
            return {
                "status": "unreachable",
                "phase": phase,
                "route": route,
                "screenshot": "",
                "error": f"{self.base_url} 不可达，跳过截图（没有真实验证，请勿视为通过）",
                "console_errors": [],
                "route_source": route_source,
            }
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            return {"status": "skipped", "phase": phase, "route": route,
                    "error": f"playwright 未安装: {e}", "console_errors": [],
                    "screenshot": "", "route_source": route_source}

        console_errors: List[str] = []
        page_errors: List[str] = []
        actions_log: List[Dict] = []
        dead = False
        blank_info: Optional[Dict] = None
        final_url = ""
        title = ""
        t0 = time.time()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = self._auth_context(browser, actions)
                page = context.new_page()
                page.on(
                    "console",
                    lambda m: console_errors.append(m.text) if m.type == "error" else None,
                )
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                try:
                    for op, target, value in actions:
                        if op == "goto" and not target:
                            target = route
                        try:
                            self._do_action(page, op, target, value)
                            actions_log.append({"op": op, "target": target, "ok": True})
                        except Exception as e:
                            actions_log.append(
                                {"op": op, "target": target, "ok": False,
                                 "error": str(e)[:140]})
                            if op == "goto":
                                dead = True  # 页面都没打开，后面的动作没有意义
                                break
                    if not dead:
                        try:
                            page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                        except Exception:
                            pass  # networkidle 只是尽力等待，超时不致命
                        blank_info = self._blank_probe(page)
                        if blank_info.get("blank"):
                            # 给慢渲染的 SPA 二次机会
                            page.wait_for_timeout(2000)
                            blank_info = self._blank_probe(page)
                    try:
                        page.screenshot(path=str(path), full_page=True)
                    except Exception:
                        pass
                    final_url = page.url
                    title = page.title()
                    if self.auth.get("storage_state"):
                        try:  # 把登录态留给下一次（after 重放 / 后续工单）
                            context.storage_state(path=self.auth["storage_state"])
                        except Exception:
                            pass
                finally:
                    browser.close()
        except Exception as e:
            return {
                "status": "error",
                "phase": phase,
                "route": route,
                "screenshot": str(path) if path.exists() else "",
                "error": f"{type(e).__name__}: {str(e)[:300]}",
                "console_errors": console_errors[:20],
                "page_errors": page_errors[:20],
                "route_source": route_source,
                "actions": [a[0] + (f" {a[1]}" if a[1] else "") for a in actions],
                "actions_log": actions_log,
                "plan_note": plan_note,
            }

        base = {
            "phase": phase,
            "route": route,
            "route_source": route_source,
            "final_url": final_url,
            "page_title": title,
            "console_errors": console_errors[:20],
            "page_errors": page_errors[:20],
            "actions": [a[0] + (f" {a[1]}" if a[1] else "") for a in actions],
            # JSON 可序列化的动作序列，专供 after 阶段重放（base64 之外的都存原值）
            "replay_actions": [[a[0], a[1], a[2]] for a in actions],
            "actions_log": actions_log,
            "plan_note": plan_note,
            "seconds": round(time.time() - t0, 1),
        }

        if dead:
            return {**base, "status": "error",
                    "screenshot": str(path) if path.exists() else "",
                    "error": f"页面未能打开（goto {route} 失败），没有可验证的内容"}
        if blank_info and blank_info.get("blank"):
            return {**base, "status": "blank",
                    "screenshot": str(path),
                    "page_text_sample": blank_info.get("text", "")[:160],
                    "error": (
                        f"页面渲染出白屏/空内容（文本长度 {blank_info.get('text_len', 0)}，"
                        f"DOM 元素 {blank_info.get('nodes', 0)} 个）。路由 {route} 可能不是工单"
                        "对应页面，或前端未正常构建/启动。空白页不能视为「渲染无报错」。")}
        if blank_info:
            base["page_text_sample"] = blank_info.get("text", "")[:160]

        errors = console_errors + page_errors
        return {**base, "status": "ok" if not errors else "error_visible",
                "screenshot": str(path)}

    def compare(self, before: Dict, after: Dict) -> Dict:
        b, a = before.get("screenshot", ""), after.get("screenshot", "")
        if not (b and a and Path(b).exists() and Path(a).exists()):
            return {"status": "no_baseline", "reason": "缺少前后任一张截图，无法做像素对比"}
        diff = self._pixel_diff(b, a)
        diff["status"] = "changed" if not diff.get("identical") else "identical"
        diff["before"] = b
        diff["after"] = a
        return diff

    # ---------------------------------------------------------- auth (login)
    def _auth_context(self, browser, actions):
        """建 context；配了账号密码就复用「工单复现动作」里的登录动作。

        设计要点：**不再让 AI 猜登录**。若工单复现步骤（或 AI 编排结果）里本来
        就有「输入账号/密码 → 点登录」，就把值换成设置里的真实凭据，在截图前
        执行一次；否则只带上上次留下的 storage_state，让已登录会话延续。
        任何一步失败都静默跳过 —— 登录只是尽力而为，绝不炸掉截图。
        """
        opts = {"viewport": {"width": 1440, "height": 900}}
        if self.auth.get("storage_state"):
            opts["storage_state"] = self.auth["storage_state"]
        try:
            context = browser.new_context(**opts)
        except Exception:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            creds = self._login_creds(actions)
            if creds:
                page = context.new_page()
                self._perform_login(page, *creds)
                try:
                    page.close()  # 截图始终在一个全新 page 上，避免残留滚动位置
                except Exception:
                    pass
        except Exception:
            pass
        return context

    def _login_creds(self, actions) -> Optional[Tuple[str, str, str]]:
        """从动作序列里认领「登录」，返回 (url, 账号选择器, 密码选择器)。

        只认那些操作数 ≥2 且明确像登录的表单：两个 fill 里至少一个命中用户名/
        邮箱类选择器、另一个命中密码类选择器。普通「填表单 → 保存」的复现步骤
        不会被误判成登录。
        """
        user = self.auth.get("email") or self.auth.get("username") or ""
        pwd = self.auth.get("password") or ""
        if not (user and pwd):
            return None
        fills: List[Tuple[str, str]] = []
        clicked = ""
        for op, target, value in (actions or []):
            if op in ("goto", "click") and target and not clicked:
                clicked = target
                if op == "click":
                    break
            elif op == "fill" and target:
                fills.append((target, str(value or "")))
        if not clicked:
            for op, target, _ in reversed(actions or []):
                if op == "click" and target:
                    clicked = target
                    break
        # 只看**前两个** fill 是不是登录框：后面出现的密码框（比如「重复周期必填」
        # 这类工单里的表单）不该把一次普通填表误判成登录。
        head = fills[:2] if len(fills) == 2 else []
        u_sel = p_sel = ""
        for sel, _val in head:
            if not u_sel and _USER_SEL_RE.search(sel):
                u_sel = sel
            elif not p_sel and _PASS_SEL_RE.search(sel):
                p_sel = sel
        url = ""
        gotos = [t for op, t, _ in (actions or []) if op == "goto"]
        for t in gotos:
            if self._is_login_url(t if t.startswith(("http", "/")) else "/" + t):
                url = t
                break
        if u_sel and p_sel:
            # 只有「账号框 + 密码框」成对出现才认；再确认周边确有登录语境，
            # 否则普通表单里恰好有个密码字段也会被误判。
            hay = " ".join([u_sel, p_sel, clicked] + [f[0] for f in fills] + gotos).lower()
            if url or _LOGIN_HINT_RE.search(hay):
                return url, u_sel, p_sel
        any_pass = any(_PASS_SEL_RE.search(sel) for sel, _ in fills)
        if url and clicked and (any_pass or _LOGIN_HINT_RE.search(clicked)):
            # 两段式登录页：先跳登录、点开「账号登录」再填，选择器交给默认值
            return url, "#username", "#password"
        return None

    def _perform_login(self, page, url: str, u_sel: str, p_sel: str) -> None:
        """打开登录页、填凭据、提交、等跳转。全链路静默失败。"""
        page.goto(self._abs(url), wait_until="domcontentloaded", timeout=self.timeout_ms)
        page.fill(u_sel, self.auth.get("email") or self.auth.get("username") or "",
                  timeout=self.timeout_ms)
        page.fill(p_sel, self.auth.get("password") or "", timeout=self.timeout_ms)
        clicked = False
        for sel in ("button[type=submit]", ".login-btn", ".btn-login",
                    "button:has-text('登录')", "button:has-text('登 录')",
                    "button:has-text('Login')", "input[type=submit]"):
            try:
                page.click(sel, timeout=1500)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            try:
                page.press(p_sel, "Enter", timeout=1500)
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    @staticmethod
    def _is_login_url(url: str) -> bool:
        return bool(_LOGIN_URL_RE.search(str(url or "")))

    def _abs(self, url: str) -> str:
        if url.startswith("http"):
            return url
        if url.startswith("/"):
            return self.base_url + url
        return self.base_url + "/" + url

    # -------------------------------------------------------------- internals
    @staticmethod
    def _blank_probe(page) -> Dict:
        try:
            data = page.evaluate(_BLANK_PROBE_JS)
        except Exception:
            return {"blank": False, "text": "", "text_len": -1, "nodes": -1}
        text = str(data.get("text", "") or "")
        return {"blank": len(text) < _BLANK_MIN_CHARS,
                "text": text, "text_len": len(text), "nodes": data.get("nodes", -1)}

    def _do_action(self, page, op: str, target: str, value) -> None:
        if op == "goto":
            page.goto(self._abs(target if target.startswith(("http", "/")) else "/" + target))
        elif op == "click":
            page.click(target, timeout=self.timeout_ms)
        elif op == "fill":
            page.fill(target, str(value or ""), timeout=self.timeout_ms)
        elif op == "wait":
            page.wait_for_timeout(int(value or "500"))
        elif op == "wait_for":
            page.wait_for_selector(target, timeout=int(value or "5000"))
        else:
            raise ValueError(f"unknown op: {op}")

    def _pixel_diff(self, a: str, b: str) -> Dict:
        try:
            from PIL import Image, ImageChops
        except ImportError as e:
            return {"status": "skipped", "reason": f"Pillow 未安装: {e}"}
        try:
            img_a = Image.open(a).convert("RGB")
            img_b = Image.open(b).convert("RGB")
        except Exception as e:
            return {"status": "error", "reason": f"读取截图失败: {e}"}
        if img_a.size != img_b.size:
            return {
                "identical": False,
                "reason": "size mismatch",
                "a_size": list(img_a.size),
                "b_size": list(img_b.size),
            }
        diff = ImageChops.difference(img_a, img_b)
        bbox = diff.getbbox()
        hist = diff.histogram()
        total = img_a.size[0] * img_a.size[1]
        changed = sum(hist[i] for i in range(1, 768))
        return {
            "identical": bbox is None,
            "diff_bbox": list(bbox) if bbox else None,
            "changed_channel_samples": changed,
            "changed_ratio": round(changed / max(total * 3, 1), 5),
            "size": list(img_a.size),
        }


def _safe(s) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))[:80]
