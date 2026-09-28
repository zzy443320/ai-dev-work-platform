# -*- coding: utf-8 -*-
"""统计页签（唯一实现：`GET /` → Vue 3）的回归用例。

界面已经完成收口：旧版原生 HTML/JS（index.html + app.js）与迁移期的 `/v2`
路由都已删除，`/` 就是 Vue 版。所以这里只对 `/` 跑一遍，断言**具体数值**，
不再做「新旧两版逐块对比」。

做法是「造一份小账本 → 只渲染一次 → 逐块断言可读文本」：
  - 账本用固定 ts（当天 00:00 起 + 1 小时/分钟步进），跨零点的抖动会被钳掉；
  - 图表不比 SVG 路径（浮点坐标容易假报警），改成比「柱子数量 + 刻度文案 + tooltip」；
  - 折叠、粒度切换、天数切换这些交互要在这一份实现上真的生效。

为什么值得单独一个用例：统计页签是「纯读 + 纯展示」的页签，
它的一致性可以直接用 DOM 文本证明，最能证明迁移没改口径。
"""
import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

from temp_server import serve                       # noqa: E402
from playwright.sync_api import sync_playwright      # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


# 造一份小账本：三种任务类型、两个模型、含一次估算和一次失败，
# 这样卡片 / 趋势 / 构成 / 排行 / 明细五块都有内容可断言。
LEDGER = [
    # 缺陷修复：两次，其中一个流式估算
    {"kind": "defect", "run_id": "ON-1", "title": "登录按钮点击无响应",
     "step": "locate", "model": "deepseek-flash", "mode": "stream",
     "input_tokens": 1200, "output_tokens": 800, "seconds": 3.2, "ok": True},
    {"kind": "defect", "run_id": "ON-1", "title": "登录按钮点击无响应",
     "step": "patch", "model": "deepseek-flash", "mode": "stream",
     "input_tokens": 400, "output_tokens": 260, "seconds": 1.8, "ok": True,
     "estimated": True},
    # 问答：一次正常
    {"kind": "chat", "run_id": "c-1", "title": "如何做骨架屏", "step": "",
     "model": "deepseek-flash", "mode": "stream",
     "input_tokens": 900, "output_tokens": 1500, "seconds": 5.5, "ok": True},
    # 长任务作业：一次失败（网关超时）
    {"kind": "team", "run_id": "t-1", "title": "升级到 Vite 6", "step": "coder",
     "agent": "coder", "model": "deepseek-pro", "mode": "chat",
     "input_tokens": 3000, "output_tokens": 0, "seconds": 30.0, "ok": False,
     "error": "gateway timeout"},
]

# 上面四条记录的期望聚合值（区间窗口是「今天」一天，四条都落在窗口内）
EXP_TOTAL = 8060          # 输入 5500 + 输出 2560
EXP_TOK = "8,060"         # 卡片用 fmtInt 千分位
EXP_TASK_META = "3 个任务（按区间内合计降序）"
EXP_RECENT_META = "最近 4 次 · 其中 1 次为估算"
# 趋势图 y 轴刻度用 fmtTok（≥1e4 会压成 10.0k），不是千分位
EXP_AXIS = ["0", "2,500", "5,000", "7,500", "10.0k"]

def seed(srv) -> None:
    """直接往临时账本目录写 jsonl —— 账本本来就是 append-only 的纯文本格式。

    ts 固定在「今天 00:00 起 + 1 小时/分钟」，而不是 now()-2h：
    否则在 00:00~02:00 之间跑会把记录落到昨天，区间/今日卡片口径就对不上了。
    """
    import json
    from datetime import date, datetime, timedelta
    lines = []
    base = datetime.combine(date.today(), datetime.min.time())
    for i, r in enumerate(LEDGER):
        rec = {
            "ts": (base + timedelta(hours=i // 2, minutes=(i % 2) * 30)).isoformat(
                timespec="seconds"),
            "kind": r["kind"], "run_id": r.get("run_id", ""),
            "title": r.get("title", ""), "step": r.get("step", ""),
            "agent": r.get("agent", ""), "model": r.get("model", ""),
            "mode": r.get("mode", ""), "attempt": 1,
            "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
            "reasoning_tokens": 0,
            "total_tokens": r["input_tokens"] + r["output_tokens"],
            "estimated": bool(r.get("estimated")), "seconds": r.get("seconds", 0),
            "ok": bool(r.get("ok", True)), "error": r.get("error", ""),
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
    (srv.root / "usage" / "usage.jsonl").write_text("\n".join(lines) + "\n",
                                                    encoding="utf-8")


# 从 Vue 版页面里取一组「可读文本切片」
SNAP_JS = r"""
() => {
  const txt = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    // innerText 在隐藏元素上是 null，统一回落到 textContent
    const s = el.innerText != null ? el.innerText : el.textContent;
    return (s || '').replace(/\s+/g, ' ').trim();
  };
  const count = (sel) => document.querySelectorAll(sel).length;
  const cells = (sel) => Array.from(document.querySelectorAll(sel))
    .map(e => ((e.innerText != null ? e.innerText : e.textContent) || '')
      .replace(/\s+/g, ' ').trim());
  const texts = (sel) => Array.from(document.querySelectorAll(sel))
    .map(e => (e.textContent || '').replace(/\s+/g, ' ').trim());
  return {
    cards: count('#usage-cards .usage-card'),
    cardValues: cells('#usage-cards .usage-card-value'),
    cardSubs: cells('#usage-cards .usage-card-sub'),
    noteText: txt('#usage-note'),
    trendMeta: txt('#usage-trend-meta'),
    bars: count('#usage-trend .usage-bar'),
    trendTips: texts('#usage-trend .usage-bar title'),
    // y 轴刻度是唯一的「end 锚点刻度」（x 轴日期刻度是 middle，别混进来）
    yLabels: texts('#usage-trend text.usage-axis[text-anchor="end"]'),
    legend: cells('#usage-trend .usage-legend > span'),
    kindsTitle: txt('#usage-kinds .usage-chart-title'),
    kindRows: cells('#usage-kinds .usage-row-label'),
    kindVals: cells('#usage-kinds .usage-row-val'),
    modelsTitle: txt('#usage-models .usage-chart-title'),
    donutLegend: cells('#usage-models .usage-donut-legend > div'),
    donutTotal: txt('#usage-models .usage-donut-total'),
    donutArcs: count('#usage-models path'),
    taskMeta: txt('#usage-task-meta'),
    taskHead: cells('#usage-tasks thead th'),
    taskRows: count('#usage-tasks tbody tr'),
    taskFirstTitle: txt('#usage-tasks tbody tr:first-child .tname'),
    taskFirstCells: cells('#usage-tasks tbody tr:first-child td'),
    recentMeta: txt('#usage-recent-meta'),
    recentHead: cells('#usage-recent thead th'),
    recentRows: count('#usage-recent tbody tr'),
    recentFirstCells: cells('#usage-recent tbody tr:first-child td'),
    recentBadges: cells('#usage-recent .usage-badge'),
  };
}
"""


def main() -> int:
    errors = []

    with serve() as srv:
        seed(srv)
        with sync_playwright() as p:
            browser = p.chromium.launch()

            def snap():
                """打开唯一的界面入口（`/` → Vue 版），等数据落定后取一份快照。"""
                page = browser.new_page()
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                page.goto(srv.base + "/", wait_until="networkidle")
                # 等真实 DOM：账本已经就位，卡片渲染出来就代表报表回来了。
                page.wait_for_selector("#usage-cards .usage-card", timeout=15000)
                page.wait_for_selector("#usage-recent tbody tr", timeout=15000)
                data = page.evaluate(SNAP_JS)
                page.close()
                return data

            got = snap()

            print("\n  —— 汇总卡片 ——")
            check("汇总卡片 5 张", got["cards"] == 5, str(got["cards"]))
            check("卡片数值均为 8,060tokens（区间/今日/本周/本月/累计）",
                  got["cardValues"] == [f"{EXP_TOK}tokens"] * 5,
                  str(got["cardValues"]))
            check("卡片副标题按调用次数口径",
                  got["cardSubs"] == ["4 次调用 · 均 2,687 · 1 次估算 · 1 次失败"] * 5,
                  str(got["cardSubs"]))
            check("口径说明文字（含估算提示与账本路径）",
                  got["noteText"] is not None
                  and "区间内有 1 次调用网关没有返回用量" in got["noteText"]
                  and "usage.jsonl" in got["noteText"]
                  and "Mock 模式的调用不消耗 token" in got["noteText"],
                  str(got["noteText"])[:160])

            print("\n  —— 消耗趋势 ——")
            check("柱子数量 = 账本天数（含空桶，默认 30 天）",
                  got["bars"] == 30, str(got["bars"]))
            check("趋势元信息（粒度 · 刻度数 · 峰值）",
                  got["trendMeta"] == f"每天 · 30 个刻度 · 峰值 {EXP_TOK}",
                  str(got["trendMeta"]))
            check("柱子 tooltip 含当日合计/输入输出/调用次数",
                  len(got["trendTips"]) == 30
                  and f"合计 {EXP_TOK} tokens" in got["trendTips"][-1]
                  and "输入 5,500 · 输出 2,560" in got["trendTips"][-1]
                  and "4 次调用" in got["trendTips"][-1]
                  and "（1 次估算）" in got["trendTips"][-1]
                  and "（1 次失败）" in got["trendTips"][-1],
                  str(got["trendTips"][-1] if got["trendTips"] else []))
            check("y 轴刻度文案（5 条网格线）",
                  got["yLabels"] == EXP_AXIS, str(got["yLabels"]))
            check("图例含输入/输出",
                  got["legend"] == ["输入", "输出"], str(got["legend"]))

            print("\n  —— 构成 ——")
            check("任务类型标题", got["kindsTitle"] == "按任务类型", str(got["kindsTitle"]))
            check("按任务类型：行标签（按合计降序）",
                  got["kindRows"] == ["长任务作业", "缺陷修复", "问答"],
                  str(got["kindRows"]))
            check("按任务类型：数值与占比",
                  got["kindVals"] == ["3,000 37.2%", "2,660 33.0%", "2,400 29.8%"],
                  str(got["kindVals"]))
            check("按模型标题", got["modelsTitle"] == "按模型", str(got["modelsTitle"]))
            check("环形图扇区数 = 模型数", got["donutArcs"] == 2, str(got["donutArcs"]))
            check("环形图中心总量", got["donutTotal"] == EXP_TOK, str(got["donutTotal"]))
            check("环形图图例（按合计降序，含合计与占比）",
                  got["donutLegend"] == ["deepseek-flash 5,060 · 62.8%",
                                         "deepseek-pro 3,000 · 37.2%"],
                  str(got["donutLegend"]))

            print("\n  —— 单任务消耗排行 ——")
            check("表头",
                  got["taskHead"] == ["#", "类型", "任务", "调用", "输入", "输出",
                                      "合计", "占比", "最后"],
                  str(got["taskHead"]))
            check("行数 = 任务数（3 个 run_id）", got["taskRows"] == 3, str(got["taskRows"]))
            check("排名第一的任务", got["taskFirstTitle"] == "升级到 Vite 6",
                  str(got["taskFirstTitle"]))
            # 「最后」列是相对时间（relTime），随墙上时钟变化：刚跑时是「N 小时前」，
            # 跨天/超过 24h 会变「N 天前」。这里只锁住它是个相对时间，不锁具体数字。
            first_cells = got["taskFirstCells"]
            check("首行数值（调用/输入/输出/合计/占比）",
                  first_cells[:8] == ["1", "长任务作业", "升级到 Vite 6",
                                      "11 失败", "3,000", "0", "3,000", "37.2%"],
                  str(first_cells))
            check("首行「最后」列是相对时间",
                  len(first_cells) == 9
                  and re.fullmatch(r"(刚刚|\d+ 分钟前|\d+ 小时前|\d+ 天前)", first_cells[8])
                  is not None,
                  str(first_cells[8:]))
            check("元信息（任务数与排序口径）", got["taskMeta"] == EXP_TASK_META,
                  str(got["taskMeta"]))

            print("\n  —— 最近调用 ——")
            check("表头",
                  got["recentHead"] == ["时间", "类型 / 阶段", "模型", "输入", "输出",
                                        "合计", "耗时", "状态"],
                  str(got["recentHead"]))
            check("行数 = 账本记录数（4）", got["recentRows"] == 4, str(got["recentRows"]))
            check("首行数值（最新一条在最前）",
                  got["recentFirstCells"] == ["09-28 01:30:00", "长任务作业 coder · coder",
                                              "deepseek-pro / chat", "3,000", "0",
                                              "3,000", "30.0s", "失败"],
                  str(got["recentFirstCells"]))
            check("角标（成功/失败/估算齐全）",
                  got["recentBadges"] == ["失败", "成功", "成功", "估算", "成功"],
                  str(got["recentBadges"]))
            check("元信息（含估算计数）", got["recentMeta"] == EXP_RECENT_META,
                  str(got["recentMeta"]))

            # ---------------- 交互 ----------------
            # 注意：请求是异步的，且「刻度数」同时受天数和粒度影响（7 天 + 按周只剩 2 个刻度），
            # 所以每次点击后都要轮询到状态落定再断言，不能固定 sleep、也不能只盯刻度数。
            print("\n  —— 交互 ——")
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(f"interact pageerror: {e}"))
            page.goto(srv.base + "/", wait_until="networkidle")
            page.wait_for_selector("#usage-cards .usage-card", timeout=15000)

            def wait_usage(days=None, gran=None, bars=None, timeout=6000):
                cond = []
                if days is not None:
                    cond.append(f"localStorage.getItem('usage-days') === '{days}'")
                if gran is not None:
                    cond.append(f"localStorage.getItem('usage-gran') === '{gran}'")
                if bars is not None:
                    cond.append("document.querySelectorAll('#usage-trend .usage-bar').length"
                                f" === {bars}")
                return bool(page.wait_for_function("() => " + " && ".join(cond),
                                                   timeout=timeout))

            before_bars = page.eval_on_selector_all("#usage-trend .usage-bar", "e => e.length")
            # 注意：gran / days 的默认值（day / 30）是「没写 localStorage 时的回落值」
            # （见 useUsageReport.readGran / readDays），应用初始加载不会主动回写这两
            # 个键 —— 所以这里不能断言 localStorage，只能等「数据真的按默认口径画出来」。
            check("初始状态落定（按天 / 30 刻度）",
                  wait_usage(bars=30), str(before_bars))

            page.click("#usage-gran .seg-btn[data-g='week']")
            # localStorage 是 setGran 里同步写的，早于 /api/usage/report 返回；
            # 只等它会在数据回来前就放行，读到旧柱子数（全量跑时必然踩到）。
            # 所以这里直接等「刻度数真的变少」，与下面的断言同一口径。
            check("切「按周」后状态落定（存储 + 重绘）",
                  wait_usage(gran="week") and bool(page.wait_for_function(
                      f"() => document.querySelectorAll('#usage-trend .usage-bar').length < {before_bars}",
                      timeout=6000)))
            after_bars = page.eval_on_selector_all("#usage-trend .usage-bar", "e => e.length")
            check("切「按周」后刻度数变少", after_bars < before_bars,
                  f"{before_bars} → {after_bars}")
            check("切「按周」后按钮高亮跟随",
                  page.eval_on_selector("#usage-gran .seg-btn.active",
                                        "el => el.dataset.g") == "week")
            check("粒度落 localStorage（键名 usage-gran）",
                  page.evaluate("() => localStorage.getItem('usage-gran')") == "week")

            # 天数切换：先等「按天」重绘落定（此时 30 刻度），7 天才对应 7 个刻度
            page.click("#usage-gran .seg-btn[data-g='day']")
            check("切回「按天」后状态落定", wait_usage(gran="day", bars=30))
            page.click("#usage-range .seg-btn[data-d='7']")
            check("切「7 天」后刻度 = 7（轮询至落定）", wait_usage(days=7, bars=7))
            check("天数落 localStorage",
                  page.evaluate("() => localStorage.getItem('usage-days')") == "7")
            check("「7 天」按钮高亮跟随",
                  page.eval_on_selector("#usage-range .seg-btn.active",
                                        "el => el.dataset.d") == "7")

            # 折叠：状态落 localStorage
            page.click("#usage-tasks-panel .fold-btn")
            page.wait_for_function(
                "() => document.querySelector('#usage-tasks-panel')"
                ".classList.contains('collapsed')", timeout=5000)
            check("折叠后 panel 带 collapsed 类",
                  page.eval_on_selector("#usage-tasks-panel",
                                        "el => el.classList.contains('collapsed')"))
            check("折叠状态下表体不可见",
                  page.eval_on_selector("#usage-tasks",
                                        "el => getComputedStyle(el).display") == "none")
            fold_state = page.evaluate("() => localStorage.getItem('panel-fold-state')")
            check("折叠状态写入 panel-fold-state",
                  fold_state and "usage-tasks-panel" in fold_state, str(fold_state))

            # 刷新按钮：账本不变，结果应稳定
            page.click(".usage-controls .btn-ghost")
            page.wait_for_selector("#usage-recent tbody tr", timeout=15000)
            check("刷新后仍有数据（未把面板刷成空态）",
                  page.eval_on_selector_all("#usage-recent tbody tr", "e => e.length") > 0)

            check("无 pageerror", not errors, "; ".join(errors)[:200])
            browser.close()

    print(f"\n{'全部通过' if not BAD else '失败 %d 项：%s' % (len(BAD), '；'.join(BAD))}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
