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

# 手搓的合法 PDF（与 tests/check_req_attachments.py 共用一份实现）：
# 附件上传要覆盖「PDF 在上传时被抽成正文」这条路径，造一个空白 PDF 测不到。
from check_req_attachments import make_pdf          # noqa: E402

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


# 一个页面里四个任务页签的关键控件（id 与旧版一致；req 是 2026-10-19 新增）
PANES = {
    # 需求开发（新建）：需求原文 → 四阶段流水线（分诊/定位/改动/自检）
    "req": ["pane-req", "rq-doc", "rq-deliver", "rq-source", "rq-scope", "rq-stack",
            "rq-context", "rq-notes", "btn-req", "rq-doc-echo", "rq-stages",
            "rq-verdict", "rq-type", "rq-design", "rq-files", "rq-check",
            "btn-rq-att", "rq-att-input"],
    # Figma 转代码（原「需求开发」改名，内部 name 仍是 reqdev）
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
            # 参数轨把 advanced 组默认折叠（新契约）：先展开再数控件，
            # 否则「齐全」这条锁的其实是折叠前那份旧版全展开形态。
            adv = page.query_selector(f"#btn-{tab}-params-advanced")
            if adv:
                adv.click()
                page.wait_for_timeout(200)
            missing = page.evaluate("""ids => ids.filter(id => !document.getElementById(id))""", ids)
            check(f"{tab} 页控件齐全（{len(ids)} 个）", not missing, str(missing))

        # ── 2. #figma-tree / #test-preview / #rq-doc-echo 空态占位（:empty 契约）──
        for pid in ("figma-tree", "test-preview", "rq-doc-echo"):
            ph = page.eval_on_selector(f"#{pid}", "el => getComputedStyle(el, '::before').content")
            check(f"#{pid} 空态占位提示存在",
                  bool(ph) and ph not in ("none", "normal"), str(ph)[:50])
            kids = page.eval_on_selector(f"#{pid}", "el => el.children.length")
            check(f"#{pid} 空态无子节点（:empty 生效）", kids == 0, f"{kids} 子节点")

        # ── 3. Figma 转代码：取稿通道切换落 localStorage ──
        page.evaluate("() => switchTab('reqdev')")
        page.wait_for_timeout(300)
        # 参数形态改版（2026-10-13）：reqdev 七条参数含长文本 → **常驻参数轨**。
        # 轨是这一屏 grid 的真实第二列，不再是要点开、还会让主列让位的浮层抽屉，
        # 所以「先点修改参数」这一步消失；反过来锁住新契约。
        check("参数轨常驻（无抽屉）",
              page.is_visible("#param-rail-reqdev")
              and not page.query_selector("#btn-reqdev-params"))
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
        # 参数形态改版（2026-10-13）：codetest 三条参数走**就地控件条**，
        # 不再有抽屉，也就不存在「先点修改参数再填」这一步 —— 控件常驻即可填。
        # 这里反过来锁住新契约：参数条与三个控件在首屏就必须是可见的。
        check("参数条常驻（无抽屉）",
              page.eval_on_selector("#param-strip-codetest",
                                    "el => !!el && !!el.offsetParent")
              and not page.query_selector("#btn-codetest-params"))
        check("参数条里的控件可见",
              page.is_visible("#test-file") and page.is_visible("#test-framework")
              and page.is_visible("#test-focus"))
        page.fill("#test-file", "packages/src/Hello.tsx")
        page.click("#btn-test-load")
        page.wait_for_timeout(1500)
        preview = page.inner_text("#test-preview")
        check("预览文件读到内容", "Hello" in preview, repr(preview[:80]))

        # ── 5. 需求开发：四阶段流水线 + 可采纳改动 + 自检 + 交接 ──
        # 这一节锁的是 2026-10-19 的完整链路：需求原文 → 分诊 → 定位 → 改动 → 自检，
        # 以及「需要设计稿时仍把非视觉部分先落地」——只做分诊是这一版要修掉的毛病。
        page.evaluate("() => switchTab('req')")
        page.wait_for_timeout(300)

        # ── 4.5 需求文档附件：md / pdf 上传后当场解析、可核对、可移除 ──
        # 需求文档在真实工作里多半是文件而不是可粘贴的文字。这一节锁三件事：
        # ① PDF 在**上传时**就被抽成正文（不是等跑任务才失败）；
        # ② 用户能看到「AI 读到了什么」（解析预览），而不是只能相信；
        # ③ 移除后运行不再引用它。
        src = Path(tempfile.mkdtemp(prefix="req-att-src-"))
        att_md = src / "需求说明.md"
        att_md.write_text("# 订单筛选\n\n列表页新增按交付日期筛选。\n", encoding="utf-8")
        att_pdf = src / "prd.pdf"
        att_pdf.write_bytes(make_pdf("Order list: add delivery date filter"))
        page.set_input_files("#rq-att-input", [str(att_md), str(att_pdf)])
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('#rq-atts .rq-att').length === 2",
                timeout=30000)
        except Exception:
            check("上传 md + pdf 后出现两条附件", False, page.inner_text("#rq-atts")[:160])
        atts_text = page.inner_text("#rq-atts")
        check("附件清单显示文件名", "需求说明.md" in atts_text and "prd.pdf" in atts_text,
              repr(atts_text[:160]))
        check("PDF 显示了抽出的字符数", "抽出" in atts_text, repr(atts_text[:160]))
        check("PDF 与 md 的类型徽标不同",
              page.eval_on_selector_all("#rq-atts .rq-att-kind",
                                        "els => els.map(e => e.textContent.trim())")
              == ["MD", "PDF"],
              str(page.eval_on_selector_all("#rq-atts .rq-att-kind",
                                            "els => els.map(e => e.textContent.trim())")))
        # 点 PDF 的「看解析结果」：预览里必须是 PDF 里的原话
        page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#rq-atts .rq-att')];
            const row = rows.find(r => r.textContent.includes('prd.pdf'));
            row.querySelector('.rq-att-view').click();
        }""")
        page.wait_for_timeout(150)
        preview = page.inner_text("#rq-att-preview")
        check("解析预览里是 PDF 的真实正文", "add delivery date filter" in preview, repr(preview[:120]))
        # 移除 md：只剩 PDF，且运行时不带已移除的那份
        page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#rq-atts .rq-att')];
            const row = rows.find(r => r.textContent.includes('需求说明.md'));
            row.querySelector('.rq-att-del').click();
        }""")
        page.wait_for_timeout(150)
        check("移除一份后只剩 1 条",
              page.eval_on_selector_all("#rq-atts .rq-att", "els => els.length") == 1,
              str(page.eval_on_selector_all("#rq-atts .rq-att", "els => els.length")))

        page.fill("#rq-doc", "订单列表页新增「按交付日期筛选」，并支持导出当前筛选结果。")
        # 附件是独立输入：运行时以 id 引用，正文由服务端拼进需求（PDF 已在上传时抽好）。
        # 断言请求体里只剩未移除的那一份，锁住「移除即生效」。
        try:
            with page.expect_request("**/api/tasks/run") as req_info:
                page.click("#btn-req")
            sent = json.loads((req_info.value.post_data or "") or "{}")
            check("运行时带上了未移除的附件",
                  len(sent.get("attachment_ids") or []) == 1,
                  str(sent.get("attachment_ids")))
        except Exception as e:
            check("运行时带上了未移除的附件", False, f"没抓到请求: {e}")
        try:
            page.wait_for_function(
                "() => !document.getElementById('rq-verdict').classList.contains('hidden')",
                timeout=60000)
        except Exception:
            check("分诊结论卡在 60s 内出现", False, "仍是空态")
        check("分诊结论：变更类型",
              "新增页面" in page.inner_text("#rq-type"), repr(page.inner_text("#rq-type")))
        check("分诊结论：需要设计稿",
              "需要设计稿" in page.inner_text("#rq-design"), repr(page.inner_text("#rq-design")))
        check("分诊结论：给了影响面",
              page.eval_on_selector_all("#rq-impacts .rq-impact", "els => els.length") > 0)
        check("阶段条有四个阶段",
              page.eval_on_selector_all("#rq-stages .rq-stage-step", "els => els.length") == 4)
        check("四个阶段都跑完（不是只做分诊）",
              page.eval_on_selector_all("#rq-stages .is-done", "els => els.length") == 4,
              str(page.eval_on_selector_all("#rq-stages .rq-stage-step",
                                            "els => els.map(e => e.dataset.stage + ':' + e.className)")))
        check("改动清单里有可采纳文件",
              page.eval_on_selector_all("#rq-files .rq-file", "els => els.length") >= 1,
              page.inner_text("#rq-files")[:120])
        check("改动清单显示了真实路径",
              "src/api/order.ts" in page.inner_text("#rq-files"),
              page.inner_text("#rq-files")[:120])
        check("自检结论已展示",
              "部分完成" in page.inner_text("#rq-check"), page.inner_text("#rq-check")[:120])
        check("「审阅改动并采纳」可点",
              page.eval_on_selector("#btn-rq-artifact", "el => !el.disabled"))
        page.click("#btn-goto-reqdev")
        page.wait_for_timeout(400)
        check("交接后切到 Figma 转代码页",
              page.eval_on_selector("#pane-reqdev", "el => el.classList.contains('active')"))
        check("需求原文已预填到生成参数的需求描述",
              "交付日期" in (page.input_value("#req-desc") or ""),
              repr(page.input_value("#req-desc")))

        # ── 6. 跑一次 Mock 任务：接口联调（表单最简，只要求粘贴文档）──
        page.evaluate("() => switchTab('apidebug')")
        page.wait_for_timeout(300)
        # 参数形态改版：apidebug 的接口文档是 rows=10 长文本 → 走常驻参数轨，
        # 直接填即可（原先「先点修改参数开抽屉」那一步随抽屉一起消失）。
        check("参数轨常驻（无抽屉）",
              page.is_visible("#param-rail-apidebug")
              and not page.query_selector("#btn-apidebug-params"))
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
        # 弹窗宽度：以前没传 width，走 Element 默认的 50%（1600 视口只有 800px，
        # diff 看不全），另外四个弹窗都是 `min(Npx, 9xvw)`。这里锁住口径。
        dlg = page.evaluate("""() => {
          const d = document.querySelector('#amodal .el-dialog');
          return d ? {w: Math.round(d.getBoundingClientRect().width), vw: innerWidth} : null;
        }""")
        check("产出物详情弹窗按 min(1000px,96vw) 撑开（不再是默认 50%）",
              dlg is not None and dlg["w"] >= min(1000, dlg["vw"] * 0.96) - 2, str(dlg))
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
