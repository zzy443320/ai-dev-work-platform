# -*- coding: utf-8 -*-
"""用真实浏览器验证唯一界面入口 `/`（Vue 3 外壳）能起来、页签能切、样式生效。

界面已收口：旧版原生 HTML/JS 与 `/v2` 路由都已删除，`GET /` 直接返回 Vue 版。

只做冒烟：起临时实例 → 打开 `/` → 断言八个页签、默认页签、switchTab 可用、
切页签后对应 pane 可见、控制台无报错。不碰仓库、不调模型。
"""
import os
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

from temp_server import serve          # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


def main() -> int:
    errors = []

    with serve() as srv:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                    if m.type == "error" else None)

            page.goto(srv.base + "/", wait_until="networkidle")
            page.wait_for_timeout(600)

            check("Vue 应用已挂载（#app 有子节点）",
                  page.eval_on_selector("#app", "el => el.children.length") > 0)
            # 页签栏已换成 Element 的 el-tabs：选项节点是 .el-tabs__item（文案在内部）。
            # 页签**容器**的契约没变：每个 pane 仍是 #pane-<name> 且活动页带 active 类。
            check("页签渲染出 8 个",
                  page.eval_on_selector_all("#tabs .el-tabs__item", "els => els.length") == 8,
                  str(page.eval_on_selector_all(
                      "#tabs .el-tabs__item", "els => els.map(e => e.innerText.trim())")))
            check("默认停在 stats",
                  page.eval_on_selector("#pane-stats", "el => el.classList.contains('active')"))
            check("switchTab 全局可用（界面用例依赖）",
                  page.evaluate("() => typeof window.switchTab === 'function'"))

            # 切到问答：pane 可见性要跟着变
            page.evaluate("() => switchTab('chat')")
            page.wait_for_timeout(300)
            check("切到问答后 pane-chat 激活",
                  page.eval_on_selector("#pane-chat", "el => el.classList.contains('active')"))
            check("切页签后统计 pane 不再激活",
                  not page.eval_on_selector("#pane-stats", "el => el.classList.contains('active')"))
            # 高亮态由 el-tabs 自己维护（.el-tabs__item.is-active）。
            # 用 eval_on_selector_all 而不是 eval_on_selector：后者在找不到元素时会抛错，
            # 那样断言失败会变成异常，看不出真正的差异。
            active_items = page.eval_on_selector_all(
                "#tabs .el-tabs__item.is-active", "els => els.map(e => e.innerText.trim())")
            check("切页签会同步高亮对应 tab 按钮",
                  any("问答" in t for t in active_items), str(active_items))

            # 流程条只属于缺陷修复页签
            page.evaluate("() => switchTab('stats')")
            page.wait_for_timeout(200)
            check("非缺陷页签不显示流程条",
                  page.eval_on_selector("#stage-flow", "el => getComputedStyle(el).display") == "none")
            page.evaluate("() => switchTab('defect')")
            page.wait_for_timeout(200)
            check("缺陷修复页签显示流程条",
                  page.eval_on_selector("#stage-flow", "el => getComputedStyle(el).display") != "none")

            # 主题切换
            before = page.evaluate("() => document.documentElement.dataset.theme")
            page.click("#theme-toggle")
            page.wait_for_timeout(200)
            after = page.evaluate("() => document.documentElement.dataset.theme")
            check("主题切换生效", before != after, f"{before} → {after}")

            # 样式确实生效（不是裸 HTML）
            bg = page.eval_on_selector(".topbar", "el => getComputedStyle(el).display")
            check("style.css 对 Vue 版生效（.topbar 有布局）", bg in ("flex", "grid", "block"), bg)

            check("无 pageerror", not [e for e in errors if e.startswith("pageerror")],
                  "; ".join(errors)[:200])
            check("无控制台报错", not [e for e in errors if e.startswith("console")],
                  "; ".join(errors)[:200])

            browser.close()

    print(f"\n{'全部通过' if not BAD else '失败 %d 项：%s' % (len(BAD), '；'.join(BAD))}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
