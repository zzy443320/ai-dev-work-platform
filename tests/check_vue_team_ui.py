# -*- coding: utf-8 -*-
"""长任务作业页签迁移断言：对唯一实现（`GET /`，Vue 3）跑一遍断言。

前端迁移已收口：`GET /` 直接返回 Vue 3 产物（`/v2` 已下线，旧版静态页与迁移期
debugBridge 均已删除），因此本脚本只跑一次，全部走真实 DOM 控件。

断言从既有用例里搬，不另发明口径：

  - 六个面板齐全 + 顺序 + 折叠/状态记忆   ← tests/check_panel_fold.py（面板契约）
  - 四角色看板（teamInit 接线）            ← tests/check_team_ui.py 第 2 节
  - 时间线筛选下拉按角色填充               ← tests/check_team_ui.py 第 2 节
  - 跑通一次作业（Mock）：卡片终态 / 工作项 / 时间线 / 复核 / 历史
                                          ← tests/check_team_ui.py 第 3 节
  - 结束后控件回收 + 介入提示               ← tests/check_team_ui.py 第 4 节
  - 历史只读回放                           ← tests/check_team_ui.py 第 5 节
  - 没有把 undefined / NaN 渲染出来          ← tests/check_team_ui.py 第 6 节
  - #team-log 空态占位（与 #run-log 同一套 CSS 契约）

与 check_team_ui.py 的分工：那个脚本跑在 8765（真实实例），本脚本自带临时实例，
所以对本机端口占用免疫。**新增断言优先加到本脚本**，check_team_ui.py 保持为
「真实实例的冒烟」。

用法：.venv\\Scripts\\python.exe tests/check_vue_team_ui.py
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

from ui_select import el_select_values, pick_select      # noqa: E402

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


PANELS = ["team-run-panel", "team-board-panel", "team-plan-panel",
          "team-timeline-panel", "team-intervene-panel", "team-history-panel"]


def run(base: str, *, errors: list) -> None:
    """对唯一实现（GET /，Vue 3）的长任务作业页签跑一遍断言。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("dialog", lambda d: d.accept())
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_timeout(900)

        # ── 页签存在且能切过去 ──
        pane = page.query_selector("#pane-team")
        check("#pane-team 存在", pane is not None)
        page.evaluate("() => switchTab('team')")
        page.wait_for_timeout(1400)
        check("切到长任务作业页后 .active",
              page.eval_on_selector("#pane-team", "el => el.classList.contains('active')"))

        # ── 1. 六个面板齐全 + 顺序 ──
        tops = page.evaluate("""ids => ids.map(id => {
          const el = document.getElementById(id);
          return el ? el.getBoundingClientRect().top : null;
        })""", PANELS)
        check("六个面板齐全", all(t is not None for t in tops), str([round(t) if t else t for t in tops]))
        # 驾驶舱双列重排（战场左 / 控制塔右）后 top 不再全局递增 ——
        # 意图不变：DOM 源顺序仍是 表单→看板→工作项→时间线→介入→历史
        # （折叠记忆、无障碍树与窄屏单列降级都依赖它），改断 compareDocumentPosition。
        dom_ordered = page.evaluate("""ids => {
          const els = ids.map(id => document.getElementById(id));
          for (let i = 1; i < els.length; i++) {
            if (!(els[i - 1].compareDocumentPosition(els[i]) & Node.DOCUMENT_POSITION_FOLLOWING))
              return false;
          }
          return true;
        }""", PANELS)
        check("面板 DOM 顺序：表单 → 看板 → 工作项 → 时间线 → 介入 → 历史",
              dom_ordered, str(PANELS))
        check("#pane-team 是 grid 布局",
              page.eval_on_selector("#pane-team", "el => getComputedStyle(el).display") == "grid")

        # ── 2. 折叠口径（2026-10-15 第七轮定稿）──
        # 配置与运行监视**不可收起**（它们是这一页的输入口与现场，收起只会让人
        # 以为没地方填参数 / 看不到进度）；历史作业仍可折叠 + 记状态。
        nofold = page.evaluate("""() => ({
          run: !!document.querySelector('#team-run-panel .fold-btn'),
          cockpit: !!document.querySelector('#team-cockpit-panel .fold-btn'),
        })""")
        check("配置 / 运行监视不可收起（没有折叠按钮）",
              not nofold["run"] and not nofold["cockpit"], str(nofold))
        page.evaluate("() => localStorage.removeItem('panel-fold-state')")
        page.wait_for_timeout(150)
        h_before = page.eval_on_selector("#team-history-panel", "el => el.offsetHeight")
        page.click("#team-history-panel .fold-btn")
        page.wait_for_timeout(200)
        collapsed = page.eval_on_selector("#team-history-panel",
                                          "el => el.classList.contains('collapsed')")
        h_after = page.eval_on_selector("#team-history-panel", "el => el.offsetHeight")
        check("历史作业折叠后带 collapsed 类且变矮",
              collapsed and h_after < h_before, f"{h_before}->{h_after}")
        saved = page.evaluate("() => localStorage.getItem('panel-fold-state')")
        check("折叠状态写 panel-fold-state（与旧版同键）",
              saved and '"team-history-panel":true' in saved.replace(" ", ""), str(saved))
        page.reload(wait_until="networkidle")
        page.evaluate("() => switchTab('team')")
        page.wait_for_timeout(1200)
        check("刷新后折叠状态保持",
              page.eval_on_selector("#team-history-panel", "el => el.classList.contains('collapsed')"))
        page.click("#team-history-panel .fold-btn")
        page.wait_for_timeout(200)
        check("二次点击展开",
              page.eval_on_selector("#team-history-panel", "el => !el.classList.contains('collapsed')"))
        page.evaluate("() => localStorage.removeItem('panel-fold-state')")

        # ── 3. 时间线空态：占位提示 + 未渲染内容容器 ──
        ph = page.eval_on_selector("#team-log", "el => getComputedStyle(el, '::before').content")
        check("#team-log 空态占位提示存在",
              bool(ph) and ph not in ("none", "normal"), str(ph)[:50])
        check("空态放大按钮隐藏",
              page.eval_on_selector("#btn-team-expand", "el => el.classList.contains('hidden')"))

        # ── 4. 角色看板（teamInit 接线）──
        cards = page.query_selector_all("#team-agents .team-agent")
        check("看板 4 张角色卡", len(cards) == 4, f"实际 {len(cards)} 张")
        if len(cards) == 4:
            names = [c.inner_text() for c in cards]
            for want in ("决策官", "编码工程师", "测试工程师", "复核官"):
                check(f"看板含角色「{want}」", any(want in n for n in names))
        # #team-filter 已迁 el-select：选项列表惰性渲染，用助手点开读 data-value
        opts = el_select_values(page, "#team-filter")
        check("时间线筛选按角色填充（all + 4）",
              opts and opts[0][0] == "all" and len(opts) >= 5, str(opts))

        # ── 5. 跑一次作业（Mock，秒级完成）：点真实「开始作业」按钮 ──
        # 参数形态改版（2026-10-13）：作业表单在**常驻参数轨**里，不再是要点开的
        # 浮层抽屉 —— 「先点修改作业参数」这一步随抽屉消失，控件常驻即可填。
        check("作业参数轨常驻（无抽屉）",
              page.is_visible("#param-rail-team")
              and not page.query_selector("#btn-team-params"))
        page.fill("#team-title", "UI 自检 · 旧表格组件迁移")
        page.fill("#team-task",
                  "把 src/views 下的列表页统一迁移到新表格组件，接口调用改到 src/api/v2，"
                  "页面路由与查询参数保持不变。")
        page.click("#btn-team-run")
        # 跑动中参数区必须**定格**：后端按启动时的参数开跑，界面上那份表单
        # 再可编辑就是骗人（改版前靠抽屉自动收起掩盖了这点）。
        try:
            page.wait_for_selector("#pane-team .plock", timeout=8000)
            lock = page.evaluate("""() => {
              const el = document.querySelector('#pane-team .plock');
              const txt = el ? el.innerText : '';
              return {railGone: !document.getElementById('param-rail-team'),
                      hasTitle: txt.includes('UI 自检 · 旧表格组件迁移'),
                      hasRoles: txt.includes('参与角色'),
                      note: txt.includes('对这一批无效'),
                      stillEditable: !!document.querySelector('#pane-team #team-title')};
            }""")
            check("跑动中参数轨换成「本批配置」定格卡", lock["railGone"] and lock["hasTitle"], str(lock))
            check("定格卡说清这一批用了什么、为什么改不动",
                  lock["hasRoles"] and lock["note"], str(lock))
            check("定格期间不再渲染可编辑表单", not lock["stillEditable"], str(lock))
        except Exception as e:
            check("跑动中出现定格卡（8s 内）", False, str(e)[:120])
        try:
            page.wait_for_function(
                "() => { const el = document.querySelector('#team-run-meta');"
                " return !!el && el.textContent.trim().startsWith('已结束'); }",
                timeout=120000)
        except Exception:
            check("作业在 120s 内结束", False, "看板没有走到终态")

        done_cards = page.query_selector_all("#team-agents .team-agent.done")
        check("有角色卡进入「已完成」", len(done_cards) > 0)
        check("跑完之后参数轨恢复可编辑表单",
              page.is_visible("#param-rail-team") and page.is_visible("#team-title")
              and not page.query_selector("#pane-team .plock"))
        items = page.query_selector_all("#team-plan .team-item")
        check("工作项已渲染（plan 事件接上）", len(items) > 0)
        lines = page.query_selector_all("#team-log .live-line")
        check("时间线 >= 5 行（事件分发没漏类型）", len(lines) >= 5, f"{len(lines)} 行")
        review_hidden = page.eval_on_selector("#team-review", "el => el.classList.contains('hidden')")
        check("复核结论已出现", not review_hidden)
        if not review_hidden:
            check("复核区含「复核」字样", "复核" in page.inner_text("#team-review"))
        hist = page.query_selector_all("#team-history .pcard")
        check("历史作业里有本次记录", len(hist) > 0)
        # 跑完后放大按钮应可见
        check("跑完后放大按钮可见",
              page.eval_on_selector("#btn-team-expand", "el => !el.classList.contains('hidden')"))

        # ── 5a+. 时间线「只看」筛选（2026-10-15 第八轮：必须真正过滤每一行）──
        # 后端 log 事件不带 agent，但 plan / item_status / agent_status 都带 ——
        # 用真实 mock 作业断言：选「编码工程师」后决策官的行必须消失。
        n_all = len(page.query_selector_all("#team-log .live-line"))
        pick_select(page, "#team-filter", "coder")
        page.wait_for_timeout(300)
        lines_c = page.query_selector_all("#team-log .live-line")
        joined = " ".join(l.inner_text() for l in lines_c)
        check("只看=编码工程师：时间线被过滤（行数变少且无决策官行）",
              0 < len(lines_c) < n_all and "🧭" not in joined,
              f"all={n_all} coder={len(lines_c)}")
        pick_select(page, "#team-filter", "all")
        page.wait_for_timeout(300)
        check("切回「全部」恢复完整时间线",
              len(page.query_selector_all("#team-log .live-line")) == n_all)
        # 时间线占满中间段剩余高度（第八轮）：pre 解除全局 420px 封顶后
        # 应撑满 .ck-mid，与介入条只隔一拍内边距。
        fill = page.evaluate("""() => {
          const log = document.getElementById('team-log');
          const mid = document.querySelector('#team-cockpit-panel .ck-mid');
          const itv = document.getElementById('team-intervene-panel');
          const lr = log.getBoundingClientRect();
          return {logH: Math.round(lr.height),
                  midH: Math.round(mid.getBoundingClientRect().height),
                  gap: Math.round(itv.getBoundingClientRect().top - lr.bottom)};
        }""")
        check("时间线占满中间段剩余高度（≥60% 且距介入条 ≤48px）",
              fill["logH"] >= fill["midH"] * 0.6 and 0 <= fill["gap"] <= 48, str(fill))

        # ── 5b. 角色输出浮层抽屉（2026-10-15 第八轮：点名片弹窗看输出）──
        # 用户推翻第七轮的「就地切视图」：那会让时间线消失，看输出时看不到现场；
        # 抽屉盖在整页之上，关掉即回。抽屉常驻 DOM 只切 .hidden（hidden 契约）。
        check("#team-drawer 常驻 DOM 且初始隐藏",
              page.query_selector("#team-drawer") is not None
              and page.eval_on_selector("#team-drawer", "el => el.classList.contains('hidden')"))
        page.click("#team-agents .team-agent")
        page.wait_for_timeout(400)
        check("点名片打开浮层抽屉",
              page.eval_on_selector("#team-drawer", "el => !el.classList.contains('hidden')"))
        th = page.eval_on_selector(
            "#team-drawer .team-term:not(.hidden) .ai-block.out pre",
            "el => Math.round(el.getBoundingClientRect().height)")
        # 抽屉上下贴边（1000 视口 - 上下 20px - 头部 ≈ 900 可用），终端应吃到大半
        check("抽屉里的终端吃到剩余高度（≥240px）", th >= 240, f"{th}px")
        check("抽屉带「把指令发给它」与「关闭」",
              page.eval_on_selector("#btn-team-target-agent", "el => !!el")
              and page.eval_on_selector("#team-drawer-close", "el => !!el"))
        page.click("#team-drawer-close")
        page.wait_for_timeout(300)
        back = page.evaluate("""() => {
          const dr = document.getElementById('team-drawer');
          const log = document.getElementById('team-log');
          return {closed: dr.classList.contains('hidden'),
                  logVisible: !!(log && log.offsetParent !== null)};
        }""")
        check("关闭抽屉回到时间线", back["closed"] and back["logVisible"], str(back))

        # ── 5b. 真实介入控件：作业已结束，「发送」置灰 ──
        check("结束后「开始作业」恢复可用",
              not page.eval_on_selector("#btn-team-run", "el => el.disabled"))
        check("结束后「发送」置灰",
              page.eval_on_selector("#btn-team-send", "el => el.disabled"))
        hint = page.inner_text("#team-intervene-hint").strip()
        check("结束后介入提示=「作业未运行」", hint == "作业未运行", repr(hint))

        # ── 6. 历史只读回放：点真实历史卡片 ──
        run_id = page.evaluate(
            "async () => (await (await fetch('/api/team/runs')).json())[0].id")
        page.click("#team-history .pcard")
        page.wait_for_timeout(1200)
        meta = page.inner_text("#team-run-meta")
        check("回放把运行记录灌回看板", run_id in meta, repr(meta))
        hint2 = page.inner_text("#team-intervene-hint").strip()
        check("回放时提示含「历史回放」", "历史回放" in hint2, repr(hint2))
        check("回放状态下「发送」只读",
              page.eval_on_selector("#btn-team-send", "el => el.disabled"))
        check("回放状态下「开始作业」可用（可开新作业）",
              not page.eval_on_selector("#btn-team-run", "el => el.disabled"))

        # ── 7. 没有把 undefined / NaN 渲染出来 ──
        for pid in ("team-agents", "team-plan", "team-log", "team-history"):
            txt = page.inner_text(f"#{pid}")
            bad = [b for b in ("undefined", "NaN", "[object Object]") if b in txt]
            check(f"#{pid} 无 undefined/NaN", not bad, str(bad))

        browser.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vue_team_"))
    proc = None
    errors = []
    try:
        proc, base, dirs = start_server(tmp)
        run(base, errors=errors)
    finally:
        if proc:
            proc.kill()

    print(f"\n合计 {len(OK)} 项通过 / {len(BAD)} 项失败")
    for e in errors:
        print("PAGEERROR:", e)
    if BAD or errors:
        for b in BAD:
            print("FAIL:", b)
        return 1
    print("PASS: 长任务作业页签（唯一实现 GET /，Vue 3）：面板 / 看板 / 跑通作业 / 控件回收 / 历史回放")
    return 0


if __name__ == "__main__":
    sys.exit(main())
