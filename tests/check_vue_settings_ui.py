# -*- coding: utf-8 -*-
"""配置侧栏（#settings-panel）与四个弹窗（#aimodal/#pmodal/#runmodal/#modal/#chmodal）自检。

界面已收口为唯一实现：`GET /` 直接返回 Vue 3 产物（旧版原生页与 `/v2` 都已删除），
所以只跑一遍。断言：
  - 侧栏配置面板：全部 cfg-* 控件在；ONES/Figma/页面登录三段的 id 齐全
  - 闸门预览：#gate-preview 渲染真实命令清单（显式命令 / package.json / 降级三态）
  - #ai-summary 摘要卡：Mock/真实调用徽标 + 模型 + 地址（短路径）
  - 大模型弹窗：#aimodal 开关；接入协议/鉴权方式切换；鉴权字段名随鉴权方式显隐；
    #ai-test-result 始终在 DOM（只切 hidden class）
  - 知识卡片弹窗：#modal 从卡片打开 → 标题 / meta 行 / 正文
  - 更新日志：#chmodal 打开 → #ch-body 有内容 / 分类 chips / 关闭
  - 没有把 undefined / NaN 渲染出来

用法：.venv\\Scripts\\python.exe tests/check_vue_settings_ui.py
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
    """与 check_vue_tasks_ui.py 同一套自举：临时实例 + mock 前端 monorepo + 一张知识卡片。"""
    dirs = {k: tmp / k for k in
            ("proposals", "artifacts", "team_runs", "chat_sessions", "attachments",
             "screenshots", "usage", "knowledge_base", "repo")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    (dirs["repo"] / "packages" / "src").mkdir(parents=True, exist_ok=True)
    (dirs["repo"] / "package.json").write_text(
        json.dumps({"name": "demo-monorepo", "private": True}, ensure_ascii=False),
        encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "init", "-b", "main"], cwd=dirs["repo"], capture_output=True)
    (dirs["repo"] / "packages" / "src" / "Hello.tsx").write_text(
        "export default function Hello() {\n  return <div>hi</div>;\n}\n", encoding="utf-8")

    # 知识卡片：让 #modal 有真实可打开的正文
    kb = dirs["knowledge_base"]
    (kb / "支付").mkdir(parents=True, exist_ok=True)
    (kb / "支付" / "DEF-1001.md").write_text(
        "---\n"
        "defect_id: DEF-1001\n"
        "title: 支付页金额显示为 0\n"
        "priority: P1\n"
        "status: applied\n"
        "ai_mode: mock\n"
        "gate_level: explicit\n"
        "created: 2026-09-01T10:00:00\n"
        "updated: 2026-09-02T11:30:00\n"
        "---\n\n"
        "# 支付页金额显示为 0\n\n"
        "**根因**：金额格式化函数在空值分支返回了 `0`。\n\n"
        "## 修复\n\n改为空字符串兜底。\n",
        encoding="utf-8")
    (kb / "INDEX.md").write_text(
        "# 知识库索引\n\n- [支付页金额显示为 0](支付/DEF-1001.md)\n", encoding="utf-8")

    (tmp / "ui_settings.json").write_text(json.dumps({
        "repo": {"path": str(dirs["repo"]), "branch": "main"},
        "ai": {"mock": True, "api_key": "", "provider": "openai", "model": "mock-model",
               "base_url": "https://example.com/v1"},
        "ones": {"base_url": "https://ones.example.com", "project_uuid": "P-1",
                 "team_uuid": "T-1", "email": "dev@example.com"},
        "gate": {"commands_text": "npm run type-check\nnpm run lint", "degraded": True},
        "playwright": {"base_url": "http://localhost:3000", "headless": True},
        "pagelogin": {"enabled": False, "email": "qa@example.com"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    port = free_port()
    env = dict(os.environ)
    env.update({
        "SETTINGS_FILE": str(tmp / "ui_settings.json"),
        "KB_DIR": str(kb),
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


# 侧栏配置面板的全部控件（id 与旧版 index.html:815-939 逐个对应）
CFG_IDS = [
    "settings-panel",
    "cfg-repo-path", "repo-hint", "cfg-repo-branch",
    "ai-summary-group", "ai-summary",
    "cfg-ones-url", "cfg-ones-uuid", "cfg-ones-team", "cfg-ones-email",
    "cfg-ones-password", "cfg-ones-password-hint", "cfg-ones-token", "cfg-ones-token-hint",
    "cfg-figma-token", "cfg-figma-token-hint",
    "cfg-pagelogin-enabled", "cfg-pagelogin-email", "cfg-pagelogin-password",
    "cfg-pagelogin-password-hint",
    "cfg-gate-commands", "cfg-gate-degraded", "gate-preview",
    "cfg-app-url", "cfg-headless",
    "btn-save",
]
# 大模型弹窗的全部控件（index.html:953-1105）
AI_IDS = [
    "aimodal", "cfg-ai-mock", "cfg-ai-preset", "cfg-ai-provider", "cfg-ai-auth",
    "cfg-ai-base", "cfg-ai-base-hint", "cfg-ai-endpoint", "cfg-ai-models-path",
    "ai-auth-header-field", "cfg-ai-auth-header", "cfg-ai-model", "btn-ai-models",
    "cfg-ai-model-hint", "cfg-ai-key", "cfg-ai-key-hint",
    "cfg-ai-temp", "cfg-ai-max-tokens", "cfg-ai-top-p", "cfg-ai-timeout",
    "cfg-ai-content-path", "cfg-ai-max-tokens-field", "cfg-ai-system-role",
    "cfg-ai-proxy", "cfg-ai-headers", "cfg-ai-body",
    "cfg-ai-json-mode", "cfg-ai-verify-ssl",
    "ai-test-result", "btn-ai-test", "btn-ai-dry", "btn-save-ai",
]


def hidden(page, sel: str) -> bool:
    return page.eval_on_selector(sel, "el => el.classList.contains('hidden')")


def run(base: str, *, errors: list, consoles: list) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def _on_console(m):
            # 网络层噪音（后端返回 4xx/5xx 时浏览器固定打这条）不算代码错误
            if m.type == "error" and "Failed to load resource" not in m.text:
                consoles.append(f"console.{m.type}: {m.text}")

        page.on("console", _on_console)
        page.on("dialog", lambda d: d.accept())
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(900)

        # ── 1. 侧栏配置面板控件齐全 ──
        missing = page.evaluate(
            "ids => ids.filter(id => !document.getElementById(id))", CFG_IDS)
        check(f"配置面板控件齐全（{len(CFG_IDS)} 个）", not missing, str(missing))

        # 值已由 /api/settings 回填（仓库路径用真实 mock 仓路径）
        check(f"仓库路径已回填",
              page.input_value("#cfg-repo-path").strip().endswith("repo"),
              repr(page.input_value("#cfg-repo-path")))
        check(f"ONES Base URL 已回填",
              page.input_value("#cfg-ones-url") == "https://ones.example.com",
              repr(page.input_value("#cfg-ones-url")))
        # 未保存凭据时：hint 空，占位文案由后端 has_* 决定（两版逐字一致）
        check(f"未保存密码时 hint 为空",
              page.inner_text("#cfg-ones-password-hint").strip() == "",
              repr(page.inner_text("#cfg-ones-password-hint")))
        check(f"密码占位=ONES 登录密码",
              page.get_attribute("#cfg-ones-password", "placeholder") == "ONES 登录密码",
              repr(page.get_attribute("#cfg-ones-password", "placeholder")))
        check(f"Token 占位=可粘贴浏览器里的 token",
              page.get_attribute("#cfg-ones-token", "placeholder") == "可粘贴浏览器里的 token",
              repr(page.get_attribute("#cfg-ones-token", "placeholder")))
        check(f"页面登录密码占位=被测应用的登录密码",
              page.get_attribute("#cfg-pagelogin-password", "placeholder") == "被测应用的登录密码",
              repr(page.get_attribute("#cfg-pagelogin-password", "placeholder")))

        # ── 2. 闸门预览（显式命令态） ──
        gp = page.inner_text("#gate-preview")
        check(f"闸门预览=显式命令态", "逐条真实执行" in gp, repr(gp[:80]))
        check(f"闸门预览列出 type-check",
              "npm run type-check" in gp, repr(gp[:120]))
        check(f"闸门预览显示 explicit 徽标",
              page.query_selector_all("#gate-preview .gate-badge") and
              "gate-explicit" in (page.get_attribute("#gate-preview .gate-badge", "class") or ""),
              page.get_attribute("#gate-preview .gate-badge", "class"))

        # ── 3. 大模型摘要卡 ──
        ais = page.inner_text("#ai-summary")
        check(f"摘要卡=Mock 模式", "Mock 模式" in ais, repr(ais[:60]))
        check(f"摘要卡显示模型名", "mock-model" in ais, repr(ais[:120]))
        check(f"摘要卡显示地址（短路径）", "example.com" in ais, repr(ais[:160]))

        # ── 4. 大模型弹窗开关 + 控件齐全 ──
        check(f"#aimodal 初始隐藏", hidden(page, "#aimodal"))
        page.click("#ai-summary-group .btn-ghost")
        page.wait_for_function(
            "() => { const m = document.querySelector('#aimodal');"
            " return m && !m.classList.contains('hidden') }", timeout=10000)
        missing = page.evaluate(
            "ids => ids.filter(id => !document.getElementById(id))", AI_IDS)
        check(f"大模型弹窗控件齐全（{len(AI_IDS)} 个）", not missing, str(missing))
        check(f"模型名已回填", page.input_value("#cfg-ai-model") == "mock-model",
              repr(page.input_value("#cfg-ai-model")))

        # 鉴权字段名：默认 bearer → 隐藏；切到 header → 显示
        # （原生 select 被自绘下拉接管，必须走 pick_select；两版通用）
        check(f"bearer 下鉴权字段名隐藏", hidden(page, "#ai-auth-header-field"))
        pick_select(page, "#cfg-ai-auth", "header")
        page.wait_for_timeout(300)
        check(f"header 下鉴权字段名显示", not hidden(page, "#ai-auth-header-field"))
        pick_select(page, "#cfg-ai-auth", "bearer")
        page.wait_for_timeout(300)

        # #ai-test-result 必须始终在 DOM（只切 hidden）
        check(f"#ai-test-result 在 DOM 但初始隐藏",
              page.query_selector("#ai-test-result") is not None
              and hidden(page, "#ai-test-result"))
        # 只校验配置（dry）：跑一次本地校验，结果面板应显形
        page.click("#btn-ai-dry")
        page.wait_for_function(
            "() => { const e = document.querySelector('#ai-test-result');"
            " return e && !e.classList.contains('hidden') }", timeout=30000)
        check(f"dry 校验后结果面板显形（hidden 已摘）",
              not hidden(page, "#ai-test-result"))
        # 关闭弹窗（点关闭按钮）
        page.click("#aimodal .close")
        page.wait_for_timeout(300)
        check(f"大模型弹窗可关闭", hidden(page, "#aimodal"))

        # ── 5. 更新日志弹窗 ──
        check(f"#chmodal 初始隐藏", hidden(page, "#chmodal"))
        page.click("#changelog-btn")
        page.wait_for_function(
            "() => { const m = document.querySelector('#chmodal');"
            " return m && !m.classList.contains('hidden') }", timeout=15000)
        check(f"更新日志弹窗打开", not hidden(page, "#chmodal"))
        # chips 与条目都是异步拉的，等第一条 chip 出现（不赌固定等待）
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('#ch-filters .ch-chip').length > 0",
                timeout=15000)
        except Exception:
            pass
        check(f"分类 chips 已渲染",
              len(page.query_selector_all("#ch-filters .ch-chip")) > 0,
              str(len(page.query_selector_all("#ch-filters .ch-chip"))))
        page.click("#chmodal .close")
        page.wait_for_timeout(300)
        check(f"更新日志弹窗可关闭", hidden(page, "#chmodal"))

        # ── 6. 知识卡片弹窗（#modal） ──
        page.evaluate("() => switchTab('defect')")
        # 切页签后等缺陷页签真的挂载完（Vue 版按需挂载，切进来才开始拉数据）
        page.wait_for_timeout(1200)
        # 切到缺陷卡片视图（旧版按钮是 data-kbview=defect，无独立 id）
        btn = page.query_selector(".seg-btn[data-kbview='defect']")
        if btn:
            btn.click()
            page.wait_for_timeout(700)
        # 必须是 #kb-panel 里的卡片：#proposal-panel 也用 .pcard/#cards 之外的类，
        # 但两版都可能把别的行拼进来，钉住容器才稳。
        card = page.query_selector("#kb-panel #cards .kb-card")
        if card:
            # 明确挑夹具那张（含 DEF-1001），避免并列渲染时点错
            for el in page.query_selector_all("#kb-panel #cards .kb-card"):
                if "DEF-1001" in (el.inner_text() or ""):
                    card = el
                    break
            card.scroll_into_view_if_needed()
            card.click()
            # 别等固定时间：等标题落到夹具卡片上（点击 → 拉 API → 渲染）
            page.wait_for_function(
                "() => (document.querySelector('#modal-title') || {}).textContent"
                " === '支付页金额显示为 0'", timeout=15000)
            check(f"知识卡片弹窗打开", not hidden(page, "#modal"))
            # 只断言「打开的是本夹具那张卡 + 正文是 markdown 渲染出来的」。
            # 别把标题钉死成中文——主题无关，真正要守的是 id/渲染器接线。
            _title = page.inner_text("#modal-title").strip()
            check(f"弹窗标题=夹具卡片的标题",
                  _title == "支付页金额显示为 0", repr(_title))
            _body = page.eval_on_selector("#modal-body", "el => el.innerHTML")
            check(f"弹窗有 meta 行",
                  len(page.query_selector_all("#modal-meta span")) > 0,
                  str(len(page.query_selector_all("#modal-meta span"))))
            check(f"正文渲染成 h1 + h2 标题",
                  "<h1>" in _body and "<h2>" in _body, _body[:120])
            check(f"弹窗正文渲染了加粗",
                  len(page.query_selector_all("#modal-body strong")) > 0,
                  f"strong={len(page.query_selector_all('#modal-body strong'))} "
                  f"body={_body[:160]!r}")
            page.click("#modal .close")
            page.wait_for_timeout(300)
            check(f"知识卡片弹窗可关闭", hidden(page, "#modal"))
        else:
            check(f"知识卡片弹窗打开", False,
                  f"没有 kb-card（#cards 内容：{page.inner_text('#cards')[:60]!r}）")

        # ── 7. 运行日志放大弹窗（#runmodal）：真实运行 → 点放大 ──
        #
        # `#btn-run-expand` 只在一次真实运行之后才显形，所以走产品自己的真实路径：
        # 点 #btn-run（Vue 版走 /api/run/stream）。离线环境没有 ONES，流式接口不带
        # defects 时返回 count:0、结果区空 → 放大按钮永不显形。这里包一层 window.fetch，
        # 只给 /api/run/stream 的 POST 注入一条 mock 工单，让流水线真的产出结果。
        # （等价于 check_vue_defect_ui.py 的做法，同样不伪造 DOM、不调 __set* 测试钩子。）
        page.evaluate("""() => {
          const orig = window.fetch;
          window.fetch = function (input, init) {
            const url = String(input);
            if (url.split('?')[0].endsWith('/api/run/stream')
                && init && init.method === 'POST' && typeof init.body === 'string') {
              const body = JSON.parse(init.body || '{}');
              body.defects = [{
                id: 'DEF-UI-1', title: '列表页金额显示为 0',
                description: '复现：打开列表页，金额列全部显示 0。\\n期望：显示真实金额。',
                priority: 'P1', category: '逻辑',
              }];
              init = Object.assign({}, init, { body: JSON.stringify(body) });
            }
            return orig.call(this, input, init);
          };
        }""")
        page.click("#btn-run")
        page.wait_for_function(
            "() => { const b = document.querySelector('#btn-run-expand');"
            " return b && !b.classList.contains('hidden') }", timeout=90000)
        check(f"注入工单跑通流水线", True)
        check(f"结果渲染后放大按钮可见",
              not hidden(page, "#btn-run-expand"))
        page.click("#btn-run-expand")
        page.wait_for_function(
            "() => { const m = document.querySelector('#runmodal');"
            " return m && !m.classList.contains('hidden') }", timeout=10000)
        check(f"运行结果弹窗打开", not hidden(page, "#runmodal"))
        check(f"弹窗标题=运行结果",
              page.inner_text("#runmodal-title").strip() == "运行结果",
              repr(page.inner_text("#runmodal-title")))
        check(f"弹窗正文有内容",
              page.eval_on_selector("#runmodal-body", "el => el.innerHTML.length") > 100,
              str(page.eval_on_selector("#runmodal-body", "el => el.innerHTML.length")))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check(f"Esc 关闭运行结果弹窗", hidden(page, "#runmodal"))

        # ── 8. 提案详情弹窗（#pmodal）：点提案卡 ──
        check(f"#pmodal 初始隐藏", hidden(page, "#pmodal"))
        # 提案列表刷新走真实路径：开一次更新日志再关（内容用 markdown 渲染，
        # 与第 6 步共用 #modal —— 这正是两版共有的复用方式）；切走再切回触发 ensure。
        page.click("#changelog-btn")
        page.wait_for_function(
            "() => { const m = document.querySelector('#chmodal');"
            " return m && !m.classList.contains('hidden') }", timeout=15000)
        page.click("#chmodal .close")
        page.wait_for_timeout(300)
        page.evaluate("() => switchTab('stats')")
        page.wait_for_timeout(500)
        page.evaluate("() => switchTab('defect')")
        page.wait_for_timeout(1500)
        # 提案卡：两版都是 pcard，点了都直接开 #pmodal（旧版 openProposal(id)）
        page.wait_for_function(
            "() => document.querySelectorAll('#proposals .pcard').length > 0", timeout=30000)
        check(f"提案列表已刷新",
              len(page.query_selector_all("#proposals .pcard")) > 0)
        page.click("#proposals .pcard")
        page.wait_for_function(
            "() => { const m = document.querySelector('#pmodal');"
            " return m && !m.classList.contains('hidden') }", timeout=15000)
        check(f"提案详情弹窗打开", not hidden(page, "#pmodal"))
        check(f"弹窗标题非空",
              len(page.inner_text("#pmodal-title").strip()) > 0,
              repr(page.inner_text("#pmodal-title")))
        check(f"7 个分区容器都在 DOM",
              not page.evaluate("""() => ['pm-analysis','pm-diff','pm-blocks','pm-gate',
                'pm-issues','pm-shots','pm-console'].filter(id => !document.getElementById(id))"""))
        check(f"采纳/拒绝/关闭按钮在 DOM",
              not page.evaluate("""() => ['btn-approve','btn-force','btn-reject','btn-undo']
                .filter(id => !document.getElementById(id))"""))
        check(f"决策备注框可输入",
              page.query_selector("#pm-note") is not None)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check(f"Esc 关闭提案详情弹窗", hidden(page, "#pmodal"))

        # ── 9. 没有把 undefined / NaN 渲染出来 ──
        for sel in ("#settings-panel", "#aimodal"):
            txt = page.inner_text(sel)
            bad = [b for b in ("undefined", "NaN", "[object Object]") if b in txt]
            check(f"{sel} 无 undefined/NaN", not bad, str(bad))

        browser.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="vue_settings_"))
    proc, base, _dirs = start_server(tmp)
    errors, consoles = [], []
    try:
        print(f"唯一实现（GET /，Vue 3）：")
        run(base, errors=errors, consoles=consoles)
        print()
    finally:
        proc.kill()

    print(f"合计 {len(OK)} 项通过 / {len(BAD)} 项失败")
    if errors:
        print("\npageerror:")
        for e in errors:
            print("  " + e)
    if consoles:
        print("\nconsole.error:")
        for c in consoles:
            print("  " + c)
    if BAD:
        print("\nFAIL:")
        for b in BAD:
            print("  " + b)
    ok = not BAD and not errors and not consoles
    print("PASS: 配置侧栏 + 大模型 / 知识卡片 / 更新日志弹窗（唯一实现 GET /，Vue 3）" if ok
          else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
