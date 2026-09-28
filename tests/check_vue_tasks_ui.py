# -*- coding: utf-8 -*-
"""三个任务页签（需求开发 / 接口联调 / 代码测试）+ 产出物面板/详情弹窗的迁移断言。

前端迁移已收口：`GET /` 直接返回 Vue 3 产物（`/v2` 已下线，旧版静态页已删除），
所以这里对唯一实现（`GET /`，Vue 3）跑一遍断言。断言口径尽量沿用既有用例：

  - 页签能切过去、内容在（面板存在 + 关键控件 id 全在）
  - #figma-tree / #test-preview 空态占位（与 #run-log 同一套 :empty CSS 契约）
  - 需求开发：Figma 取稿通道切换落 localStorage('figma-fetch-mode')
  - 代码测试：预览文件按钮读只读接口（无仓库文件时给错误提示，不炸）
  - 跑一次 Mock 任务（三选一）→ 产出物卡片出现 → 打开详情弹窗 → 采纳 → 状态变「已采纳」
  - 产出物面板角标（N 待采纳 / M 条）
  - 没有把 undefined / NaN 渲染出来

用法：.venv\\Scripts\\python.exe tests/check_vue_tasks_ui.py
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
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(tmp: Path):
    dirs = {k: tmp / k for k in
            ("proposals", "artifacts", "team_runs", "chat_sessions", "attachments",
             "screenshots", "usage", "knowledge_base", "repo")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    # 造一个「像前端 monorepo」的目标仓库，否则 /api/repo/file 与 preflight 会拒
    (dirs["repo"] / "packages").mkdir(parents=True, exist_ok=True)
    (dirs["repo"] / "package.json").write_text(
        json.dumps({"name": "demo-monorepo", "private": True}, ensure_ascii=False),
        encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "init", "-b", "main"], cwd=dirs["repo"], capture_output=True)
    (dirs["repo"] / "packages" / "src").mkdir(parents=True, exist_ok=True)
    (dirs["repo"] / "packages" / "src" / "Hello.tsx").write_text(
        "export default function Hello() {\n  return <div>hi</div>;\n}\n", encoding="utf-8")

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


# 一个页面里三个任务页签的关键控件（id 与旧版一致）
PANES = {
    "reqdev": ["pane-reqdev", "req-figma-url", "req-figma-mode", "req-figma-token-field",
               "req-framework", "btn-figma", "req-figma-hint", "figma-tree",
               "req-desc", "req-context", "req-notes", "btn-reqdev"],
    "apidebug": ["pane-apidebug", "api-doc", "api-base", "api-framework",
                 "api-context", "api-notes", "btn-apidebug"],
    "codetest": ["pane-codetest", "test-file", "test-framework", "btn-test-load",
                 "test-focus", "btn-codetest", "test-preview"],
}


def run(base: str, *, errors: list, consoles: list) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        # 运行时错误（如 ReferenceError）只出在 console 里，pageerror 抓不到
        page.on("console", lambda m: consoles.append(f"console.{m.type}: {m.text}")
                if m.type in ("error",) else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(900)

        # ── 1. 三个页签的关键控件齐全 ──
        for tab, ids in PANES.items():
            page.evaluate(f"() => switchTab('{tab}')")
            page.wait_for_timeout(350)
            check(f"#pane-{tab} 切过去 .active",
                  page.eval_on_selector(f"#pane-{tab}", "el => el.classList.contains('active')"))
            missing = page.evaluate("""ids => ids.filter(id => !document.getElementById(id))""", ids)
            check(f"{tab} 页控件齐全（{len(ids)} 个）", not missing, str(missing))

        # ── 2. #figma-tree / #test-preview 空态占位（:empty 契约）──
        for pid in ("figma-tree", "test-preview"):
            ph = page.eval_on_selector(f"#{pid}", "el => getComputedStyle(el, '::before').content")
            check(f"#{pid} 空态占位提示存在",
                  bool(ph) and ph not in ("none", "normal"), str(ph)[:50])
            kids = page.eval_on_selector(f"#{pid}", "el => el.children.length")
            check(f"#{pid} 空态无子节点（:empty 生效）", kids == 0, f"{kids} 子节点")

        # ── 3. 需求开发：Figma 取稿通道切换落 localStorage ──
        page.evaluate("() => switchTab('reqdev')")
        page.wait_for_timeout(300)
        page.click("#req-figma-mode .seg-btn[data-mode='browser']")
        page.wait_for_timeout(200)
        saved = page.evaluate("() => localStorage.getItem('figma-fetch-mode')")
        check("切到浏览器通道写 figma-fetch-mode=browser（与旧版同键）",
              saved == "browser", repr(saved))
        check("浏览器通道下 Token 字段隐藏",
              page.eval_on_selector("#req-figma-token-field", "el => el.classList.contains('hidden')"))
        page.click("#req-figma-mode .seg-btn[data-mode='token']")
        page.wait_for_timeout(200)
        check("切回 token 通道恢复 Token 字段",
              not page.eval_on_selector("#req-figma-token-field", "el => el.classList.contains('hidden')"))

        # ── 4. 代码测试：预览文件（只读接口，路径存在→有内容）──
        page.evaluate("() => switchTab('codetest')")
        page.wait_for_timeout(300)
        page.fill("#test-file", "packages/src/Hello.tsx")
        page.click("#btn-test-load")
        page.wait_for_timeout(1500)
        preview = page.inner_text("#test-preview")
        check("预览文件读到内容", "Hello" in preview, repr(preview[:80]))

        # ── 5. 跑一次 Mock 任务：接口联调（表单最简，只要求粘贴文档）──
        page.evaluate("() => switchTab('apidebug')")
        page.wait_for_timeout(300)
        page.fill("#api-doc", '{"paths": {"/api/v1/list": {"get": {}}}}')
        page.click("#btn-apidebug")
        try:
            page.wait_for_function(
                "() => !!document.querySelector('#artifacts .pcard')", timeout=120000)
        except Exception:
            check("产出物卡片在 120s 内出现", False, "没有卡片")

        cards = page.query_selector_all("#artifacts .pcard")
        check("产出物卡片已渲染", len(cards) > 0, f"{len(cards)} 张")
        badge = page.inner_text("#artifact-count")
        check("面板角标显示「N 待采纳 / M 条」", "待采纳" in badge and "条" in badge, repr(badge))
        check("产出物面板在任务页签可见",
              page.eval_on_selector("#artifact-panel", "el => !el.classList.contains('hidden')"))
        check("产出物类型标签=接口联调",
              page.inner_text("#artifact-type-label").strip() == "接口联调",
              repr(page.inner_text("#artifact-type-label")))
        check("顶栏状态药丸转 ok",
              "产出物已生成" in page.inner_text("#status"), repr(page.inner_text("#status")))

        # ── 6. 打开详情弹窗 ──
        # 不用固定等待：先等弹窗可见，再等异步详情（状态药丸文案）落到 DOM。
        page.click("#artifacts .pcard")
        page.wait_for_function(
            "() => { const m = document.querySelector('#amodal');"
            " return m && !m.classList.contains('hidden') }", timeout=15000)
        page.wait_for_function(
            "() => { const s = document.querySelector('#amodal-status');"
            " return s && s.textContent.trim().length > 0 }", timeout=15000)
        check("详情弹窗打开（#amodal 可见）",
              not page.eval_on_selector("#amodal", "el => el.classList.contains('hidden')"))
        check("弹窗标题是产出物 id",
              len(page.inner_text("#amodal-title").strip()) > 0, repr(page.inner_text("#amodal-title")))
        check("弹窗状态=待采纳",
              page.inner_text("#amodal-status").strip() == "待采纳",
              repr(page.inner_text("#amodal-status")))
        check("弹窗有 meta 行",
              len(page.query_selector_all("#amodal-meta span")) > 0)
        check("文件区渲染了将写入的文件",
              "将写入的文件" in page.inner_text("#am-files"), repr(page.inner_text("#am-files")[:80]))
        check("采纳按钮可见",
              not page.eval_on_selector("#a-btn-approve", "el => el.classList.contains('hidden')"))
        check("强制采纳按钮隐藏（非闸门失败态）",
              page.eval_on_selector("#a-btn-force", "el => el.classList.contains('hidden')"))
        check("撤销按钮隐藏（未采纳）",
              page.eval_on_selector("#a-btn-undo", "el => el.classList.contains('hidden')"))

        # ── 7. 采纳：状态 → 已采纳，撤销按钮出现 ──
        page.fill("#am-note", "UI 自检采纳")
        page.click("#a-btn-approve")
        try:
            page.wait_for_function(
                "() => (document.querySelector('#amodal-status')||{}).textContent.trim() === '已采纳'",
                timeout=60000)
        except Exception:
            check("采纳后弹窗状态变「已采纳」", False,
                  repr(page.inner_text("#amodal-status")))
        check("采纳后弹窗状态=已采纳",
              page.inner_text("#amodal-status").strip() == "已采纳",
              repr(page.inner_text("#amodal-status")))
        check("采纳后出现撤销按钮",
              not page.eval_on_selector("#a-btn-undo", "el => el.classList.contains('hidden')"))
        applied = page.evaluate(
            "async () => (await (await fetch('/api/artifacts?type=apidebug')).json())[0].status")
        check("后端产出物状态=applied", applied == "applied", repr(applied))

        # 采纳后工作区文件真被写了（且未 commit）
        r = page.evaluate("""async () => {
          const j = await (await fetch('/api/repo/file?path=' + encodeURIComponent('src/api/newApi.ts'))).json();
          return j.content || j.error || '';
        }""")
        check("采纳把文件写进工作区（只读接口可读到）", "fetchList" in r, repr(r[:80]))

        # 撤销：状态回到待采纳？——undo 后状态由后端决定，这里只验证按钮联动
        page.click("#a-btn-undo")
        page.wait_for_timeout(1500)
        undone = page.evaluate(
            "async () => (await (await fetch('/api/artifacts?type=apidebug')).json())[0].status")
        check("撤销后状态离开 applied", undone != "applied", repr(undone))

        # 关掉弹窗
        page.click("#amodal .close")
        page.wait_for_timeout(300)
        check("关闭后弹窗隐藏",
              page.eval_on_selector("#amodal", "el => el.classList.contains('hidden')"))

        # ── 8. 切走页签：产出物面板隐藏 ──
        page.evaluate("() => switchTab('stats')")
        page.wait_for_timeout(400)
        check("切到统计页后产出物面板隐藏",
              page.eval_on_selector("#artifact-panel", "el => el.classList.contains('hidden')"))

        # ── 9. 没有把 undefined / NaN 渲染出来 ──
        for pid in ("pane-reqdev", "pane-apidebug", "pane-codetest", "artifact-panel"):
            txt = page.inner_text(f"#{pid}")
            bad = [b for b in ("undefined", "NaN", "[object Object]") if b in txt]
            check(f"#{pid} 无 undefined/NaN", not bad, str(bad))

        browser.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vue_tasks_"))
    proc = None
    errors, consoles = [], []
    try:
        proc, base, dirs = start_server(tmp)
        run(base, errors=errors, consoles=consoles)
    finally:
        if proc:
            proc.kill()

    print(f"\n合计 {len(OK)} 项通过 / {len(BAD)} 项失败")
    for e in errors:
        print("PAGEERROR:", e)
    for c in consoles:
        print("CONSOLE:", c)
    if BAD or errors or consoles:
        for b in BAD:
            print("FAIL:", b)
        return 1
    print("PASS: 三个任务页签 + 产出物面板/详情弹窗（唯一实现 GET /，Vue 3）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
