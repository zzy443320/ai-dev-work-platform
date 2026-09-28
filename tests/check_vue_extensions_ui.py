# -*- coding: utf-8 -*-
"""扩展能力页签（技能 / MCP）自检。

界面已收口为唯一实现：`GET /` 直接返回 Vue 3 产物（旧版原生页与 `/v2` 都已删除），
所以只跑一遍。断言：
  - 两个 panel（#ext-skill-panel / #ext-mcp-panel）与全部关键控件 id 在
  - 空态文案逐字一致（还没有技能 / 还没有 MCP 服务）
  - 角标「N 启用 / M 条」随启停 / 增删联动
  - 技能表单：新增 → 卡片出现 → 停用（ext-off + 角标变化）→ 编辑回填 → 改名保存 → 删除
  - MCP 表单：stdio ↔ http 字段互斥切换；假命令测试连接 → 失败路径（表单内
    #mcp-test-result 与卡片 #mcp-out-<id> 两个输出口都验到）
  - 表单关闭状态：#skill-form / #mcp-form / #mcp-test-result 初始都带 hidden
  - 没有把 undefined / NaN 渲染出来

用法：.venv\\Scripts\\python.exe tests/check_vue_extensions_ui.py
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
from ui_select import pick_select                    # noqa: E402

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
    """与 check_vue_tasks_ui.py 同一套自举：临时实例 + mock 前端 monorepo。"""
    dirs = {k: tmp / k for k in
            ("proposals", "artifacts", "team_runs", "chat_sessions", "attachments",
             "screenshots", "usage", "knowledge_base", "repo")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
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
        "extensions": {"skills": [], "mcp": []},
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


SKILL_IDS = ["skill-count", "btn-skill-add", "skill-form",
             "ext-skill-name", "ext-skill-desc", "ext-skill-content",
             "ext-skill-scope-all", "ext-skill-scope-reqdev", "ext-skill-scope-apidebug",
             "ext-skill-scope-codetest", "ext-skill-enabled", "skill-list"]
MCP_IDS = ["mcp-count", "btn-mcp-add", "mcp-form", "ext-mcp-transport",
           "ext-mcp-timeout", "ext-mcp-enabled", "ext-mcp-stdio-fields", "ext-mcp-command",
           "ext-mcp-args", "ext-mcp-env", "ext-mcp-cwd", "ext-mcp-http-fields",
           "ext-mcp-url", "ext-mcp-headers", "btn-mcp-test", "mcp-test-result", "mcp-list"]


def hidden(page, sel: str) -> bool:
    return page.eval_on_selector(sel, "el => el.classList.contains('hidden')")


def run(base: str, *, errors: list, consoles: list) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        # 运行时错误（如 ReferenceError）只出在 console 里，pageerror 抓不到。
        # 「Failed to load resource」是浏览器对非 2xx fetch 的固定噪音
        # （假命令的 MCP 测试连接必然 400，新旧各两条），失败路径已由断言覆盖。
        page.on("console", lambda m: consoles.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(900)

        # ── 1. 切到扩展页签，两个 panel 与全部控件齐全 ──
        page.evaluate("() => switchTab('extensions')")
        page.wait_for_timeout(800)
        check(f"#pane-extensions 切过去 .active",
              page.eval_on_selector("#pane-extensions", "el => el.classList.contains('active')"))
        missing = page.evaluate(
            "ids => ids.filter(id => !document.getElementById(id))", SKILL_IDS + MCP_IDS)
        check(f"扩展页控件齐全（{len(SKILL_IDS + MCP_IDS)} 个）", not missing, str(missing))

        # ── 2. 空态 + 初始隐藏态 ──
        check(f"技能空态文案一致",
              "还没有技能" in page.inner_text("#skill-list"), repr(page.inner_text("#skill-list")[:60]))
        check(f"MCP 空态文案一致",
              "还没有 MCP 服务" in page.inner_text("#mcp-list"), repr(page.inner_text("#mcp-list")[:60]))
        check(f"技能角标 0 启用 / 0",
              page.inner_text("#skill-count").strip() == "0 启用 / 0", repr(page.inner_text("#skill-count")))
        check(f"MCP 角标 0 启用 / 0",
              page.inner_text("#mcp-count").strip() == "0 启用 / 0", repr(page.inner_text("#mcp-count")))
        for sel in ("#skill-form", "#mcp-form", "#mcp-test-result", "#ext-mcp-http-fields"):
            check(f"{sel} 初始 hidden", hidden(page, sel))
        check(f"#ext-mcp-stdio-fields 初始可见", not hidden(page, "#ext-mcp-stdio-fields"))
        kids = page.eval_on_selector("#mcp-test-result", "el => el.children.length")
        check(f"#mcp-test-result 初始无子节点", kids == 0, f"{kids} 子节点")

        # ── 3. 技能：新增 → 卡片 → 停用 → 编辑回填 → 改名 → 删除 ──
        page.click("#btn-skill-add")
        page.wait_for_timeout(250)
        check(f"新增技能打开表单", not hidden(page, "#skill-form"))
        check(f"表单打开后名字框自动聚焦",
              page.evaluate("() => document.activeElement && document.activeElement.id") == "ext-skill-name")
        page.fill("#ext-skill-name", "团队前端规范")
        page.fill("#ext-skill-desc", "组件一律函数式")
        page.fill("#ext-skill-content", "1. 组件一律函数式 + hooks；\n2. 样式用 less module。")
        page.check("#ext-skill-scope-reqdev")
        page.click("#skill-form button.btn-primary")
        page.wait_for_timeout(900)
        check(f"保存后表单关闭", hidden(page, "#skill-form"))
        cards = page.query_selector_all("#skill-list .ext-card")
        check(f"技能卡片已渲染", len(cards) == 1, f"{len(cards)} 张")
        check(f"卡片显示技能名", "团队前端规范" in page.inner_text("#skill-list"))
        check(f"卡片有范围徽标（需求开发）", "需求开发" in page.inner_text("#skill-list"))
        check(f"技能角标 1 启用 / 1",
              page.inner_text("#skill-count").strip() == "1 启用 / 1", repr(page.inner_text("#skill-count")))
        check(f"后端技能列表已落数",
              page.evaluate(
                  "async () => (await (await fetch('/api/extensions')).json()).skills.length") == 1)

        page.click("#skill-list .ext-card button:has-text('停用')")
        page.wait_for_timeout(700)
        check(f"停用后卡片带 ext-off",
              page.eval_on_selector("#skill-list .ext-card", "el => el.classList.contains('ext-off')"))
        check(f"停用后角标 0 启用 / 1",
              page.inner_text("#skill-count").strip() == "0 启用 / 1", repr(page.inner_text("#skill-count")))

        page.click("#skill-list .ext-card button:has-text('编辑')")
        page.wait_for_timeout(300)
        check(f"编辑打开表单", not hidden(page, "#skill-form"))
        check(f"编辑回填名称",
              page.input_value("#ext-skill-name") == "团队前端规范", repr(page.input_value("#ext-skill-name")))
        check(f"编辑回填内容",
              "hooks" in page.input_value("#ext-skill-content"))
        page.fill("#ext-skill-name", "团队前端规范 v2")
        page.click("#skill-form button.btn-primary")
        page.wait_for_timeout(900)
        check(f"改名保存后卡片更新", "团队前端规范 v2" in page.inner_text("#skill-list"))

        page.click("#skill-list .ext-card button:has-text('删除')")
        page.wait_for_timeout(900)
        check(f"删除后回到空态", "还没有技能" in page.inner_text("#skill-list"))
        check(f"删除后角标归零",
              page.inner_text("#skill-count").strip() == "0 启用 / 0", repr(page.inner_text("#skill-count")))

        # ── 4. MCP：stdio ↔ http 字段切换 + 表单内测试连接（失败路径） ──
        page.click("#btn-mcp-add")
        page.wait_for_timeout(250)
        check(f"新增 MCP 打开表单", not hidden(page, "#mcp-form"))
        pick_select(page, "#ext-mcp-transport", "http")  # 原生 select 被自绘下拉接管
        page.wait_for_timeout(200)
        check(f"切 http 后 stdio 字段隐藏", hidden(page, "#ext-mcp-stdio-fields"))
        check(f"切 http 后 http 字段可见", not hidden(page, "#ext-mcp-http-fields"))
        pick_select(page, "#ext-mcp-transport", "stdio")  # 原生 select 被自绘下拉接管
        page.wait_for_timeout(200)
        check(f"切回 stdio 后 stdio 字段可见", not hidden(page, "#ext-mcp-stdio-fields"))
        check(f"切回 stdio 后 http 字段隐藏", hidden(page, "#ext-mcp-http-fields"))

        page.fill("#ext-mcp-name", "demo-mcp")
        page.fill("#ext-mcp-command", "definitely-not-a-real-mcp-cmd-xyz")
        page.click("#btn-mcp-test")
        try:
            page.wait_for_function(
                "() => (document.querySelector('#mcp-test-result')||{textContent:''}).textContent.includes('连接失败')",
                timeout=60000)
            check(f"表单内测试连接走失败路径", True)
        except Exception:
            check(f"表单内测试连接走失败路径", False,
                  repr(page.inner_text("#mcp-test-result")[:120]))
        check(f"测试结果框可见", not hidden(page, "#mcp-test-result"))
        check(f"失败结果带 log-title",
              "连接失败" in page.inner_text("#mcp-test-result"))

        # ── 5. 保存 MCP → 卡片 + 卡片级测试输出 #mcp-out-<id> ──
        page.click("#mcp-form button.btn-primary")
        page.wait_for_timeout(900)
        check(f"保存后表单关闭", hidden(page, "#mcp-form"))
        mcards = page.query_selector_all("#mcp-list .ext-card")
        check(f"MCP 卡片已渲染", len(mcards) == 1, f"{len(mcards)} 张")
        check(f"卡片显示服务名与 stdio 标记",
              "demo-mcp" in page.inner_text("#mcp-list") and "stdio" in page.inner_text("#mcp-list"))
        check(f"MCP 角标 1 启用 / 1",
              page.inner_text("#mcp-count").strip() == "1 启用 / 1", repr(page.inner_text("#mcp-count")))
        out_id = page.eval_on_selector(
            "#mcp-list .mcp-test-out", "el => el.id")
        check(f"卡片输出框 id=mcp-out-<id>", out_id.startswith("mcp-out-"), repr(out_id))
        page.click("#mcp-list .ext-card button:has-text('测试')")
        try:
            page.wait_for_function(
                f"() => (document.querySelector('#{out_id}')||{{textContent:''}}).textContent.includes('连接失败')",
                timeout=60000)
            check(f"卡片级测试输出写入 {out_id}", True)
        except Exception:
            check(f"卡片级测试输出写入 {out_id}", False,
                  repr(page.eval_on_selector(f"#{out_id}", "el => el.textContent")[:120]))

        # ── 6. 停用 + 删除 MCP ──
        page.click("#mcp-list .ext-card button:has-text('停用')")
        page.wait_for_timeout(700)
        check(f"MCP 停用后角标 0 启用 / 1",
              page.inner_text("#mcp-count").strip() == "0 启用 / 1", repr(page.inner_text("#mcp-count")))
        page.click("#mcp-list .ext-card button:has-text('删除')")
        page.wait_for_timeout(900)
        check(f"MCP 删除后回到空态", "还没有 MCP 服务" in page.inner_text("#mcp-list"))

        # ── 7. 没有把 undefined / NaN 渲染出来 ──
        txt = page.inner_text("#pane-extensions")
        bad = [b for b in ("undefined", "NaN", "[object Object]") if b in txt]
        check(f"#pane-extensions 无 undefined/NaN", not bad, str(bad))

        browser.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vue_ext_"))
    proc = None
    errors, consoles = [], []
    try:
        proc, base, dirs = start_server(tmp)
        print("唯一实现（GET /，Vue 3）：")
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
    print("PASS: 扩展能力页签（技能 / MCP）（唯一实现 GET /，Vue 3）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
