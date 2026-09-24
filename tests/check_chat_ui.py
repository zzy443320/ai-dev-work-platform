# -*- coding: utf-8 -*-
"""UI 检查：问答页签（第 2 个）+ 会话 / 流式回答 / 改动提案。

覆盖：
1. 「问答」是第 2 个页签（统计之后、缺陷修复之前），点进去面板真的显示；
2. 空态给的是「可以问什么」的样例，而不是一片空白；
3. 提问 → 用户气泡 + 助手回答气泡；回答走 SSE（流式连接），不是一次性的；
4. 会话落盘：提问后后端出现 1 个会话、chips 渲染出来、刷新页面后会话还在；
5. 切会话 / 新会话 / 清空 / 删除 四个操作的行为（删除后列表为空）；
6. 「可出改动提案」开启时，要求改代码的回答会带一张改动提案卡，
   点「查看 / 采纳」能打开产出物详情弹窗；产出物按 type=chat 归档；
7. 关闭「允许查仓库」的状态写进 localStorage 并在刷新后保持；
8. 无 pageerror、无控制台报错。

自包含：自己起一个临时服务实例，并用环境变量把 **设置文件 / 会话目录 / 产出物目录 /
用量账本 / 目标仓库** 全部指到临时目录——所以它既不会读你的真实配置，也不会往你的
真实目录里写任何东西。AI 走 mock（临时设置里写死 mock=true），不消耗 token。

用法：
    python tests/check_chat_ui.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

OUT_DIR = PROJECT_ROOT / ".workbuddy"
FAILURES = []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)
    return cond


# --------------------------------------------------------------- 临时环境
def seed_repo(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "api.ts").write_text(
        "export const WEEK_TOKEN = 'week';\n", encoding="utf-8")
    (root / "README.md").write_text("# 临时仓库\n", encoding="utf-8")


def write_settings(path: Path, repo: Path) -> None:
    path.write_text(json.dumps({
        "repo": {"path": str(repo), "branch": "main"},
        "ai": {"mock": True, "api_key": "", "provider": "openai", "model": "mock"},
        "gate": {"commands_text": ""},
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(env_extra: dict, port: int, settings: Path):
    code = (
        "import uvicorn;"
        "from web.server import app;"
        f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='error')"
    )
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if proc.poll() is not None:
            out = (proc.stdout.read() or b"").decode("utf-8", "replace")
            raise RuntimeError(f"临时服务启动失败：\n{out[-2000:]}")
        try:
            urllib.request.urlopen(base + "/api/health", timeout=2).read()
            return proc, base
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("临时服务 30s 内未就绪")


def api(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_replies(page, n: int) -> None:
    page.wait_for_function(
        "n => document.querySelectorAll('#chat-stream .chat-msg.ai:not(.pending)').length >= n",
        arg=n, timeout=40000)


def run(base: str) -> None:
    chat_dir = Path(os.environ["CHAT_DIR_TMP"])
    art_dir = Path(os.environ["ARTIFACT_DIR_TMP"])
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1680, "height": 1100}).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{base}/", wait_until="networkidle")
        page.wait_for_timeout(800)

        # ── 1. 页签位置与面板 ──
        order = page.evaluate("() => [...document.querySelectorAll('.tab')].map(t => t.dataset.tab)")
        check("问答是第 2 个页签（统计之后、缺陷修复之前）",
              len(order) >= 2 and order[0] == "stats" and order[1] == "chat", str(order))
        check("统计仍是默认落地页",
              "active" in (page.get_attribute('.tab[data-tab="stats"]', "class") or ""))
        page.evaluate("() => switchTab('chat')")
        page.wait_for_timeout(400)
        check("点进问答面板后可见",
              page.eval_on_selector("#pane-chat",
                                    "el => el.classList.contains('active') && "
                                    "getComputedStyle(el).display !== 'none'"))
        check("缺陷面板同时被隐藏",
              page.eval_on_selector("#pane-defect", "el => getComputedStyle(el).display === 'none'"))
        check("产出物面板在问答页也挂出来了",
              page.eval_on_selector("#artifact-panel", "el => !el.classList.contains('hidden')"))

        # ── 2. 空态 ──
        empty = page.inner_text("#chat-stream")
        check("空态给出可问样例（不是空白）",
              "随便问点什么" in empty and "路由" in empty, empty[:60].replace("\n", " "))
        check("空态说明只读边界", "只读" in page.inner_text("#pane-chat"))

        # ── 3. 提问 → 流式回答 ──
        page.fill("#chat-text", "这个项目的路由是怎么配的？")
        page.click("#btn-chat-send")
        wait_replies(page, 1)
        msgs = page.eval_on_selector_all(
            "#chat-stream .chat-msg",
            "els => els.map(e => ({me: e.classList.contains('me'),"
            " body: (e.querySelector('.chat-body')||{}).innerText || ''}))")
        check("一问一答两条气泡", len(msgs) == 2, str(len(msgs)))
        check("用户气泡是原问题", msgs and msgs[0]["me"] and "路由" in msgs[0]["body"],
              msgs[0]["body"][:40] if msgs else "")
        check("助手气泡有内容且不再是 pending 占位",
              len(msgs) > 1 and not msgs[1]["me"] and len(msgs[1]["body"]) > 20,
              (msgs[1]["body"][:60] if len(msgs) > 1 else ""))
        check("发送后按钮恢复可用、停止按钮收起",
              page.eval_on_selector("#btn-chat-send", "el => !el.disabled")
              and page.eval_on_selector("#btn-chat-stop", "el => el.classList.contains('hidden')"))
        check("mock 回答如实标注（不假装是真结论）",
              "Mock" in page.inner_text("#chat-stream .chat-msg.ai .chat-body"))

        # ── 4. 会话落盘与刷新保持 ──
        sess = api(base, "/api/chat/sessions")["sessions"]
        check("后端出现 1 个会话", len(sess) == 1, str(len(sess)))
        check("会话标题取自首句提问",
              sess and sess[0]["title"].startswith("这个项目的路由"), sess[0]["title"] if sess else "")
        check("会话落盘到临时目录（不是项目根）",
              len(list(chat_dir.glob("*.json"))) == 1, str(chat_dir))
        chips = page.eval_on_selector_all("#chat-sessions .chat-chip",
                                         "els => els.map(e => e.innerText)")
        check("会话 chip 渲染出来", len(chips) == 1, str(chips)[:80])

        page.reload(wait_until="networkidle")
        page.evaluate("() => switchTab('chat')")
        page.wait_for_timeout(600)
        chips2 = page.eval_on_selector_all("#chat-sessions .chat-chip",
                                          "els => els.map(e => e.innerText)")
        check("刷新后会话列表还在", len(chips2) == 1, str(chips2)[:80])
        page.click("#chat-sessions .chat-chip")
        page.wait_for_timeout(500)
        replayed = page.eval_on_selector_all("#chat-stream .chat-msg", "els => els.length")
        check("打开历史会话能回放到两条消息", replayed == 2, str(replayed))

        # ── 5. 改动提案 → 产出物 ──
        page.fill("#chat-text", "帮我把 src/api.ts 里的常量改名")
        page.click("#btn-chat-send")
        wait_replies(page, 2)
        page.wait_for_selector(".chat-art", timeout=20000)
        art_text = page.inner_text(".chat-art")
        check("改动提案卡片出现", "改动提案" in art_text and "src/components/Demo/Demo.vue" in art_text,
              art_text[:80].replace("\n", " "))
        check("卡片写明「未写入仓库」", "没有写入仓库" in art_text or "采纳" in art_text)
        arts = api(base, "/api/artifacts?type=chat")
        check("产出物按 type=chat 归档", len(arts) == 1 and arts[0]["type"] == "chat",
              json.dumps(arts, ensure_ascii=False)[:120])
        check("产出物落在临时目录", len(list(art_dir.glob("*.json"))) == 1, str(art_dir))
        check("产出物是待采纳状态（没写仓库）", arts and arts[0]["status"] == "pending",
              arts[0]["status"] if arts else "")
        check("产出物面板列出该提案",
              page.eval_on_selector_all("#artifacts .pcard", "els => els.length") >= 1)

        page.click(".chat-art button")
        page.wait_for_selector("#amodal:not(.hidden)", timeout=15000)
        check("点「查看 / 采纳」打开产出物详情弹窗",
              not page.eval_on_selector("#amodal", "el => el.classList.contains('hidden')"))
        check("弹窗标题是产出物 id",
              "chat-" in page.inner_text("#amodal-title"), page.inner_text("#amodal-title"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Esc 关闭弹窗", page.eval_on_selector("#amodal", "el => el.classList.contains('hidden')"))

        # ── 6. 新会话 / 清空 / 删除 ──
        page.click("#btn-chat-new")
        page.wait_for_timeout(300)
        check("新会话后回到空态",
              "随便问点什么" in page.inner_text("#chat-stream"))
        check("新会话不落盘（提问才建）",
              len(api(base, "/api/chat/sessions")["sessions"]) == 1)

        page.click("#chat-sessions .chat-chip")
        page.wait_for_timeout(500)
        page.click("#btn-chat-clear")
        page.wait_for_timeout(600)
        check("清空会话后消息为空但会话保留",
              page.eval_on_selector_all("#chat-stream .chat-msg", "els => els.length") == 0
              and len(api(base, "/api/chat/sessions")["sessions"]) == 1)
        page.click("#btn-chat-del")
        page.wait_for_timeout(700)
        check("删除会话后后端与界面都空了",
              api(base, "/api/chat/sessions")["sessions"] == []
              and page.eval_on_selector_all("#chat-sessions .chat-chip", "els => els.length") == 0)

        # ── 7. 开关状态记忆 ──
        page.uncheck("#chat-use-repo")
        page.wait_for_timeout(200)
        saved = page.evaluate("() => localStorage.getItem('chat-prefs')")
        check("关闭「允许查仓库」写进 localStorage", saved and "false" in saved, str(saved))
        page.reload(wait_until="networkidle")
        page.evaluate("() => switchTab('chat')")
        page.wait_for_timeout(500)
        check("刷新后开关状态保持",
              page.eval_on_selector("#chat-use-repo", "el => el.checked === false"))
        page.check("#chat-use-repo")
        page.wait_for_timeout(200)

        # ── 8. Enter 发送 ──
        page.fill("#chat-text", "用 Enter 直接发送")
        page.press("#chat-text", "Enter")
        wait_replies(page, 1)
        check("Enter 键能直接发送（Shift+Enter 才换行）",
              page.eval_on_selector_all("#chat-stream .chat-msg", "els => els.length") == 2,
              page.inner_text("#chat-stream")[:60].replace("\n", " "))
        check("发送后输入框已清空",
              page.eval_on_selector("#chat-text", "el => el.value === ''"))

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUT_DIR / "chat-panel.png"), full_page=False)

        check("无 pageerror", not [e for e in errors if not e.startswith("console.")],
              "; ".join(errors[:3]))
        check("无控制台报错", not [e for e in errors if e.startswith("console.")],
              "; ".join([e for e in errors if e.startswith("console.")][:3]))
        browser.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="chat-ui-"))
    repo = tmp / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    seed_repo(repo)
    settings = tmp / "settings.json"
    write_settings(settings, repo)
    chat_dir = tmp / "chat_sessions"
    art_dir = tmp / "artifacts"
    usage_dir = tmp / "usage"
    for d in (chat_dir, art_dir, usage_dir):
        d.mkdir(parents=True, exist_ok=True)

    env_extra = {
        "SETTINGS_FILE": str(settings),
        "CHAT_DIR": str(chat_dir),
        "ARTIFACT_DIR": str(art_dir),
        "USAGE_DIR": str(usage_dir),
        "REPO_PATH": str(repo),
    }
    os.environ["CHAT_DIR_TMP"] = str(chat_dir)
    os.environ["ARTIFACT_DIR_TMP"] = str(art_dir)

    port = free_port()
    proc, base = start_server(env_extra, port, settings)
    try:
        print(f"临时实例 {base}（设置/会话/产出物/账本/仓库都指向 {tmp}）")
        run(base)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：" + "；".join(FAILURES))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
