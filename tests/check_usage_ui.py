# -*- coding: utf-8 -*-
"""UI 检查：统计面板（第一个页签）+ token 用量图表。

覆盖：
1. 统计面板是**第一个页签**且为默认落地页（`.tab` 首位、pane-stats 带 active）；
2. 五张汇总卡片（区间/今天/本周/本月/累计）数值与 `/api/usage/report` 完全一致——
   断言的是 DOM 事实，顺带兜住 `NaN` / `undefined` 这类渲染事故；
3. 趋势图按粒度画堆叠柱（输入+输出），柱子数 == 分桶数；空桶也有存在感；
4. 「按任务类型」横向条数量 == 类型数，且标签走中文（KIND_LABELS 同源）；
5. 「按模型」环形图（多模型走弧线、单模型走整环降级）；
6. 「单任务消耗」排行表按合计降序，第一名就是账本里最费 token 的任务；
7. 最近调用明细带「估算 / 失败」角标；
8. 粒度（日/周/月）与区间（7/30/90/365）分段控件真的重新取数并写入 localStorage，
   刷新后保持；
9. 无 pageerror、无控制台报错。

自包含：默认自己起一个临时服务实例（`USAGE_DIR` 指向临时账本），灌入可预期的
记录再断言——不污染真实账本，也不依赖 8765 上正在跑的服务。
传 base_url 则直接打已有实例（此时只做「DOM 与 API 一致」的结构断言）。

用法：
    python tests/check_usage_ui.py
    python tests/check_usage_ui.py http://127.0.0.1:8765
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta
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


# --------------------------------------------------------------- 临时账本
def seed_ledger(path: Path) -> None:
    """灌入可预期的记录。日期相对今天，保证分桶落在窗口内。"""
    today = date.today()

    def rec(day, kind, run_id, title, step, model, inp, out,
            ok=True, estimated=False, agent="", mode="openai"):
        ts = datetime.combine(day, datetime.min.time()).replace(
            hour=10, minute=30, second=5)
        return {
            "ts": ts.isoformat(timespec="seconds"),
            "kind": kind, "run_id": run_id, "title": title, "step": step,
            "agent": agent, "model": model, "mode": mode, "attempt": 1,
            "input_tokens": inp, "output_tokens": out, "reasoning_tokens": 0,
            "total_tokens": inp + out, "estimated": estimated,
            "seconds": 1.5, "ok": ok,
            "error": "" if ok else "网关 502",
        }

    rows = [
        # 今天：缺陷修复 D-1 两次 + 长任务作业 R-1（含子 agent 归属）
        rec(today, "defect", "D-1", "重复周期应该必填", "locate",
            "deepseek-flash", 1000, 400),
        rec(today, "defect", "D-1", "重复周期应该必填", "verify",
            "deepseek-flash", 2000, 800),
        rec(today, "team", "R-1", "组件库升级迁移", "coder",
            "deepseek-flash", 3000, 1500, agent="coder"),
        # 今天：一次估算 + 一次失败（角标口径）
        rec(today, "reqdev", "D-9", "日程页 Figma 转码", "figma",
            "claude-3-5", 500, 200, estimated=True),
        rec(today, "defect", "D-7", "接口报 500", "locate",
            "deepseek-flash", 300, 0, ok=False),
        # 昨天 / 3 天前：跨桶
        rec(today - timedelta(days=1), "defect", "D-2", "表单校验失效", "locate",
            "deepseek-flash", 700, 300),
        rec(today - timedelta(days=3), "team", "R-2", "老组件替换", "tester",
            "deepseek-flash", 400, 100, agent="tester"),
        # 9 天前：7 天窗口外、30 天窗口内
        rec(today - timedelta(days=9), "apidebug", "A-1", "联调超时", "verify",
            "claude-3-5", 900, 100),
        # 40 天前：30 天窗口外（只进「累计」）
        rec(today - timedelta(days=40), "defect", "D-0", "远古工单", "locate",
            "deepseek-flash", 111, 22),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(usage_dir: Path, port: int):
    code = (
        "import os;"
        f"os.environ['USAGE_DIR']={str(usage_dir)!r};"
        "import uvicorn;"
        "from web.server import app;"
        f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='error')"
    )
    env = dict(os.environ)
    env["USAGE_DIR"] = str(usage_dir)
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


# ------------------------------------------------------------------- 断言
def run(base: str, seeded: bool) -> None:
    rep = api(base, "/api/usage/report?days=30&granularity=day")
    kinds_api = api(base, "/api/usage/kinds")
    labels = kinds_api["labels"]
    tot = rep["totals"]
    print(f"  [api] 区间合计={tot['total']} tokens / {tot['calls']} 次调用"
          f"（估算 {tot['estimated']}、失败 {tot['failed']}）")

    if seeded:
        check("临时账本已灌入数据（否则后面全是空态断言）", tot["total"] > 0,
              f"total={tot['total']}")
        check("分桶数 = 区间天数", len(rep["buckets"]) == 30, str(len(rep["buckets"])))

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1680, "height": 1100}).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.goto(f"{base}/", wait_until="networkidle")
        page.wait_for_timeout(1200)

        # ── 1. 第一个页签 + 默认落地 ──
        order = page.evaluate("() => [...document.querySelectorAll('.tab')].map(t => t.dataset.tab)")
        check("统计是第一个页签", order and order[0] == "stats", str(order))
        check("默认落在统计面板",
              "active" in (page.get_attribute('.tab[data-tab="stats"]', "class") or ""))
        check("pane-stats 是可见的活动页",
              page.eval_on_selector("#pane-stats",
                                    "el => el.classList.contains('active') && "
                                    "getComputedStyle(el).display !== 'none'"))
        check("其它页签仍是隐藏的（没有同时亮）",
              page.eval_on_selector("#pane-defect",
                                    "el => getComputedStyle(el).display === 'none'"))

        # ── 2. 汇总卡片与 API 完全一致 ──
        cards = page.eval_on_selector_all(
            "#usage-cards .usage-card",
            "els => els.map(e => ({label: e.querySelector('.usage-card-label').textContent,"
            " value: e.querySelector('.usage-card-value').textContent,"
            " sub: e.querySelector('.usage-card-sub').textContent}))")
        check("五张汇总卡片", len(cards) == 5, str(len(cards)))
        want = [("区间内", rep["totals"]), ("今天", rep["today"]),
                ("本周", rep["this_week"]), ("本月", rep["this_month"]),
                ("累计", rep["all_time"])]
        labels_seen = [c["label"] for c in cards]
        check("卡片顺序 区间/今天/本周/本月/累计",
              labels_seen == [w[0] for w in want], str(labels_seen))
        for (name, t), card in zip(want, cards):
            expected = f"{t['total']:,}"
            check(f"「{name}」合计与 API 一致（{expected}）",
                  card["value"].startswith(expected),
                  f"DOM={card['value']!r} API={t['total']}")
        check("首张卡片有 primary 强调",
              page.eval_on_selector("#usage-cards .usage-card",
                                    "el => el.classList.contains('primary')"))

        pane_text = page.inner_text("#pane-stats")
        check("面板文本无 NaN / undefined / null",
              not any(x in pane_text for x in ("NaN", "undefined", "null")),
              pane_text[:80].replace("\n", " "))

        # ── 3. 说明区如实交代估算与 mock 口径 ──
        note = page.inner_text("#usage-note")
        check("说明区讲清「真实调用才入账」", "真实" in note and "入账" in note)
        check("说明区给出账本路径", str(Path(rep["note"]["ledger"]).name) in note
              or "usage.jsonl" in note)

        # ── 4. 趋势图 ──
        if tot["total"]:
            bars = page.eval_on_selector_all("#usage-trend .usage-bar",
                                             "els => els.length")
            check("趋势柱子数 = 分桶数", bars == len(rep["buckets"]),
                  f"DOM={bars} API={len(rep['buckets'])}")
            check("趋势图有输入/输出图例",
                  page.eval_on_selector_all("#usage-trend .usage-legend span",
                                            "els => els.length") == 2)
            check("趋势图是手绘 SVG（无第三方图表库依赖）",
                  page.eval_on_selector("#usage-trend", "el => !!el.querySelector('svg')"))
            meta = page.inner_text("#usage-trend-meta")
            check("趋势标题写明粒度与刻度数",
                  "每天" in meta and "刻度" in meta and "峰值" in meta, meta)
            # 悬停提示带真实数值（<title> 是 SVG tooltip）：取峰值那根柱子来验，
            # 空桶也要有提示（否则「有断档」这件事在界面上无从察觉）
            tips = page.eval_on_selector_all(
                "#usage-trend .usage-bar",
                "els => els.map(e => e.querySelector('title').textContent)")
            peak_i = max(range(len(rep["buckets"])),
                         key=lambda i: rep["buckets"][i]["total"])
            peak = rep["buckets"][peak_i]
            check("峰值柱子提示含合计与调用次数",
                  f"合计 {peak['total']:,} tokens" in tips[peak_i]
                  and "次调用" in tips[peak_i], tips[peak_i].split("\n")[0])
            check("柱子提示带所属日期",
                  peak["date"] in tips[peak_i], tips[peak_i].split("\n")[0])
            empty = [i for i, b in enumerate(rep["buckets"]) if not b["total"]]
            if empty:
                check("空桶也带提示（0 值与 0 次调用）",
                      "合计 0 tokens" in tips[empty[0]] and "0 次调用" in tips[empty[0]],
                      tips[empty[0]].split("\n")[0])
        else:
            check("无数据时趋势区给空态提示（而不是空白）",
                  "还没有真实" in page.inner_text("#usage-trend"))

        # ── 5. 按任务类型 ──
        rows = page.eval_on_selector_all("#usage-kinds .usage-row",
                                         "els => els.length")
        check("类型条数 = 有记录的类型数", rows == len(rep["kinds"]),
              f"DOM={rows} API={len(rep['kinds'])}")
        if rep["kinds"]:
            shown = page.eval_on_selector_all(
                "#usage-kinds .usage-row-label", "els => els.map(e => e.textContent)")
            want_labels = [labels.get(k["key"], k["key"]) for k in rep["kinds"]]
            check("类型标签用中文（与后端 KIND_LABELS 同源）",
                  shown == want_labels, f"DOM={shown} 期望={want_labels}")
            pct = page.eval_on_selector_all(
                "#usage-kinds .usage-row-val", "els => els.map(e => e.textContent)")
            check("类型占比合计≈100%",
                  abs(sum(float(x.split()[-1].rstrip('%')) for x in pct) - 100) < 0.5,
                  str(pct))
        else:
            check("无数据时类型区给空态", "暂无数据" in page.inner_text("#usage-kinds"))

        # ── 6. 按模型（环形） ──
        if rep["models"]:
            check("模型环形图存在",
                  page.eval_on_selector("#usage-models", "el => !!el.querySelector('.usage-donut')"))
            paths = page.eval_on_selector_all("#usage-models .usage-donut path",
                                              "els => els.length")
            if len(rep["models"]) == 1:
                check("单模型时降级为整环（不画 0 度弧）", paths == 0)
            else:
                check("多模型时弧数 = 模型数", paths == len(rep["models"]),
                      f"DOM={paths} API={len(rep['models'])}")
            legend = page.eval_on_selector_all("#usage-models .usage-donut-legend div",
                                               "els => els.map(e => e.textContent)")
            check("环形图例含模型名与占比", len(legend) == len(rep["models"]),
                  str(legend)[:120])
        else:
            check("无数据时模型区给空态", "暂无数据" in page.inner_text("#usage-models"))

        # ── 7. 单任务排行 ──
        trows = page.eval_on_selector_all(
            "#usage-tasks tbody tr",
            "els => els.map(e => ({kind: e.children[1].textContent.trim(),"
            " title: e.children[2].textContent.trim(),"
            " total: e.children[6].textContent.trim()}))")
        check("任务行数 = API 任务数", len(trows) == len(rep["tasks"]),
              f"DOM={len(trows)} API={len(rep['tasks'])}")
        if rep["tasks"]:
            top_api = rep["tasks"][0]
            check("排行第一名是最费 token 的任务（降序）",
                  trows[0]["total"] == f"{top_api['total']:,}",
                  f"DOM={trows[0]['total']} API={top_api['total']}")
            check("第一名任务标题与 API 一致",
                  trows[0]["title"].startswith((top_api.get("title") or "")[:8]),
                  f"DOM={trows[0]['title']!r}")
            check("第一名类型显示中文",
                  trows[0]["kind"] == labels.get(top_api.get("kind"), ""),
                  f"DOM={trows[0]['kind']!r}")
            real_names = {str(r.get("title") or "(无标题)") for r in rep["tasks"]}
            check("任务标题真实（不是占位串）",
                  all("未命名" not in n and "unknown" not in n.lower() for n in real_names))
        else:
            check("无数据时任务区给空态", "还没有任务级记录" in page.inner_text("#usage-tasks"))

        # ── 8. 最近调用 + 角标 ──
        rrows = page.eval_on_selector_all("#usage-recent tbody tr", "els => els.length")
        check("最近调用行数 = API 条数", rrows == len(rep["recent"]),
              f"DOM={rrows} API={len(rep['recent'])}")
        if rep["recent"]:
            badges = page.eval_on_selector_all(
                "#usage-recent .usage-badge", "els => els.map(e => e.textContent)")
            est_api = sum(1 for r in rep["recent"] if r.get("estimated"))
            bad_api = sum(1 for r in rep["recent"] if r.get("ok") is False)
            check("估算次数打「估算」角标",
                  badges.count("估算") == est_api,
                  f"DOM={badges.count('估算')} API={est_api}")
            check("失败调用打「失败」角标",
                  badges.count("失败") == bad_api,
                  f"DOM={badges.count('失败')} API={bad_api}")
            check("成功调用标「成功」", badges.count("成功") + bad_api == len(rep["recent"]))

        if seeded:
            page.screenshot(path=str(OUT_DIR / "usage-panel.png"), full_page=True)

        # ── 9. 粒度 / 区间控件真的重新取数 ──
        page.click('#usage-gran .seg-btn[data-g="week"]')
        page.wait_for_timeout(900)
        check("切「周」写入 localStorage",
              page.evaluate("() => localStorage.getItem('usage-gran')") == "week")
        check("切「周」后按钮高亮跟着走",
              "active" in (page.get_attribute('#usage-gran .seg-btn[data-g="week"]', "class") or ""))
        rep_w = api(base, "/api/usage/report?days=30&granularity=week")
        if rep_w["buckets"] and tot["total"]:
            n_bars = page.eval_on_selector_all("#usage-trend .usage-bar", "els => els.length")
            check("切「周」后柱子数 = 周分桶数", n_bars == len(rep_w["buckets"]),
                  f"DOM={n_bars} API={len(rep_w['buckets'])}")
            check("趋势标题改口为「每周」", "每周" in page.inner_text("#usage-trend-meta"))

        page.click('#usage-range .seg-btn[data-d="7"]')
        page.wait_for_timeout(900)
        check("切「7 天」写入 localStorage",
              page.evaluate("() => localStorage.getItem('usage-days')") == "7")
        rep7 = api(base, "/api/usage/report?days=7&granularity=week")
        cards7 = page.eval_on_selector_all(
            "#usage-cards .usage-card-value", "els => els.map(e => e.textContent)")
        check("切区间后「区间内」卡片跟着变（7 天口径）",
              cards7 and cards7[0].startswith(f"{rep7['totals']['total']:,}"),
              f"DOM={cards7[0] if cards7 else None} API={rep7['totals']['total']}")
        sub = page.inner_text("#usage-cards .usage-card-sub")
        check("区间副标题给出起止日期",
              rep7["range"]["from"][5:] in page.inner_text("#usage-cards")
              or rep7["range"]["to"][5:] in page.inner_text("#usage-cards"), sub[:40])

        # ── 10. 刷新后控件状态保持 ──
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1400)
        check("刷新后仍在统计页签（第一个页签就是它）",
              page.eval_on_selector("#pane-stats",
                                    "el => el.classList.contains('active')"))
        check("刷新后粒度/区间选择保持（localStorage）",
              "active" in (page.get_attribute('#usage-gran .seg-btn[data-g="week"]', "class") or "")
              and "active" in (page.get_attribute('#usage-range .seg-btn[data-d="7"]', "class") or ""))

        # 切到别的页签再回来，不应该报错/丢数据
        page.click('.tab[data-tab="defect"]')
        page.wait_for_timeout(400)
        page.click('.tab[data-tab="stats"]')
        page.wait_for_timeout(900)
        check("来回切页签后统计面板仍正常渲染",
              page.eval_on_selector_all("#usage-cards .usage-card", "els => els.length") == 5)

        browser.close()

    check("无 pageerror / 控制台报错", not errors, str(errors[:3]))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    given = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else ""
    if given:
        print(f"== 打已有实例 {given}（不灌数据） ==")
        run(given, seeded=False)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="usage_ui_"))
        seed_ledger(tmp / "usage.jsonl")
        port = free_port()
        print(f"== 临时实例 127.0.0.1:{port}（账本 {tmp}） ==")
        proc, base = start_server(tmp, port)
        try:
            run(base, seeded=True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    if FAILURES:
        print(f"\nFAIL（{len(FAILURES)} 项）")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nPASS: 统计面板（第一个页签）/ 汇总卡片 / 趋势 / 类型 / 模型 / 任务排行 / "
          "最近调用 / 粒度与区间切换 / 持久化 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
