# -*- coding: utf-8 -*-
"""问答页签：对唯一实现（`GET /`，Vue 3）跑一遍断言。

迁移期这里是「旧版 `/` 与新版 `/v2` 各跑一遍，断言两版一致」；前端收口后
`/` 已直接返回 Vue 产物、`/v2` 已删除，所以只剩一条实现可测，改为跑一次。

问答页签与统计页签不同 —— 它不是「纯读」，而是有一条真实的状态机：
提问 → 流式增量 → 落盘 → 会话列表刷新 → 回放。所以这个用例的重点是
**把 check_chat_ui.py 已经锁定的那套断言原样保留**，而不是另发明一套口径。

用法：.venv\\Scripts\\python.exe tests/check_vue_chat_ui.py
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright      # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(tmp: Path):
    """起一个临时实例：聊天/附件/产出物/账本各自落在临时目录。"""
    dirs = {k: tmp / k for k in
            ("chat_sessions", "attachments", "artifacts", "usage", "proposals",
             "knowledge_base", "team_runs", "screenshots", "repo")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    (tmp / "ui_settings.json").write_text(json.dumps({
        "repo": {"path": str(dirs["repo"]), "branch": "main"},
        "ai": {"mock": True, "api_key": "", "provider": "openai", "model": "mock"},
        "gate": {"commands_text": ""},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    port = free_port()
    env = dict(os.environ)
    env.update({
        "SETTINGS_FILE": str(tmp / "ui_settings.json"),
        "KB_DIR": str(dirs["knowledge_base"]),
        "PROPOSAL_DIR": str(dirs["proposals"]),
        "ARTIFACT_DIR": str(dirs["artifacts"]),
        "TEAM_DIR": str(dirs["team_runs"]),
        "CHAT_DIR": str(dirs["chat_sessions"]),
        "ATTACH_DIR": str(dirs["attachments"]),
        "SCREENSHOT_DIR": str(dirs["screenshots"]),
        "USAGE_DIR": str(dirs["usage"]),
        "REPO_PATH": str(dirs["repo"]),
        "PYTHONIOENCODING": "utf-8",
    })
    code = ("import uvicorn; from web.server import app; "
            f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='error')")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if proc.poll() is not None:
            out = (proc.stdout.read() or b"").decode("utf-8", "replace")
            raise RuntimeError(f"临时服务启动失败：\n{out[-2000:]}")
        try:
            urllib.request.urlopen(base + "/api/health", timeout=2).read()
            return proc, base, dirs
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("临时服务 30s 内未就绪")


def api(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def make_png(path: Path) -> Path:
    path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8Dw"
        "HwAFAAH/q842iQAAAABJRU5ErkJggg=="))
    return path


def wait_replies(page, n: int) -> None:
    page.wait_for_function(
        "n => document.querySelectorAll('#chat-stream .chat-msg.ai:not(.pending)').length >= n",
        arg=n, timeout=40000)


def run(base: str, dirs, *, errors: list) -> None:
    """跑一遍问答全流程断言（页面是唯一实现：`GET /`）。"""
    png = make_png(dirs["_tmp"] / "pasted.png")
    logf = dirs["_tmp"] / "run.log"
    logf.write_text("TypeError: cannot read property 'a' of undefined\n  at app.js:12\n",
                    encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1680, "height": 1100}).new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("dialog", lambda d: d.accept())
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(800)

        page.evaluate("() => switchTab('chat')")
        page.wait_for_timeout(400)

        # ── 空态 ──
        empty = page.inner_text("#chat-stream")
        check("空态给出可问样例",
              "随便问点什么" in empty and "路由" in empty,
              empty[:60].replace("\n", " "))
        check("空态说明只读边界", "只读" in page.inner_text("#pane-chat"))
        check("说明条分行（>=2 个 br）",
              page.eval_on_selector("#pane-chat .info-note",
                                    "el => el.querySelectorAll('br').length") >= 2)

        # ── 浮层入口：产出物抽屉 / 使用说明浮窗 ──
        # 2026-09-30 改版：这两块原来占着右栏（320px），内容挤、还把对话区压窄，
        # 现在都由标题栏按钮打开浮层。断言锁住「默认关着 + 按钮能开 + 遮罩能关」，
        # 免得哪天又退化成常驻右栏（那种改法下 .chat-grid 只剩两栏会直接崩版）。
        check("两个浮层默认都是关着的",
              page.eval_on_selector("#chat-art-drawer", "el => el.classList.contains('hidden')")
              and page.eval_on_selector("#chat-help-pop", "el => el.classList.contains('hidden')"))
        page.click("#btn-chat-artifacts")
        page.wait_for_timeout(300)
        check("点「产出物」打开右侧抽屉（真的占到宽度）",
              page.eval_on_selector("#chat-art-drawer",
                                    "el => !el.classList.contains('hidden')")
              and page.eval_on_selector("#chat-art-drawer", "el => el.getBoundingClientRect().width") > 300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Esc 收起产出物浮窗",
              page.eval_on_selector("#chat-art-drawer", "el => el.classList.contains('hidden')"))
        page.click("#btn-chat-help")
        page.wait_for_timeout(300)
        check("点「使用说明」打开浮窗（含只读边界说明）",
              page.eval_on_selector("#chat-help-pop", "el => !el.classList.contains('hidden')")
              and "只读" in page.inner_text("#chat-help-pop"))
        # 点遮罩左上角关掉（点中心会落在居中浮窗上，被浮窗吃掉）
        page.click("#chat-float-mask", position={"x": 12, "y": 12})
        page.wait_for_timeout(300)
        check("点遮罩收起使用说明浮窗",
              page.eval_on_selector("#chat-help-pop", "el => el.classList.contains('hidden')"))

        # ── 一屏高度 / 吸顶 / 浮层内边距（2026-09-30）──
        # ① 问答页必须「一屏装下、页面不滚」。旧实现是 `100vh - 实测常量`（--fit-chat
        #    176px / side 121px），换布局常量就对不上（顶部导航把页签条藏了却仍按 176 减，
        #    白留 55px 死带）。现在改成从 #app 一路传到 pane 的高度链：内容矮就占满剩余
        #    视口，内容高才整页滚 —— 所以这条断言对两种布局、对顶栏高度变化都成立。
        # ② 侧边工作台的左导航吸顶位置必须在顶栏「下面」（旧 top:20 会滚进顶栏里）。
        # ③ 产出物浮窗的内容要与边框留出内边距（旧版内容距边框只有 1px = 边框本身）。
        # ④ 配置栏只剩抽屉一种形态（三栏经典已删）：开合由顶栏齿轮控制，遮罩/Esc 能关，
        #    内容超长时在抽屉自己框内滚，不把页面顶出滚动条。

        def scroll_overflow():
            return page.evaluate(
                "() => document.documentElement.scrollHeight - window.innerHeight")

        def set_layout(idx):
            """0=侧边工作台 / 1=顶部导航（LAYOUTS 的顺序）。"""
            page.locator(".layout-switch .ls-btn").nth(idx).click()
            page.wait_for_timeout(400)

        for name, idx in (("侧边", 0), ("顶部", 1)):
            set_layout(idx)
            page.evaluate("() => switchTab('chat')")
            page.wait_for_timeout(350)
            over = scroll_overflow()
            check(f"{name}布局：问答页一屏装下、页面不滚", over == 0, f"overflow={over}px")
            # 网格底边要贴到内容区底边（= 视口高 − #app 下留白 18 − 面板下内边距 15）：
            # 只断「不滚」不够，占不满就是下方一条死白，正是这次要修掉的症状。
            bottom = page.evaluate(
                "() => document.querySelector('#pane-chat .chat-grid')"
                ".getBoundingClientRect().bottom")
            vh = page.evaluate("() => innerHeight")
            check(f"{name}布局：问答网格占满剩余高度",
                  abs((vh - 33) - bottom) <= 2, f"gridBottom={bottom:.1f} 期望≈{vh - 33}")
        set_layout(0)          # 后面都按侧边工作台量

        # 产出物浮窗的内边距（此时还没产出物，量的是面板自身的内容左沿 + 标题）
        page.click("#btn-chat-artifacts")
        page.wait_for_timeout(350)
        pads = page.evaluate(
            """() => {
              const d = document.querySelector('#chat-art-drawer');
              const dr = d.getBoundingClientRect();
              const body = d.querySelector('.chat-drawer-body > .panel > *');
              const title = d.querySelector('.chat-float-title').getBoundingClientRect();
              return {body: body.getBoundingClientRect().left - dr.left,
                      title: title.left - dr.left};
            }""")
        check("产出物浮窗内容与边框有内边距（≥12px）",
              pads["body"] >= 12 and pads["title"] >= 12, str(pads))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # 吸顶：临时塞一个高占位把主列撑长（问答页本身正好一屏，滚不动）。
        # 占位必须放进 .col-main —— 左导航的 sticky 活动范围就是自己所在的 grid 行，
        # 行高由主列决定：塞 body 的话行高不变，它根本滚不动。
        SPACER = """(h) => {
          const s = document.createElement('div');
          s.id = 'tmp-tall'; s.style.height = h + 'px';
          document.querySelector('main.layout .col-main').appendChild(s);
        }"""
        DROP_SPACER = """() => {
          const s = document.getElementById('tmp-tall');
          if (s) s.remove();
          window.scrollTo(0, 0);
        }"""
        STICKY = """() => {
          const pick = (sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {top: r.top, bottom: r.bottom, h: r.height,
                    pos: getComputedStyle(el).position,
                    scrollable: el.scrollHeight > el.clientHeight + 2};
          };
          return {topbar: pick('.topbar'), nav: pick('.side-nav'), col: pick('.col-side')};
        }"""

        page.evaluate("() => switchTab('chat')")
        page.evaluate(SPACER, 1400)
        page.evaluate("() => window.scrollTo(0, 600)")
        page.wait_for_timeout(350)
        geo = page.evaluate(STICKY)
        check("侧边布局：左导航吸顶停在顶栏下方（没被顶栏盖住）",
              geo["nav"] is not None and geo["nav"]["top"] >= geo["topbar"]["bottom"] + 4
              and geo["nav"]["h"] > 100,
              f"navTop={geo['nav'] and geo['nav']['top']} topbarBottom={geo['topbar']['bottom']}")
        page.evaluate(DROP_SPACER)

        # 配置抽屉：默认关着（不盖内容）→ 齿轮开 → 超长内容在自己框内滚、
        # 且抽屉是 fixed 不参与页面高度 → 点遮罩关。
        drawer = """() => {
          const c = document.querySelector('.col-side');
          const r = c.getBoundingClientRect();
          return {pos: getComputedStyle(c).position, vis: getComputedStyle(c).visibility,
                  left: r.left, vw: innerWidth,
                  scrollable: c.scrollHeight > c.clientHeight + 2,
                  over: document.documentElement.scrollHeight - innerHeight};
        }"""
        check("抽屉默认关闭", page.evaluate(drawer)["vis"] == "hidden",
              str(page.evaluate(drawer)))
        page.click("#sidebar-toggle")
        page.wait_for_timeout(450)
        opened = page.evaluate(drawer)
        check("点齿轮打开配置抽屉（浮到内容之上、贴右边缘）",
              opened["pos"] == "fixed" and opened["vis"] == "visible"
              and opened["left"] > opened["vw"] - 400, str(opened))
        page.evaluate("""(h) => {
          const s = document.createElement('div');
          s.id = 'tmp-tall-side'; s.style.height = h + 'px';
          document.querySelector('.col-side #settings-panel').appendChild(s);
        }""", 1400)
        page.wait_for_timeout(250)
        tall = page.evaluate(drawer)
        check("抽屉内容超长时在自己框内滚、不把页面顶出滚动条",
              tall["scrollable"] and tall["over"] <= 0, str(tall))
        page.evaluate("""() => {
          const s = document.getElementById('tmp-tall-side');
          if (s) s.remove();
        }""")
        page.click("#config-mask", position={"x": 12, "y": 300})
        page.wait_for_timeout(400)
        check("点遮罩关闭配置抽屉", page.evaluate(drawer)["vis"] == "hidden")
        # 键盘路径：抽屉是浮层，Esc 也得能关（与问答两个浮层同一约定）
        page.click("#sidebar-toggle")
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        check("Esc 关闭配置抽屉", page.evaluate(drawer)["vis"] == "hidden")
        page.wait_for_timeout(200)

        # ── 一问一答 ──
        page.fill("#chat-text", "这个项目的路由是怎么配的？")
        page.click("#btn-chat-send")
        wait_replies(page, 1)
        msgs = page.eval_on_selector_all(
            "#chat-stream .chat-msg",
            "els => els.map(e => ({me: e.classList.contains('me'),"
            " body: (e.querySelector('.chat-body')||{}).innerText || ''}))")
        check("一问一答两条气泡", len(msgs) == 2, str(len(msgs)))
        check("用户气泡是原问题",
              bool(msgs) and msgs[0]["me"] and "路由" in msgs[0]["body"],
              msgs[0]["body"][:40] if msgs else "")
        check("助手气泡有内容且不再是 pending",
              len(msgs) > 1 and not msgs[1]["me"] and len(msgs[1]["body"]) > 20,
              (msgs[1]["body"][:60] if len(msgs) > 1 else ""))
        check("发送后按钮恢复、停止按钮收起",
              page.eval_on_selector("#btn-chat-send", "el => !el.disabled")
              and page.eval_on_selector("#btn-chat-stop", "el => el.classList.contains('hidden')"))
        check("mock 回答如实标注",
              "Mock" in page.inner_text("#chat-stream .chat-msg.ai .chat-body"))

        # ── 会话落盘 / chip / 回放 ──
        sess = api(base, "/api/chat/sessions")["sessions"]
        check("后端出现 1 个会话", len(sess) == 1, str(len(sess)))
        chips = page.eval_on_selector_all("#chat-sessions .chat-chip", "els => els.length")
        check("会话 chip 渲染出来", chips == 1, str(chips))

        page.reload(wait_until="networkidle")
        page.evaluate("() => switchTab('chat')")
        page.wait_for_timeout(700)
        chips2 = page.eval_on_selector_all("#chat-sessions .chat-chip", "els => els.length")
        check("刷新后会话列表还在", chips2 == 1, str(chips2))
        page.click("#chat-sessions .chat-chip")
        page.wait_for_timeout(600)
        replayed = page.eval_on_selector_all("#chat-stream .chat-msg", "els => els.length")
        check("打开历史会话回放两条消息", replayed == 2, str(replayed))

        # ── 改动提案卡片 ──
        page.fill("#chat-text", "帮我把 src/api.ts 里的常量改名")
        page.click("#btn-chat-send")
        wait_replies(page, 2)
        page.wait_for_selector(".chat-art", timeout=20000)
        art_text = page.inner_text(".chat-art")
        check("改动提案卡片出现",
              "改动提案" in art_text and "src/components/Demo/Demo.vue" in art_text,
              art_text[:80].replace("\n", " "))
        check("卡片写明「未写入仓库」",
              "没有写入仓库" in art_text or "采纳" in art_text)

        # ── 新建 / 清空 / 删除 ──
        page.click("#btn-chat-new")
        page.wait_for_timeout(400)
        check("新建后回到空态",
              "随便问点什么" in page.inner_text("#chat-stream"))
        page.click("#chat-sessions .chat-chip")
        page.wait_for_timeout(500)
        page.click("#btn-chat-clear")
        page.wait_for_timeout(600)
        check("清空后消息为零、会话仍在",
              page.eval_on_selector_all("#chat-stream .chat-msg", "els => els.length") == 0
              and len(api(base, "/api/chat/sessions")["sessions"]) == 1)
        page.click("#btn-chat-del")
        page.wait_for_timeout(700)
        check("删除后会话列表清空",
              api(base, "/api/chat/sessions")["sessions"] == []
              and page.eval_on_selector_all("#chat-sessions .chat-chip", "els => els.length") == 0)

        # ── 偏好落 localStorage ──
        page.uncheck("#chat-use-repo")
        page.wait_for_timeout(300)
        saved = page.evaluate("() => localStorage.getItem('chat-prefs')")
        check("偏好落 chat-prefs",
              saved and '"useRepo":false' in saved.replace(" ", ""), str(saved))

        # ── Enter 发送 ──
        page.fill("#chat-text", "用 Enter 直接发送")
        page.press("#chat-text", "Enter")
        wait_replies(page, 1)
        check("Enter 直接发送并清空输入框",
              page.eval_on_selector_all("#chat-stream .chat-msg", "els => els.length") == 2
              and page.eval_on_selector("#chat-text", "el => el.value === ''"))

        # ── 附件：上传文件入口 ──
        page.click("#btn-chat-new")
        page.wait_for_timeout(400)
        page.set_input_files("#chat-file", str(png))
        page.wait_for_selector("#chat-attach .chat-att-chip:not(.uploading)", timeout=15000)
        chip = page.inner_text("#chat-attach .chat-att-chip")
        check("上传图片出现 chip（带文件名与体积）",
              "pasted.png" in chip and "B" in chip, chip.replace("\n", " ")[:60])
        check("图片 chip 带缩略图",
              page.eval_on_selector_all("#chat-attach .chat-att-thumb", "els => els.length") == 1)
        check("有附件后提示语隐藏",
              page.eval_on_selector("#chat-attach-hint", "el => el.classList.contains('hidden')"))

        # ── 附件：粘贴入口 ──
        page.evaluate(
            """() => {
              const dt = new DataTransfer();
              const f = new File([new Uint8Array([137,80,78,71])], 'paste2.png',
                                 { type: 'image/png' });
              dt.items.add(f);
              document.querySelector('#chat-text').dispatchEvent(
                new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }));
            }""")
        page.wait_for_function(
            "() => document.querySelectorAll('#chat-attach .chat-att-chip:not(.uploading)').length >= 2",
            timeout=15000)
        check("粘贴图片也进 chip 行",
              page.eval_on_selector_all("#chat-attach .chat-att-chip", "els => els.length") == 2)

        # ── 附件移除 ──
        # 删掉最后一张（粘贴进来的 paste2.png），把上传的那张留在待发队列里 ——
        # 这样后面「随消息发出」的断言能同时覆盖「图片」和「文本」两种附件。
        page.click("#chat-attach .chat-att-chip:last-child .chat-att-del")
        page.wait_for_timeout(300)
        check("移除按钮生效",
              page.eval_on_selector_all("#chat-attach .chat-att-chip", "els => els.length") == 1
              and "pasted.png" in page.inner_text("#chat-attach"))

        # ── 附件：文本文件 + 随消息发出 ──
        page.set_input_files("#chat-file", str(logf))
        page.wait_for_function(
            "() => document.querySelectorAll('#chat-attach .chat-att-chip:not(.uploading)').length >= 2",
            timeout=15000)
        check("文本附件显示文件名而非缩略图",
              "run.log" in page.inner_text("#chat-attach")
              and page.eval_on_selector_all("#chat-attach .chat-att-thumb", "els => els.length") == 1)

        page.fill("#chat-text", "这个报错和这张图有关吗？")
        page.click("#btn-chat-send")
        page.wait_for_timeout(600)
        check("发送后待发附件清空",
              page.eval_on_selector_all("#chat-attach .chat-att-chip", "els => els.length") == 0)
        att_text = page.inner_text("#chat-stream .chat-msg.me")
        # 此时待发队列里是「上传的那张图 + run.log」（粘贴的那张上一步已被删掉）。
        # 别写成 `"pasted.png" in att_text` 之外的粗判 —— `paste2.png` 不含
        # 子串 `pasted.png`，用那个当条件会连带把粘贴路径也放进来。
        check("用户气泡内嵌附件（图片 + 文本）",
              "pasted.png" in att_text and "run.log" in att_text
              and "paste2.png" not in att_text,
              att_text[:110].replace("\n", " "))
        check("已发送消息里的图片是缩略图",
              page.eval_on_selector_all("#chat-stream .chat-msg.me .chat-msg-thumb",
                                        "els => els.length") >= 1)
        wait_replies(page, 1)

        # ── 拖拽入口 ──
        page.click("#btn-chat-new")
        page.wait_for_timeout(400)
        page.evaluate(
            """() => {
              const drop = document.querySelector('#chat-drop');
              const dt = new DataTransfer();
              dt.items.add(new File(['dropped text'], 'dropped.txt', { type: 'text/plain' }));
              drop.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true }));
              drop.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true }));
            }""")
        page.wait_for_function(
            "() => document.querySelectorAll('#chat-attach .chat-att-chip:not(.uploading)').length >= 1",
            timeout=15000)
        check("拖拽文件进 chip 行",
              "dropped.txt" in page.inner_text("#chat-attach"))
        page.click("#chat-attach .chat-att-chip .chat-att-del")
        page.wait_for_timeout(300)

        # 消息攒多之后页面也不能被撑出滚动条（该滚的是 #chat-stream，不是页面）
        over = page.evaluate(
            "() => document.documentElement.scrollHeight - window.innerHeight")
        check("多轮消息后页面仍不滚（消息流自己滚）", over == 0, f"overflow={over}px")

        browser.close()


def main() -> int:
    errors = []
    tmp = Path(tempfile.mkdtemp(prefix="vue-chat-"))
    (tmp / "_tmp").mkdir(exist_ok=True)
    proc, base, dirs = start_server(tmp)
    dirs["_tmp"] = tmp / "_tmp"
    try:
        print("\n  —— 唯一实现：GET / （Vue 3）——")
        run(base, dirs, errors=errors)
        check("无 pageerror", not errors, "; ".join(errors)[:300])
    finally:
        if proc.poll() is None:
            proc.terminate()

    print(f"\n{'全部通过' if not BAD else '失败 %d 项：%s' % (len(BAD), '；'.join(BAD))}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
