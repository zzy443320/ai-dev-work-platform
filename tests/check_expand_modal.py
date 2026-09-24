# -*- coding: utf-8 -*-
"""Verify the expand button + big modal for run/probe results (UI only, no AI calls)."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / ".workbuddy"
OUT.mkdir(parents=True, exist_ok=True)
results = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    page.goto("http://127.0.0.1:8765", wait_until="load")
    page.evaluate("() => switchTab('defect')")   # 默认页签是统计面板，放大按钮在缺陷修复页
    page.wait_for_timeout(1200)

    # 1) inject mock run results using the page's own renderer
    page.evaluate(
        """
        () => {
          const data = {
            count: 2, wrote_any_files: false,
            results: [
              { id: 'L4Zo7p9z4ehvrm44', status: 'pending', category: '逻辑',
                proposal_id: '', verify_status: '',
                files: ['packages/cloudpivot-ai/lui/pages/schedule/schedule-editor.vue'],
                errors: [], warnings: [],
                root_cause: 'canSubmit 只在 scheduleType === noRepeat 时校验 date/time，其余重复类型 time 为空也可提交……（测试根因文本，验证放大弹窗换行与滚动）'.repeat(6),
                source: 'ones' },
              { id: 'ONES-1001', status: 'invalid', category: '其他',
                proposal_id: '', verify_status: '',
                files: [], errors: ['没有解析出任何可用的 SEARCH/REPLACE 块'], warnings: [],
                root_cause: '(示例) 第二条结果', source: 'mock' },
            ],
          };
          document.querySelector('#run-log').innerHTML = renderRunResults(data);
          setRunExpand();
        }
        """
    )
    page.wait_for_timeout(200)

    # 2) expand button should be visible now
    hidden = page.eval_on_selector("#btn-run-expand", "el => el.classList.contains('hidden')")
    results.append(("结果渲染后放大按钮可见", not hidden))

    # 3) click it -> modal visible with content
    page.click("#btn-run-expand")
    page.wait_for_timeout(300)
    modal_hidden = page.eval_on_selector("#runmodal", "el => el.classList.contains('hidden')")
    body_len = page.eval_on_selector("#runmodal-body", "el => el.innerHTML.length")
    title = page.eval_on_selector("#runmodal-title", "el => el.textContent")
    results.append(("点击后弹窗打开", not modal_hidden))
    results.append((f"弹窗标题='{title}'", title == "运行结果"))
    results.append((f"弹窗内容长度={body_len}", body_len > 200))

    page.screenshot(path=str(OUT / "expand-modal.png"))

    # 4) Esc closes
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    modal_hidden2 = page.eval_on_selector("#runmodal", "el => el.classList.contains('hidden')")
    results.append(("Esc 关闭弹窗", modal_hidden2))

    # 5) empty log -> button hidden again
    page.evaluate("() => { document.querySelector('#run-log').textContent=''; setRunExpand(); }")
    hidden2 = page.eval_on_selector("#btn-run-expand", "el => el.classList.contains('hidden')")
    results.append(("清空结果后按钮隐藏", hidden2))

    # 6) probe-style title check
    page.evaluate(
        """
        () => {
          runLogTitle = '试运行结果';
          document.querySelector('#run-log').innerHTML = '<div class="log-html"><div class="log-title">试运行结果：真实 ONES 工单</div><div class="run-row"><div class="probe-title">【迭代106】重复周期应该必填（模拟）</div></div></div>';
          setRunExpand();
        }
        """
    )
    page.click("#btn-run-expand")
    page.wait_for_timeout(200)
    title2 = page.eval_on_selector("#runmodal-title", "el => el.textContent")
    vis2 = page.eval_on_selector("#runmodal", "el => !el.classList.contains('hidden')")
    results.append((f"试运行标题='{title2}'", title2 == "试运行结果" and vis2))
    page.screenshot(path=str(OUT / "expand-modal-probe.png"))

    b.close()

print(json.dumps([{"check": k, "ok": v} for k, v in results], ensure_ascii=False, indent=1))
all_ok = all(v for _, v in results)
sys.exit(0 if all_ok else 1)
