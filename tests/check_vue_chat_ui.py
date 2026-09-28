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
