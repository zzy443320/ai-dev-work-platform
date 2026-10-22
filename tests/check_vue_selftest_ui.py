# -*- coding: utf-8 -*-
"""自测页签（#pane-selftest）的界面断言。

只锁「页签能用、判定表真的会出来」这条最小闭环：
  - 页签可切换、面板与关键控件 id 都在（参数由 schema 渲染，id 是契约）
  - 判定表空态走 `:empty` 占位（与 #test-preview / #figma-tree 同一套 CSS 契约）
  - 跑一次 Mock 自测（环境指向临时服务自己）→ 出现判定行 + 三态徽标，
    且模型不可用时结论必须是「无法判断」而不是「通过」

为什么用临时服务而不是你本机的 8765：那里可能正被代理或你的真实配置占着，
用例既可能读错数据，也可能写进你的 artifacts/。

用法：.venv\\Scripts\\python.exe tests/check_vue_selftest_ui.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

from playwright.sync_api import sync_playwright      # noqa: E402

import temp_server as TS                            # noqa: E402
# 手搓最小可解析 PDF 的同一个工具（有文字层 → 能被抽出），不重复造一份
from check_req_attachments import make_pdf          # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


def main() -> int:
    with TS.serve() as srv:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page_errors = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(srv.base + "/", wait_until="networkidle")
            # 页签头在两种布局里都被 CSS 藏了，只能等「挂载」不能等「可见」
            page.wait_for_selector("#tabs .el-tabs__item", state="attached")
            page.evaluate("() => window.switchTab('selftest')")
            page.wait_for_selector("#pane-selftest.active")
            page.wait_for_timeout(400)

            print("页签与控件")
            tabs_text = page.eval_on_selector("#tabs", "el => el.textContent")
            check("自测页签出现在页签条里", "自测" in tabs_text)
            check("面板存在", page.locator("#st-panel").count() == 1)
            # switch-group 不是一个控件：三个子开关各自带 options[].id（schema 契约）
            for sel in ("#btn-selftest", "#st-btn-cases", "#st-btn-doc", "#st-att-input",
                        "#st-verdicts", "#st-issue", "#st-lane-case", "#st-lane-req",
                        "#st-lane-figma", "#st-doc", "#st-url"):
                check(f"控件 {sel} 在", page.locator(sel).count() >= 1)
            check("判定表空态没有渲染出子节点",
                  page.eval_on_selector("#st-verdicts", "el => el.children.length") == 0)
            check("没上传文档时不渲染附件清单",
                  page.locator("#st-atts").count() == 0)

            print("上传需求文档（txt → 与手贴正文合并）")
            doc = srv.root / "prd.txt"
            doc.write_text("验收点：页面顶部有主导航\n验收点：列表为空时显示占位提示",
                           encoding="utf-8")
            # 直接喂原生 file input：不用点按钮再走系统文件选择器
            page.set_input_files("#st-att-input", str(doc))
            page.wait_for_selector("#st-atts .st-att", timeout=60000)
            att_text = page.inner_text("#st-atts")
            check("附件出现在清单里", page.locator("#st-atts .st-att").count() == 1)
            check("清单显示文件名", "prd.txt" in att_text, att_text[:80])
            check("清单显示文件大小", "B" in att_text, att_text[:80])
            page.click("#st-atts .st-att-view")
            page.wait_for_selector("#st-att-preview", timeout=10000)
            check("「看解析」展开出正文",
                  "主导航" in page.inner_text("#st-att-preview"))

            # PDF 才是真实场景（PRD 常常是一份 pdf）：文字层在上传那一刻就被抽出来，
            # 清单里能直接看到「抽出 N 字符」——这份字符数是 PDF 才有的。
            pdf = srv.root / "prd.pdf"
            pdf.write_bytes(make_pdf("Acceptance A1 show delivery time"))
            page.set_input_files("#st-att-input", str(pdf))
            page.wait_for_selector("#st-atts .st-att:nth-child(2)", timeout=60000)
            att_text = page.inner_text("#st-atts")
            check("PDF 也进了清单并抽出文字", "prd.pdf" in att_text and "字符" in att_text,
                  att_text[:120])
            check("两份附件同时在清单里",
                  page.locator("#st-atts .st-att").count() == 2)

            print("跑一次 Mock 自测")
            page.fill("#st-url", srv.base)
            page.fill("#st-routes", "/")
            page.fill("#st-doc", "1. 页面能打开\n2. 页面有标题")
            page.click("#btn-selftest")
            page.wait_for_selector("#st-verdicts tbody tr", timeout=180000)
            rows = page.locator("#st-verdicts tbody tr").count()
            check("判定表出现行", rows >= 1, f"{rows} 行")
            badges = page.eval_on_selector_all(
                "#st-verdicts .st-badge", "els => els.map(e => e.textContent.trim())")
            check("徽标只有三种取值", all(b in ("通过", "不通过", "无法判断") for b in badges),
                  str(badges))
            check("Mock 下结论不可能是「通过」（模型没跑就没有证据）",
                  "通过" not in badges, str(badges))
            check("小结与计数条都在",
                  page.locator("#st-summary").count() == 1
                  and page.locator("#st-counts").count() == 1)
            env_text = page.inner_text("#st-env")
            check("环境摘要显示的是本次地址", srv.base in env_text, env_text[:80])
            # 过程区：跑完要有采证截图缩略条（自测浏览器实际看到的页面）
            shots = page.locator("#st-shots .st-shot").count()
            check("过程区有采证截图缩略条", shots >= 1, f"{shots} 张")
            check("过程区截图指向 /screenshots 静态服务",
                  page.eval_on_selector(
                      "#st-shots .st-shot img",
                      "el => (el.getAttribute('src')||'').startsWith('/screenshots/')"))
            # 附件正文由服务端在运行时合并：报告里必须能看出「判的是哪几份文档」
            notes_text = page.inner_text("#st-notes")
            check("报告记下了这次用的需求文档",
                  "需求文档：" in notes_text and "prd.pdf" in notes_text,
                  notes_text[:120])

            # 第二次运行：指向一个**真的空白页**（本地起一个只回空 HTML 的端口）。
            # 用户最常见的质疑就是「我本地明明是好的」——所以空白时必须给出
            # 诊断（停在哪个 URL、可见文本多少、截图在哪），并在环境摘要里亮红灯。
            print("跑一次指向空白页的自测")
            import http.server
            import threading

            class _Blank(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body></body></html>")

                def log_message(self, *a):  # 静音
                    pass

            blank_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Blank)
            threading.Thread(target=blank_srv.serve_forever, daemon=True).start()
            try:
                page.fill("#st-url", f"http://127.0.0.1:{blank_srv.server_port}")
                page.fill("#st-routes", "/")
                page.click("#btn-selftest")
                # 旧一轮的判定行还挂着，wait_for_selector 会立即返回读到陈旧结果；
                # 必须等「环境摘要出现空白警告」——那只会来自本轮的新结果
                page.wait_for_function(
                    "() => (document.querySelector('#st-env')||{}).textContent"
                    " && document.querySelector('#st-env').textContent.includes('空白')",
                    timeout=180000)
                page.wait_for_timeout(400)
                env_text = page.inner_text("#st-env")
                check("环境摘要亮出空白警告与落点", "空白" in env_text, env_text[:120])
                check("空白警告带截图入口", "看工具看到的截图" in env_text, env_text[:120])
                reasons = page.eval_on_selector_all(
                    "#st-verdicts .st-reason", "els => els.map(e => e.textContent)")
                check("空白结论写明工具看到的事实",
                      reasons and all("页面空白" in r for r in reasons), str(reasons[:1]))
                check("空白结论不复读「常见原因」（建议只进 notes）",
                      all("常见原因" not in r for r in reasons), str(reasons[:1]))
                notes_text2 = page.inner_text("#st-notes")
                check("notes 里有空白诊断（含常见原因建议）",
                      "空白诊断" in notes_text2 and "常见原因" in notes_text2,
                      notes_text2[:160])
                check("Mock 下空白页不可能判「通过」",
                      "通过" not in page.eval_on_selector_all(
                          "#st-verdicts .st-badge",
                          "els => els.map(e => e.textContent.trim())"))
            finally:
                blank_srv.shutdown()

            shot = srv.root / "selftest.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"  截图：{shot}")
            check("页面没有抛未捕获异常", not page_errors, "；".join(page_errors[:3]))
            browser.close()

    print(f"\n{len(OK)} 项通过，{len(BAD)} 项失败")
    if BAD:
        print("失败项：" + "；".join(BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
