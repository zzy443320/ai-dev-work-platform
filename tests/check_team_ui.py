# -*- coding: utf-8 -*-
"""长任务作业页签的界面自检（Playwright）。

离线逻辑由 `tests/check_team.py` 覆盖，HTTP 层由 `tests/check_team_api.py` 覆盖；
这个脚本补的是**界面这一层**——页签接线漏了、事件分发漏了一种类型，前两个脚本
都发现不了，但用户一打开就是空白：

1. 页签与面板：切到「长任务作业」后 `#pane-team` 激活，六个面板都在；
2. 角色看板：`teamInit()` 拉回四个子 Agent 并各占一张卡（不是空态提示）；
3. 跑一次作业（强制 Mock）：卡片状态从「待命」走到「已完成」、工作项渲染、
   实时时间线有行、复核结论出现、历史里多出一条记录；
4. 结束后控件回收：开始按钮重新可用，介入类按钮置灰，提示回到「作业未运行」；
5. 历史回放：点开历史卡片能把记录灌回看板（只读），介入区提示为「历史回放 · 只读」；
6. 页面没有把 `undefined` / `NaN` 渲染出来——接线漏了最常见的症状。

需要 `web.server` 已在 8765 运行（`tests/settings_state.py` 只认 8765）+ 已装
`playwright install chromium`。会临时把 AI 切成 Mock 再还原，不依赖环境里的真实密钥。

用法：
    python tests/check_team_ui.py
"""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TESTS_DIR))

from playwright.sync_api import sync_playwright  # noqa: E402
from settings_state import force_mock, restore  # noqa: E402

BASE = "http://127.0.0.1:8765"   # settings_state 写死了 8765，临时实例跑不了这个脚本
PANELS = ["team-run-panel", "team-board-panel", "team-plan-panel",
          "team-timeline-panel", "team-intervene-panel", "team-history-panel"]


def main() -> int:
    failures = []
    prev_ai = force_mock()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(f"{BASE}/", wait_until="networkidle")

            # ── 1. 页签与面板 ──
            page.evaluate("() => switchTab('team')")
            page.wait_for_timeout(1500)
            if not page.eval_on_selector("#pane-team", "el => el.classList.contains('active')"):
                failures.append("切到「长任务作业」后 #pane-team 没有激活")
            for pid in PANELS:
                if not page.query_selector(f"#{pid}"):
                    failures.append(f"缺少面板 #{pid}")

            # ── 2. 角色看板（teamInit 的接线） ──
            cards = page.query_selector_all("#team-agents .team-agent")
            if len(cards) != 4:
                failures.append(f"看板应有 4 张角色卡，实际 {len(cards)} 张"
                                "（teamInit 可能没接到 loadAll/switchTab 上，或角色接口挂了）")
            else:
                names = [c.inner_text() for c in cards]
                for want in ("决策官", "编码工程师", "测试工程师", "复核官"):
                    if not any(want in n for n in names):
                        failures.append(f"看板缺少角色「{want}」")
            # 介入下拉按角色填充（teamSyncFilterOptions 的另一处）
            opts = page.eval_on_selector_all("#team-filter option", "els => els.map(e => e.value)")
            if opts[:1] != ["all"] or len(opts) < 4:
                failures.append(f"时间线筛选没有按角色填充：{opts!r}")

            # ── 3. 跑一次作业（Mock，秒级完成） ──
            page.fill("#team-title", "UI 自检 · 旧表格组件迁移")
            page.fill("#team-task",
                      "把 src/views 下的列表页统一迁移到新表格组件，接口调用改到 src/api/v2，"
                      "页面路由与查询参数保持不变。")
            page.click("#btn-team-run")
            try:
                page.wait_for_function(
                    "() => { const el = document.querySelector('#team-run-meta');"
                    " return !!el && el.textContent.trim().startsWith('已结束'); }",
                    timeout=120000)
            except Exception:
                failures.append("作业在 120s 内没有结束（看板没有走到终态）")

            done_cards = page.query_selector_all("#team-agents .team-agent.done")
            if not done_cards:
                failures.append("作业结束了但没有任何角色卡进入「已完成」")
            items = page.query_selector_all("#team-plan .team-item")
            if not items:
                failures.append("工作项没有渲染（决策官的 plan 事件没接上）")
            lines = page.query_selector_all("#team-log .live-line")
            if len(lines) < 5:
                failures.append(f"实时时间线只有 {len(lines)} 行，事件分发可能漏了类型")
            if page.eval_on_selector("#team-review", "el => el.classList.contains('hidden')"):
                failures.append("复核结论没有出现")
            elif "复核" not in page.inner_text("#team-review"):
                failures.append("复核区渲染了但不是复核结论")
            hist = page.query_selector_all("#team-history .pcard")
            if not hist:
                failures.append("历史作业里没有本次记录（loadTeamRuns 未刷新）")

            # ── 4. 结束后控件回收 ──
            if page.eval_on_selector("#btn-team-run", "el => el.disabled"):
                failures.append("作业结束后「开始作业」仍是禁用状态")
            if not page.eval_on_selector("#btn-team-send", "el => el.disabled"):
                failures.append("作业结束后「发送」仍可点（会误导用户以为指令能送进去）")
            hint = page.inner_text("#team-intervene-hint").strip()
            if hint != "作业未运行":
                failures.append(f"结束后介入提示异常：{hint!r}")

            # ── 5. 历史回放（只读） ──
            run_id = page.evaluate(
                "async () => (await (await fetch('/api/team/runs')).json())[0].id")
            page.evaluate("id => openTeamRun(id)", run_id)
            page.wait_for_timeout(1200)
            meta = page.inner_text("#team-run-meta")
            if run_id not in meta:
                failures.append(f"回放没有把运行记录灌回看板：{meta!r}")
            hint2 = page.inner_text("#team-intervene-hint").strip()
            if "历史回放" not in hint2:
                failures.append(f"回放时介入提示异常：{hint2!r}")
            if page.eval_on_selector("#btn-team-send", "el => !el.disabled"):
                failures.append("回放状态下「发送」可点（应只读）")

            # ── 6. 没有把 undefined / NaN 渲染出来 ──
            for pid in ("team-agents", "team-plan", "team-log", "team-history"):
                txt = page.inner_text(f"#{pid}")
                for bad in ("undefined", "NaN", "[object Object]"):
                    if bad in txt:
                        failures.append(f"#{pid} 渲染出了 {bad}（字段没取到就直拼了）")
                        break

            browser.close()
    finally:
        restore(prev_ai)

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: 页签接线 / 四角色看板 / 跑通一次作业 / 控件回收 / 历史只读回放 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
