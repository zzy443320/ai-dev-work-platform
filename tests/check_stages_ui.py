"""验证两项 UI 修复：

1. 板块间距：.tabpane.active 为 grid 布局，相邻 panel 有 ≥12px 的实际间隙；
2. 步骤条与运行结果真实关联：
   - stagesRunning()：运行中只有第 1 步 active；
   - stagesFromRunResults()：按 /api/run 返回数据判定 done/warn/fail/off；
   - stagesFromProbeResults()：试运行只关联前两步，3-5 为 off；
   - 真实 DOM 上 setStagesStates 后 .stage 的 class 正确。

用法：python tests/check_stages_ui.py（需 web 服务已在 8765 运行）
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

OK_RUN = {"source": "ones", "results": [
    {"status": "pending", "gate_ok": True, "gate_level": "degraded",
     "locate_empty": False, "kb_path": "k"},
]}
PARTIAL_RUN = {"source": "ones", "results": [
    {"status": "pending", "gate_ok": True, "gate_level": "degraded",
     "locate_empty": False, "kb_path": "k"},
    {"status": "invalid", "gate_ok": False, "gate_level": "none",
     "locate_empty": True, "kb_path": "k"},
]}
FETCH_FAIL = {"source": "error", "results": []}
PROBE_FAIL = {"source": "error", "fetch_error": "token 过期", "results": []}
PROBE_OK = {"source": "ones", "results": [
    {"defect": {"id": "1"}, "analysis": {"category": "逻辑"}},
]}


def main() -> int:
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://127.0.0.1:8765/", wait_until="networkidle")
        page.evaluate("() => switchTab('defect')")   # 默认页签是统计面板

        # ---- 1) 板块间距 ----
        display = page.eval_on_selector(
            "#pane-defect", "el => getComputedStyle(el).display")
        gap = page.eval_on_selector(
            "#pane-defect", "el => getComputedStyle(el).gap")
        if display != "grid":
            failures.append(f".tabpane.active display={display}（期望 grid）")
        panels_gap = page.evaluate("""() => {
          const ps = [...document.querySelectorAll('#pane-defect > .panel')]
            .filter(el => !el.classList.contains('hidden'));
          const out = [];
          for (let i = 1; i < ps.length; i++) {
            out.push(ps[i].getBoundingClientRect().top - ps[i-1].getBoundingClientRect().bottom);
          }
          return out;
        }""")
        if not panels_gap or min(panels_gap) < 12:
            failures.append(f"相邻 panel 垂直间隙不足: {panels_gap}")
        else:
            print(f"[gap] display=grid gap={gap} 相邻间隙={['%.0f' % g for g in panels_gap]}")

        # ---- 2) 步骤条状态联动（纯函数 + 真实 DOM） ----
        def expect_states(fn_expr, arg, want, label):
            got = page.evaluate(f"arg => {fn_expr}(arg)", arg)
            if got != want:
                failures.append(f"{label}: {got}（期望 {want}）")
            else:
                print(f"[stages] {label} -> {got}")

        expect_states("stagesFromRunResults", OK_RUN,
                      ["done"] * 5, "运行·全部成功")
        expect_states("stagesFromRunResults", PARTIAL_RUN,
                      # 判定策略一致：某步全部失败=fail，部分失败=warn
                      ["done", "warn", "warn", "warn", "done"], "运行·部分 invalid")
        expect_states("stagesFromRunResults", FETCH_FAIL,
                      ["fail", "off", "off", "off", "off"], "运行·拉取失败")
        expect_states("stagesFromProbeResults", PROBE_OK,
                      ["done", "done", "off", "off", "off"], "试运行·成功(3-5 off)")
        expect_states("stagesFromProbeResults", PROBE_FAIL,
                      ["fail", "fail", "off", "off", "off"], "试运行·拉取失败")

        # DOM 联动：设置运行中状态后，第 1 步 active、其余 idle
        page.evaluate("() => setStagesStates(stagesRunning())")
        cls = page.eval_on_selector_all(
            ".stage", "els => els.map(e => e.className)")
        if "stage active" not in cls[0] or any("active" in c for c in cls[1:]):
            failures.append(f"运行中状态错误: {cls}")
        page.evaluate(
            "arg => setStagesStates(stagesFromRunResults(arg))", FETCH_FAIL)
        cls = page.eval_on_selector_all(
            ".stage", "els => els.map(e => e.className)")
        if "stage fail" not in cls[0] or not all("off" in c for c in cls[1:]):
            failures.append(f"拉取失败状态错误: {cls}")
        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: 板块间距 + 步骤条真实关联全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
