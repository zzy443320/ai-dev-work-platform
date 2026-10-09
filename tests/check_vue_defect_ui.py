# -*- coding: utf-8 -*-
"""缺陷修复页签：对唯一实现（`GET /`，Vue 3）跑一遍断言。

迁移期这里是「旧版 `/` 与新版 `/v2` 各跑一遍，断言两版一致」；前端收口后
`/` 已直接返回 Vue 产物、`/v2` 已删除，迁移期的 debugBridge（把 Vue 内部状态
挂到 window 上模仿旧版全局函数名）也一并删掉了，所以现在只剩一条真实实现可测。

这个页签是四个面板叠在一起（运行流水线 / 待审批提案 / 分类统计 / 知识库），
所以断言从既有用例里搬，不另发明口径：

  - 面板顺序 + 折叠 + 状态记忆          ← tests/check_panel_fold.py
  - 步骤条与真实结果联动                ← 原 check_stages_ui.py（第 4b 步，
    收口时补的回归：stageStates 曾被算出来却没接到 #stage-flow 上）
  - 提案分页（每页 8 条 / 翻页 / 筛选重置）← tests/check_pager.py
  - 知识库双层视图 + 开关持久化          ← tests/check_kb_patterns_ui.py（视图部分）
  - 日志区 420px 恒定高中 + 空态占位提示 ← tests/check_live_ui.py（布局部分）
  - 运行/试运行结果渲染 + 放大按钮可见性  ← tests/check_expand_modal.py（渲染部分）

上面箭头右列那四个文件（check_pager / check_kb_patterns_ui / check_live_ui /
check_expand_modal）已于 2026-10-09 收掉——它们是 Vue 收口前的原生界面用例，
直调 `proposalsCache` / `liveSetup` / `renderRunResults` 这类已不存在的 window
全局，一跑就 ReferenceError，属于"名字看起来还像在守什么、实际没人跑"的死壳。
承接关系就是本文件这几行，别再去找那些文件；完整备查清单见 check_vue_all.py 顶部。

刻意**不**验证的部分：`liveSetup/liveLine/liveAI/liveRefs` 这类旧版命令式内部函数、
`stagesRunning/stagesFromRunResults/stagesFromProbeResults` 这几个纯函数的**直调**结果
（旧版靠 debugBridge 挂到 window 才能直调，收口后是 composable 内部实现），
以及 `proposalsCache/propPage/renderProposals` 这类全局变量 —— 它们在新版里是
composable 的内部状态，没有一一对应的名字。那部分由「行为等价」的断言覆盖
（同样的点击、同样的输入，产出同样的 DOM）。

数据准备：分页要 30 条提案才测得出「每页 8 条 / 第 4 页 6 条」，这里直接往
临时 `PROPOSAL_DIR`（`dirs["proposals"]`）写与后端落盘同构的提案 JSON ——
字段走 `ProposalStore.summary()` 的读取路径，`created` 递增保证时间倒序可控。

用法：.venv\\Scripts\\python.exe tests/check_vue_defect_ui.py
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
    dirs = {k: tmp / k for k in
            ("proposals", "artifacts", "team_runs", "chat_sessions", "attachments",
             "screenshots", "usage", "knowledge_base", "repo")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    # 用夹具知识库（脱敏），保证模式视图有卡可断言
    import kb_fixture
    kb_fixture.build(dirs["knowledge_base"])
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


# 步骤条纯函数的判定用例：迁移期靠 debugBridge 把 `stagesRunning` /
# `stagesFromRunResults` / `stagesFromProbeResults` 这几个纯函数挂到 window 上，
# 才能对两版同一段断言。收口时 debugBridge 已删除，Vue 版把它们收进了
# composable 内部（不再暴露到 window），所以这一组「纯函数直调」断言整体删掉。
# 它们其实是 `start()` 结束后的状态推导，已由第 5 步的真实流水线断言
# （注入工单 → 点 #btn-run → 结果行 / 放大按钮）在行为层覆盖。


# 提案分页的数据：直接往临时 PROPOSAL_DIR 写与后端落盘同构的提案 JSON。
# 注意 `created` 必须递增 —— 前端列表按时间倒序排，第 1 页要正好是 DEF-30..DEF-23。
SEED_COUNT = 30


def seed_proposals(dirs, n: int = SEED_COUNT) -> None:
    for k in range(1, n + 1):
        rec = {
            "id": f"X{k}",
            "created": f"2026-09-22T10:00:{k:02d}",
            "updated": f"2026-09-22T10:00:{k % 60:02d}",
            "status": "pending",
            "defect": {"id": f"DEF-{k}", "title": f"测试提案 {k}",
                       "priority": f"P{k % 3}"},
            "analysis": {"category": "逻辑", "ai_mode": "openai",
                         "root_cause": f"第 {k} 条测试根因"},
            "patch": {"changes": [{"file_path": "a.vue", "applied_blocks": 1,
                                   "stats": {"added": 1, "removed": 0}}]},
            "gate": {"level": "degraded", "ok": True},
        }
        (dirs["proposals"] / f"X{k}.json").write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def api_list(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def run(base: str, dirs, *, errors: list) -> None:
    """跑一遍缺陷修复页签断言（页面是唯一实现：`GET /`）。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("dialog", lambda d: d.accept())
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(900)
        # 后段（分页 / 知识库）要在「删掉运行产物」之后仍有确定数据，
        # 这里先包一层 fetch：显式打开 `__propListStub` 后由内存列表供应提案，
        # 别的一律透传。默认关闭，前面几步走的还是真后端。
        page.evaluate("""() => {
          const orig = window.fetch;
          window.__propListStub = null;
          window.fetch = function (input, init) {
            const url = String(input);
            if (window.__propListStub && url.split('?')[0].endsWith('/api/proposals')
                && (!init || !init.method || init.method === 'GET')) {
              return Promise.resolve(new Response(window.__propListStub, {
                status: 200, headers: { 'Content-Type': 'application/json' } }));
            }
            return orig.apply(this, arguments);
          };
        }""")

        # ── 页签存在且能切过去 ──
        pane = page.query_selector("#pane-defect")
        check("#pane-defect 存在", pane is not None)
        page.evaluate("() => switchTab('defect')")
        page.wait_for_timeout(400)
        check("切到缺陷修复页后 .active",
              page.eval_on_selector("#pane-defect", "el => el.classList.contains('active')"))

        # ── 1. 面板顺序（check_panel_fold.py 契约）──
        order = ["run-panel", "proposal-panel", "stats-panel", "kb-panel"]
        tops = page.evaluate("""ids => ids.map(id => {
          const el = document.getElementById(id);
          return el ? el.getBoundingClientRect().top : null;
        })""", order)
        check("四个面板齐全且在页面里",
              all(t is not None for t in tops), str(tops))
        check("面板顺序：运行 → 提案 → 统计 → 知识库",
              tops == sorted(tops), str([round(t) for t in tops]))

        # 相邻面板垂直间隙 >=12px（.tabpane.active 的 grid gap）
        gap = page.evaluate("""() => {
          const ps = [...document.querySelectorAll('#pane-defect > .panel')]
            .filter(el => !el.classList.contains('hidden'));
          const out = [];
          for (let i = 1; i < ps.length; i++) {
            out.push(ps[i].getBoundingClientRect().top - ps[i-1].getBoundingClientRect().bottom);
          }
          return out;
        }""")
        check("面板间距 >= 12px", bool(gap) and min(gap) >= 12,
              str([round(g) for g in gap]))
        check("#pane-defect 是 grid 布局",
              page.eval_on_selector("#pane-defect", "el => getComputedStyle(el).display") == "grid")

        # ── 2. 折叠 + 状态记忆（check_panel_fold.py 契约）──
        page.evaluate("() => localStorage.removeItem('panel-fold-state')")
        page.wait_for_timeout(150)
        h_before = page.eval_on_selector("#proposal-panel", "el => el.offsetHeight")
        page.click("#proposal-panel .fold-btn")
        page.wait_for_timeout(200)
        collapsed = page.eval_on_selector("#proposal-panel",
                                          "el => el.classList.contains('collapsed')")
        h_after = page.eval_on_selector("#proposal-panel", "el => el.offsetHeight")
        check("折叠后带 collapsed 类且变矮",
              collapsed and h_after < h_before, f"{h_before}->{h_after}")
        saved = page.evaluate("() => localStorage.getItem('panel-fold-state')")
        check("折叠状态写 panel-fold-state",
              saved and '"proposal-panel":true' in saved.replace(" ", ""), str(saved))
        page.reload(wait_until="networkidle")
        page.evaluate("() => switchTab('defect')")
        page.wait_for_timeout(400)
        check("刷新后折叠状态保持",
              page.eval_on_selector("#proposal-panel", "el => el.classList.contains('collapsed')"))
        page.click("#proposal-panel .fold-btn")
        page.wait_for_timeout(200)
        check("二次点击展开",
              page.eval_on_selector("#proposal-panel", "el => !el.classList.contains('collapsed')"))
        page.evaluate("() => localStorage.removeItem('panel-fold-state')")

        # ── 3. 日志区布局（check_live_ui.py 契约）──
        mh = page.eval_on_selector("#run-log", "el => getComputedStyle(el).maxHeight")
        check("#run-log max-height=420px", mh == "420px", mh)
        init_h = page.eval_on_selector("#run-log", "el => el.clientHeight")
        check("空态高度 ≈420px（不跳动）", 415 <= init_h <= 425, str(init_h))
        ph = page.eval_on_selector("#run-log",
                                   "el => getComputedStyle(el, '::before').content")
        check("空态占位提示存在", bool(ph) and ph not in ("none", "normal"),
              str(ph)[:50])
        check("空态放大按钮隐藏",
              page.eval_on_selector("#btn-run-expand", "el => el.classList.contains('hidden')"))

        # ── 4. 结构化结果渲染 + 放大按钮（check_expand_modal.py 契约）──
        # 走真实路径：点 #btn-run（前端自己发 POST /api/run/stream），流水线跑完
        # 后 `#run-log` 被渲染成结构化结果，放大按钮随之显形。
        #
        # 关键：离线环境下 ONES 拉不到工单（/api/run/stream 会返回 count=0、
        # results=[]，实测如此），流水线就没东西可渲染。解法是给这次请求补上
        # `defects` —— 后端 `_run_opts` 对注入工单的优先级高于拉取（见
        # web/server.py 的 `defects` 注释），所以页面里包一层 fetch，只给
        # `/api/run/stream` 的这一次 POST 塞进注入工单，其余请求一律透传。
        # 这样跑的仍是前端真实的「点运行 → 消费 SSE → 渲染结果」链路，
        # 不是往 DOM 里硬塞 HTML。
        page.evaluate("""() => {
          const orig = window.fetch;
          window.fetch = function (input, init) {
            const url = String(input);
            if (url.split('?')[0].endsWith('/api/run/stream')
                && init && init.method === 'POST' && typeof init.body === 'string') {
              const body = JSON.parse(init.body || '{}');
              body.defects = [{
                id: 'DEMO-UI-1', title: '提交前没有校验必填项',
                description: '复现：不填必填项直接提交。\\n期望：给出拦截提示。',
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
        check("结果渲染后放大按钮可见",
              page.eval_on_selector("#btn-run-expand",
                                    "el => !el.classList.contains('hidden')"))
        # 结构化结果块：mock 流水线对这条工单给不出 SEARCH/REPLACE，结果是
        # invalid → 1 行结果（工单号 + 「无法生成补丁」状态）
        log_html = page.eval_on_selector("#run-log .log-html", "el => el.innerText")
        body_len = page.eval_on_selector("#run-log",
                                         "el => el.querySelectorAll('.run-row').length")
        check(".log-html 里渲染出结果行（含工单号与状态）",
              body_len == 1 and "DEMO-UI-1" in log_html and "无法生成补丁" in log_html,
              f"rows={body_len} {log_html[:90]}")

        # ── 4b. 步骤条与真实结果联动（原 check_stages_ui.py 的 DOM 契约；纯函数分支判定在 frontend/tests/pure.test.mjs）──
        # 这是收口时补上的一处真回归：`#stage-flow` 在迁移期被写成静态标记，
        # useDefect 里全套 stageStates 状态机（stagesRunning / stagesFromRunResults /
        # stagesFromProbeResults）算出来了却没有接到 DOM 上，5 步永远不带
        # active/done/warn/fail/off，style.css 里 .stage.done/.fail 等样式形同虚设。
        # 现在 App.vue 用 :class="stageStates[i]" 绑上，断言下面「跑完一轮后不再是
        # 让 5 步都停在 idle」来守住接线。
        page.evaluate("() => switchTab('defect')")
        page.wait_for_timeout(200)
        stage_classes = page.eval_on_selector_all(
            "#stage-flow .stage", "els => els.map(e => e.className)")
        check("步骤条确实由 stageStates 驱动（跑完一轮后不再全是 idle）",
              any("done" in c or "warn" in c or "fail" in c for c in stage_classes),
              str(stage_classes))
        check("步骤条第 1 步「拉取」为 done（工单确实拉到了）",
              "done" in (stage_classes[0] if stage_classes else ""),
              str(stage_classes[:1]))
        # 5 个 .stage 都在且只有 5 个（防止绑定写歪成多渲染/少渲染）
        check("步骤条恰好 5 步", len(stage_classes) == 5, str(len(stage_classes)))

        # 放大弹窗：真实结果点开后正文有内容
        page.click("#btn-run-expand")
        page.wait_for_function(
            "() => { const m = document.querySelector('#runmodal');"
            " return m && !m.classList.contains('hidden') }", timeout=10000)
        check("运行结果弹窗打开",
              page.eval_on_selector("#runmodal", "el => !el.classList.contains('hidden')"))
        check("弹窗正文有内容",
              page.eval_on_selector("#runmodal-body", "el => el.innerHTML.length") > 100,
              str(page.eval_on_selector("#runmodal-body", "el => el.innerHTML.length")))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ── 5. 提案分页（check_pager.py 契约，灌 30 条真提案）──
        # 第 4 步跑出的真提案会占掉第 1 页一个位置，所以先把它从盘上删掉。
        # 运行产物从盘上按 `DEMO-*` / `DEF-UI-*` 认（`/api/run` 的响应没有顶层
        # proposal_id，只在 results[] 里；这里不依赖它，直接扫盘更稳）。
        for f in sorted(dirs["proposals"].glob("*.json")):
            if f.stem.startswith(("DEF-UI-", "DEMO-UI-", "DEMO-")):
                f.unlink(missing_ok=True)
        run_kb_path = None
        for md in dirs["knowledge_base"].rglob("*.md"):
            if "_patterns" in str(md):
                continue
            if "DEMO-UI-1" in md.name:
                run_kb_path = md
        # 这一组断言全程要求「恰好 30 条」，所以此时**不能**多放占位提案。
        seed_proposals(dirs, 30)
        page.wait_for_timeout(200)
        page.evaluate("s => { window.__propListStub = s; }",
                      json.dumps(api_list(base, "/api/proposals"), ensure_ascii=False))
        # 清掉运行产出的知识卡（让第 7 步「模式视图」只对着夹具知识库断言）。
        # **只删这一张** ——夹具自己的缺陷卡还要用来断言「缺陷卡片渲染出来」，
        # 无差别 rglob 会把它们一起删掉，第 7 步必然为 0。
        # `_patterns/` 是派生文件，留着即可；漏删这张会残留一条「其他」模式，
        # 把「已复发」计数冲掉。
        if run_kb_path is not None and run_kb_path.is_file():
            run_kb_path.unlink(missing_ok=True)
        page.evaluate("() => switchTab('stats')")
        page.wait_for_timeout(400)
        page.evaluate("() => switchTab('defect')")
        page.wait_for_function(
            "() => document.querySelectorAll('#proposals .pcard').length === 8"
            " && document.querySelector('.prop-pager .pg-info')",
            timeout=30000)
        cards = page.eval_on_selector_all("#proposals .pcard", "els => els.length")
        check("每页 8 条", cards == 8, str(cards))
        info = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
        check("第 1 页信息含 1-8 与 30", "1-8" in info and "30" in info, info)
        # el-pager 的页码项 textContent 带模板空白（" 1 "），比较前 strip
        active = page.eval_on_selector(
            ".prop-pager .el-pager li.is-active", "el => el.textContent").strip()
        check("高亮页码 = 1", active == "1", active)
        ids_p1 = page.eval_on_selector_all("#proposals .pcard .pcard-id",
                                           "els => els.map(e => e.textContent)")
        check("同状态注入数据严格按时间倒序",
              ids_p1 == [f"DEF-{i}" for i in range(30, 22, -1)], str(ids_p1[:3]))
        page.click(".prop-pager .el-pagination .btn-next")
        page.wait_for_timeout(250)
        info2 = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
        ids_p2 = page.eval_on_selector_all("#proposals .pcard .pcard-id",
                                           "els => els.map(e => e.textContent)")
        check("翻到第 2 页（9-16，无重复）",
              len(ids_p2) == 8 and "9-16" in info2 and not (set(ids_p1) & set(ids_p2)), info2)
        # 末页：连点「下一页」到最后一页
        page.click(".prop-pager .el-pagination .btn-next")
        page.wait_for_timeout(200)
        page.click(".prop-pager .el-pagination .btn-next")
        page.wait_for_timeout(250)
        ids_p4 = page.eval_on_selector_all("#proposals .pcard .pcard-id", "els => els.length")
        info3 = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
        nxt_dis = page.eval_on_selector(".prop-pager .el-pagination .btn-next", "el => el.disabled")
        prv_en = page.eval_on_selector(".prop-pager .el-pagination .btn-prev", "el => !el.disabled")
        check("第 4 页 6 条 / 25-30 / 下一页禁用",
              ids_p4 == 6 and "25-30" in info3 and nxt_dis and prv_en, info3)
        # 筛选重置到第 1 页：先退到第 3 页，再切筛选（#proposal-filter 已是 el-select）
        page.click(".prop-pager .el-pagination .btn-prev")
        page.wait_for_timeout(200)
        pick_select(page, "#proposal-filter", "applied")
        page.wait_for_timeout(250)
        check("切筛选回第 1 页且给空提示",
              page.query_selector("#proposals .empty") is not None,
              "空态存在")
        pick_select(page, "#proposal-filter", "")
        page.wait_for_timeout(250)
        active2 = page.eval_on_selector(
            ".prop-pager .el-pager li.is-active", "el => el.textContent").strip()
        info4 = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
        check("切回全部后停在第 1 页", active2 == "1" and "1-8" in info4, info4)

        # ── 6. 分类统计（有数据就渲染出卡）──
        stats_html = page.inner_text("#stats")
        check("统计面板有口径文字或空态",
              bool(stats_html.strip()), stats_html[:40].replace("\n", " "))

        # ── 7. 知识库双层视图（check_kb_patterns_ui.py 的视图部分）──
        page.click("#kb-views .seg-btn[data-kbview='pattern']")
        page.wait_for_timeout(800)
        count_text = page.inner_text("#card-count")
        check("模式视图数量文案含「模式」", "模式" in count_text, count_text)
        pats = page.query_selector_all("#cards .kb-card.pattern")
        check("模式卡渲染出来", len(pats) > 0, str(len(pats)))
        check("复发≥2 的卡带 hot 标记",
              len(page.query_selector_all("#cards .kb-card.pattern.hot")) > 0)
        check("模式卡带「复发」徽标",
              pats and "复发" in pats[0].inner_text(), pats[0].inner_text()[:50] if pats else "")
        hint1 = page.inner_text("#kb-hint")
        check("模式视图提示文案一致",
              hint1 == "同类缺陷的共性沉淀：复发次数越高，越说明该处缺护栏", hint1[:40])
        check("kb-view 落 localStorage = pattern",
              page.evaluate("() => localStorage.getItem('kb-view')") == "pattern")
        # 切缺陷卡片视图
        page.click("#kb-views .seg-btn[data-kbview='defect']")
        page.wait_for_timeout(800)
        check("缺陷视图数量文案含「张卡片」",
              "张卡片" in page.inner_text("#card-count"), page.inner_text("#card-count"))
        cards2 = page.query_selector_all("#cards .kb-card")
        check("缺陷卡片渲染出来", len(cards2) > 0, str(len(cards2)))
        hint2 = page.inner_text("#kb-hint")
        check("缺陷视图提示文案一致",
              hint2 == "一个工单一张卡：现象、根因、补丁与验证结果", hint2[:40])
        check("切视图按钮高亮跟随",
              page.eval_on_selector("#kb-views .seg-btn.active", "el => el.dataset.kbview") == "defect")
        # 刷新后保持
        page.reload(wait_until="networkidle")
        page.evaluate("() => switchTab('defect')")
        page.wait_for_timeout(900)
        check("刷新后 kb-view 保持为缺陷卡片",
              "张卡片" in page.inner_text("#card-count"), page.inner_text("#card-count"))

        # ── 8. 提案角标（有开放项时高亮）──
        # 角标只在列表非空时才摘掉 hidden（空列表会藏起来），而上面的分页断言
        # 要求「恰好 30 条 pending」、筛 applied 必须是空态，所以占位提案放到
        # 这里才补进来 —— 在此之前不能多，否则那几个断言会连锁失败。
        (dirs["proposals"] / "ZZ-badge.json").write_text(json.dumps({
            "id": "ZZ-badge",
            "created": "2026-09-01T09:00:00",
            "updated": "2026-09-01T09:00:00",
            "status": "applied",
            "defect": {"id": "DEF-BADGE", "title": "角标占位提案", "priority": "P2"},
            "analysis": {"category": "逻辑", "ai_mode": "openai",
                         "root_cause": "仅用于让角标可见"},
            "patch": {"changes": []},
            "gate": {"level": "degraded", "ok": True},
            "apply": {"sha": "deadbeef"},
        }, ensure_ascii=False), encoding="utf-8")
        page.evaluate("() => switchTab('stats')")
        page.wait_for_timeout(400)
        page.evaluate("() => switchTab('defect')")
        page.wait_for_timeout(900)
        badge = page.query_selector("#proposal-count")
        check("提案角标元素存在", badge is not None)
        check("提案角标已显示（列表非空时摘 hidden）",
              page.eval_on_selector("#proposal-count",
                                    "el => !el.classList.contains('hidden')"))

        check("无 pageerror（本段）",
              not errors,
              "; ".join(errors)[:200])
        browser.close()


def main() -> int:
    errors = []
    procs = []
    tmp = Path(tempfile.mkdtemp(prefix="vue-defect-"))
    proc, base, dirs = start_server(tmp)
    procs.append(proc)
    try:
        print("\n  —— 唯一实现：GET / （Vue 3）——")
        run(base, dirs, errors=errors)
        check("无 pageerror", not errors, "; ".join(errors)[:300])
    finally:
        for pr in procs:
            if pr and pr.poll() is None:
                pr.terminate()

    print(f"\n{'全部通过' if not BAD else '失败 %d 项：%s' % (len(BAD), '；'.join(BAD))}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
