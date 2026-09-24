# -*- coding: utf-8 -*-
"""UI 验证：运行按钮走 /api/run/stream 的实时视图（Playwright + 路由拦截）。

不调真实 AI、不跑流水线：用 page.route 伪造 SSE 响应，验证
① .log 区域放大到 420px；② live 助手函数的 DOM 行为（阶段行/思考/输出流式块）；
③ 点「运行」消费 SSE 后渲染最终结果 + 回放折叠面板；④ 步骤条随最终数据点亮；
⑤ 按钮状态恢复。需要服务在 8765 运行（仅为了加载页面与静态资源）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def sse(events):
    return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)


EVENTS = [
    {"type": "stage", "stage": "fetch", "status": "start", "detail": "正在拉取 ONES 工单…"},
    {"type": "stage", "stage": "fetch", "status": "done", "detail": "拉取到 1 条工单（来源 injected）"},
    {"type": "defect", "index": 1, "total": 1, "id": "UI-TEST-1", "title": "【UI流式】重复周期必填"},
    {"type": "stage", "stage": "locate", "status": "start", "defect": "UI-TEST-1", "detail": "关键词抽取 + git grep 扫描…"},
    {"type": "ai_delta", "defect": "UI-TEST-1", "kind": "reasoning", "text": "用户说重复周期应该必填，"},
    {"type": "ai_delta", "defect": "UI-TEST-1", "kind": "reasoning", "text": "需要找 schedule-editor 里的校验逻辑。"},
    {"type": "ai_delta", "defect": "UI-TEST-1", "kind": "content", "text": '{"root_cause": "canSubmit 未校验 repeat 必填", '},
    {"type": "ai_delta", "defect": "UI-TEST-1", "kind": "content", "text": '"category": "逻辑"}'},
    {"type": "stage", "stage": "locate", "status": "done", "defect": "UI-TEST-1", "detail": "定位到 2 个嫌疑文件"},
    {"type": "stage", "stage": "patch", "status": "done", "defect": "UI-TEST-1", "detail": "补丁构建成功：1 个块"},
    {"type": "stage", "stage": "gate", "status": "done", "defect": "UI-TEST-1", "detail": "验收闸门 level=degraded ok=True"},
    {"type": "stage", "stage": "proposal", "status": "done", "defect": "UI-TEST-1", "detail": "提案 demo-1 已生成（状态 pending）"},
    {"type": "stage", "stage": "kb", "status": "done", "defect": "UI-TEST-1", "detail": "知识卡片已沉淀"},
    {"type": "done", "payload": {
        "count": 1, "ai_mode": "openai", "wrote_any_files": False,
        "results": [{
            "proposal_id": "demo-1", "id": "UI-TEST-1", "title": "【UI流式】重复周期必填",
            "category": "逻辑", "locate_empty": False, "status": "pending",
            "root_cause": "canSubmit 未校验 repeat 必填", "files": ["x.vue"],
            "gate_level": "degraded", "gate_ok": True, "errors": [], "warnings": [],
            "verify_status": "skipped", "kb_path": "kb.md", "source": "injected",
        }],
    }},
]
SSE_BODY = sse(EVENTS)

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8765"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    page.goto(BASE, wait_until="domcontentloaded")
    page.evaluate("() => switchTab('defect')")   # 默认页签是统计面板
    page.wait_for_selector("#btn-run")

    # ① 运行结果区域放大
    mh = page.evaluate("() => getComputedStyle(document.querySelector('#run-log')).maxHeight")
    check("运行结果区域 max-height=420px", mh == "420px", mh)

    # ①b 空态就占满 420px（避免出结果后布局跳动），占位提示居中可见
    init_h = page.evaluate("() => document.querySelector('#run-log').clientHeight")
    check("初始高度即 420px（空态保留完整区域）", 415 <= init_h <= 425, str(init_h))
    ph = page.evaluate("() => getComputedStyle(document.querySelector('#run-log'), '::before').content")
    check("空态占位提示存在", ph and ph != "none" and ph != "normal", str(ph)[:60])

    # ② live 助手函数 DOM 行为（等 healthCache 完整就绪——ai_model 有值，模型名才有值）
    page.wait_for_function("() => !!(healthCache && healthCache.ai_model)", timeout=20000)
    page.evaluate("""() => {
      liveRefs = liveSetup(document.querySelector('#run-log'));
      liveLine('正在拉取 ONES 工单…', 'dim');
      liveLine('拉取到 1 条工单', 'ok');
      liveLine('拉取失败', 'bad');
      liveAI('reasoning', '思考增量一;');
      liveAI('reasoning', '思考增量二;');
      liveAI('content', '{"root_cause":"x"}');
    }""")
    n_lines = page.evaluate("() => document.querySelectorAll('#run-log .live-line').length")
    check("实时阶段行渲染（3 行）", n_lines == 3, str(n_lines))
    think_visible = page.evaluate(
        "() => !document.querySelector('#run-log .ai-block.think').classList.contains('hidden')")
    out_visible = page.evaluate(
        "() => !document.querySelector('#run-log .ai-block.out').classList.contains('hidden')")
    think_text = page.evaluate("() => document.querySelector('#run-log .ai-block.think pre').textContent")
    out_text = page.evaluate("() => document.querySelector('#run-log .ai-block.out pre').textContent")
    check("思考块可见且累积", think_visible and think_text == "思考增量一;思考增量二;", think_text)
    check("输出块可见且累积", out_visible and out_text == '{"root_cause":"x"}', out_text)
    live_meta = page.evaluate("() => document.querySelector('#run-log .ai-stream-meta').textContent.trim()")
    check("输出区显示模型名", bool(live_meta), live_meta)

    # ②b 思考块溢出后应自动跟随到最新输出（用户没上翻时）
    page.evaluate("""() => {
      for (let i = 0; i < 60; i++) liveAI('reasoning', '这是一段很长的思考增量内容用来撑爆滚动区' + i + '。');
    }""")
    at_bottom = page.evaluate("""() => {
      const pre = document.querySelector('#run-log .ai-block.think pre');
      return {overflow: pre.scrollHeight > pre.clientHeight + 4,
              bottom: Math.abs(pre.scrollTop + pre.clientHeight - pre.scrollHeight) < 4};
    }""")
    check("思考块溢出且自动滚到最新", at_bottom["overflow"] and at_bottom["bottom"],
          str(at_bottom))
    # 用户上翻阅读后，继续追加不应强行拽回底部
    page.evaluate("""() => {
      const pre = document.querySelector('#run-log .ai-block.think pre');
      pre.scrollTop = 0;
      liveAI('reasoning', '用户上翻后新增的一段。');
    }""")
    stayed = page.evaluate("""() => {
      const pre = document.querySelector('#run-log .ai-block.think pre');
      return pre.scrollTop < 4;
    }""")
    check("用户上翻时不强制拽回底部", stayed)

    # ③ 拦截 /api/run/stream 伪造完整 SSE，点「运行」走真实前端逻辑
    page.route("**/api/run/stream", lambda route: route.fulfill(
        status=200, content_type="text/event-stream", body=SSE_BODY))
    page.click("#btn-run")
    page.wait_for_selector("#run-log .log-html", timeout=15000)
    check("最终结果渲染（log-html）", True)
    row_text = page.evaluate("() => document.querySelector('#run-log .run-row').textContent")
    check("结果行含工单号与根因", "UI-TEST-1" in row_text and "canSubmit" in row_text, row_text[:80])
    check("回放折叠面板存在", page.evaluate(
        "() => !!document.querySelector('#run-log .ai-replay')"))
    page.evaluate("() => document.querySelector('#run-log .ai-replay').open = true")
    rp_think = page.evaluate("() => { const el = document.querySelector('#run-log .ai-replay .ai-block.think pre'); return el ? el.textContent : ''; }")
    rp_out = page.evaluate("() => { const el = document.querySelector('#run-log .ai-replay .ai-block.out pre'); return el ? el.textContent : ''; }")
    check("回放含思考全文", "重复周期应该必填" in rp_think and "校验逻辑" in rp_think, rp_think[:60])
    check("回放含输出全文", '"category": "逻辑"}' in rp_out, rp_out[:60])

    # ④ 步骤条随最终数据点亮（全部成功 → 5 个 done）
    done_stages = page.evaluate("() => document.querySelectorAll('.stage.done').length")
    check("步骤条 5 步全部 done", done_stages == 5, str(done_stages))

    # ⑤ 状态与按钮恢复（Promise.all 刷新面板完成后按钮才解禁，显式等待）
    status_text = page.evaluate("() => document.querySelector('#status').textContent")
    check("状态显示产出提案数", "产出 1 个提案" in status_text, status_text)
    page.wait_for_function("() => !document.querySelector('#btn-run').disabled", timeout=20000)
    check("运行按钮恢复可用", True)
    expand_visible = page.evaluate(
        "() => !document.querySelector('#btn-run-expand').classList.contains('hidden')")
    check("放大按钮可见（有内容）", expand_visible)

    browser.close()

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} 项未通过 -> {FAILS}")
    sys.exit(1)
print("PASS: 实时运行 UI 全部通过")
