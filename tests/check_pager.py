# -*- coding: utf-8 -*-
"""Verify proposals pagination + fake-data cleanup (UI only)."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(r"C:\Users\27409\Desktop\ones-defect-auto-fixer\.workbuddy")
results = []


def pick_filter(page, value):
    """#proposal-filter 被包装成自定义下拉（原生 select 被隐藏），
    所以走 select._csSync() 同步 + 派发 change 事件，与真实点击等效。"""
    page.evaluate(
        """(val) => {
            const sel = document.querySelector('#proposal-filter');
            sel.value = val;
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            if (sel._csSync) sel._csSync();
        }""",
        value,
    )

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1600, "height": 1100})
    page.goto("http://127.0.0.1:8765", wait_until="load")
    page.evaluate("() => switchTab('defect')")   # 默认页签是统计面板，分页器在缺陷修复页
    # 等首屏真实数据真正落地再断言：固定 sleep 会竞态——晚到的 /api/proposals
    # 响应会把下面注入的假数据冲掉，断言就随机红。
    page.wait_for_selector("#proposals .pcard", timeout=20000)

    # 1) real data loaded: 真实提案（条数会随日常使用累积，只断言「不是假数据」）
    real_count = page.evaluate("() => proposalsCache.length")
    results.append((f"真实提案数={real_count}（>0 且无假数据）", real_count > 0))
    real_ids = page.eval_on_selector_all(
        "#proposals .pcard .pcard-id", "els => els.map(e => e.textContent)")
    fake_left = [i for i in real_ids if i.startswith("X") or "DEF-" in i]
    results.append((f"假数据已清（未混入 {len(fake_left)} 条）", not fake_left))

    # 1.5) 回归：卡片必须按时间倒序渲染（updated 优先、created 兜底），最新的在最前。
    #      曾经先按状态分组再按时间——8 分钟前 gate_failed 的提案被压到两天前
    #      pending 的后面，列表时间忽新忽旧。这里让页面自己算一遍时间序，与 DOM 对账。
    expect_ids = page.evaluate(
        """() => {
            const sel = document.querySelector('#proposal-filter');
            const f = sel ? sel.value : '';
            const open = s => s === 'pending' || s === 'gate_failed';
            let list = proposalsCache.slice();
            if (f === 'pending') list = list.filter(p => open(p.status) || p.status === 'apply_failed');
            else if (f) list = list.filter(p => p.status === f);
            list.sort((a, b) =>
                String(b.updated || b.created || '').localeCompare(String(a.updated || a.created || '')));
            return list.map(p => p.defect_id || p.id);
        }"""
    )
    results.append((f"卡片按时间倒序（DOM 前 {len(real_ids)} = 时间序前 {len(real_ids)}）",
                    list(real_ids) == list(expect_ids[:len(real_ids)])))
    pager_info = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent") if page.query_selector(".prop-pager .pg-info") else "(无分页器)"
    results.append((f"真实数据分页信息='{pager_info}'", str(real_count) in pager_info))

    # 2) inject 30 fake cache entries -> 4 pages
    page.evaluate(
        """
        () => {
          const mk = (i) => ({
            id: 'X' + i, defect_id: 'DEF-' + i, title: '测试提案 ' + i,
            status: 'pending', gate_level: 'degraded', gate_ok: true,
            category: '逻辑', priority: 'P' + (i % 3), ai_mode: 'openai',
            // 单页容量固定为 8 -> 30 条 = 8 + 8 + 8 + 6，这里只断言「不重复 / 条数与分页器一致」
            // 排序在状态相同时按 updated 倒序，条数由分页器给出，不做跨页位置强绑定
            files: ['a.vue'], added: 1, removed: 0,
            updated: '2026-09-22T10:00:' + String(i % 60).padStart(2, '0'),
            created: '2026-09-22T10:00:00',
            root_cause: '第 ' + i + ' 条测试根因',
          });
          proposalsCache = Array.from({length: 30}, (_, i) => mk(i + 1));
          propPage = 1;
          renderProposals();
        }
        """
    )
    page.wait_for_timeout(200)
    cards = page.eval_on_selector_all("#proposals .pcard", "els => els.length")
    results.append((f"每页卡片数={cards}（应为 8）", cards == 8))
    info = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
    results.append((f"第 1 页信息='{info}'", "1-8" in info and "30" in info))
    active = page.eval_on_selector(".pg-num.active", "el => el.textContent")
    results.append((f"高亮页码={active}", active == "1"))

    page.screenshot(path=str(OUT / "pager-page1.png"))

    ids_p1 = page.eval_on_selector_all("#proposals .pcard .pcard-id", "els => els.map(e => e.textContent)")
    # 注入的 30 条状态相同、updated 秒数 = i，时间倒序即 X30 最前 -> 第 1 页是 DEF-30..DEF-23
    results.append((f"同状态注入数据严格按时间倒序={ids_p1}",
                    ids_p1 == [f"DEF-{i}" for i in range(30, 22, -1)]))

    # 3) next page（注意：这是 30 条注入数据下的最后一页按钮）
    page.click(".prop-pager .pg-btns button:last-child")
    page.wait_for_timeout(200)
    ids_p2 = page.eval_on_selector_all("#proposals .pcard .pcard-id", "els => els.map(e => e.textContent)")
    info2 = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
    results.append((f"翻到第 2 页 卡片数={len(ids_p2)} 信息='{info2}' 无重复={len(set(ids_p1) & set(ids_p2)) == 0}",
                    len(ids_p2) == 8 and "9-16" in info2 and not (set(ids_p1) & set(ids_p2))))

    # 4) jump to page 4 by number button
    page.evaluate("() => gotoPropPage(4)")
    page.wait_for_timeout(200)
    ids_p4 = page.eval_on_selector_all("#proposals .pcard .pcard-id", "els => els.map(e => e.textContent)")
    info3 = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
    next_disabled = page.eval_on_selector(".prop-pager .pg-btns button:last-child", "el => el.disabled")
    prev_enabled = page.eval_on_selector(".prop-pager .pg-btns button:first-child", "el => !el.disabled")
    results.append((f"第 4 页 卡片数={len(ids_p4)} 信息='{info3}' 下一页禁用={next_disabled}",
                    len(ids_p4) == 6 and "25-30" in info3 and next_disabled and prev_enabled))

    # 5) filter switch resets to page 1
    #    先制造「当前在第 3 页」的状态，再切筛选，断言 page 已被重置
    page.evaluate("() => { propPage = 3; renderProposals(); }")
    page.wait_for_timeout(150)
    before = page.eval_on_selector(".pg-num.active", "el => el.textContent")
    pick_filter(page, "applied")          # 30 条测试数据里没有 applied -> 空列表
    page.wait_for_timeout(200)
    empty_el = page.query_selector("#proposals .empty")
    empty_text = empty_el.text_content() if empty_el else "(无空提示)"
    sel_val = page.eval_on_selector("#proposal-filter", "el => el.value")
    reset_value = page.evaluate("() => propPage")
    results.append((f"切换筛选前 active={before}，切到 applied 后 propPage={reset_value}", reset_value == 1))
    results.append((f"applied 筛选下 select.value='{sel_val}' 空提示='{empty_text.strip()[:12]}'",
                    sel_val == "applied" and empty_el is not None))

    # 回到「全部」，确认停在 reset 后的第 1 页
    pick_filter(page, "")
    page.wait_for_timeout(200)
    active2 = page.eval_on_selector(".pg-num.active", "el => el.textContent")
    info4 = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
    # 仍在 30 条注入数据下：应回到第 1 页，且信息恢复为 1-8
    results.append((f"切回全部后 页码={page.evaluate('() => propPage')} active={active2} 信息='{info4}'",
                    active2 == "1" and "1-8" in info4))

    # 6) 还原真实数据，确认假数据已清、只剩真实提案
    page.evaluate("() => { proposalsCache = []; propPage = 1; renderProposals(); }")
    page.reload(wait_until="load")
    page.evaluate("() => switchTab('defect')")   # 刷新回到默认页签
    page.wait_for_selector("#proposals .pcard", timeout=20000)
    real_n = page.evaluate("() => proposalsCache.length")
    real_info = page.eval_on_selector(".prop-pager .pg-info", "el => el.textContent")
    results.append((f"重载后真实提案数={real_n}（与初始 {real_count} 一致）分页信息='{real_info}'",
                    real_n == real_count and str(real_n) in real_info))

    page.screenshot(path=str(OUT / "pager-page4.png"))
    b.close()

print(json.dumps([{"check": k, "ok": v} for k, v in results], ensure_ascii=False, indent=1))
sys.exit(0 if all(v for _, v in results) else 1)
