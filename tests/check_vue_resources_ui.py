# -*- coding: utf-8 -*-
"""资源汇总页签（#pane-resources）的回归用例。

这一页是唯一一个**不接 AI、不碰仓库**的模块：它只是把需求文档链接与各环境地址
存在 `resources.json` 里，点条目在新页签打开。所以断言只盯三件事：

  ① **点得开**：条目真是 `<a target="_blank" rel="noopener noreferrer">`，
     点下去真的新开一个页签（用 expect_popup 抓，不靠肉眼）；
  ② **存得住**：改完失焦就回存，刷新页面还在（断言走 `/api/resources` 读回的
     内容，而不是只看页面上的字 —— 只看页面就分不清"存了"和"还挂着"）；
  ③ **收得住**：后端只放行 http/https，`javascript:` 这类地址必须被丢掉
     （href 里能执行脚本，这是这一页唯一的安全边界）。

不联网（新页签开的是本机临时实例的 /api/health）、不调模型。
用法：.venv\\Scripts\\python.exe tests/check_vue_resources_ui.py
"""
import json
import sys
import time
import urllib.request
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


def get_board(base):
    with urllib.request.urlopen(base + "/api/resources", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))["board"]


def post_board(base, board):
    req = urllib.request.Request(
        base + "/api/resources",
        data=json.dumps({"board": board}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))["board"]


def wait_board(base, pred, timeout=8.0):
    """等回存落盘。界面是「失焦即存」，存与断言之间没有同步点，只能轮询。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = get_board(base)
        if pred(last):
            return last
        time.sleep(0.2)
    return last


def main() -> int:
    errors = []
    with serve(kb="fixture") as srv:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                    if m.type == "error" else None)

            # 空板开局：先验「第一次进来」的引导态，再看增改
            page.goto(srv.base + "/", wait_until="domcontentloaded")
            # __APP_READY__ 在挂载前就已赋值（= 那个 Promise 本身），等它没用；
            # switchTab 是 App.vue setup 里挂上去的，等它才等于「界面真的起来了」
            page.wait_for_function("() => typeof window.switchTab === 'function'", timeout=20000)
            page.evaluate("() => switchTab('resources')")
            page.wait_for_selector("#pane-resources.active", timeout=8000)

            # ── ① 空板引导 ──
            check("切到资源汇总：pane 激活",
                  page.eval_on_selector("#pane-resources", "el => el.classList.contains('active')"))
            check("空板时出现引导块", page.locator("#res-empty").count() == 1)
            check("引导块给出现成分组名（点一下就有分组）",
                  page.locator("#res-empty .res-chip").count() >= 3,
                  str(page.locator("#res-empty .res-chip").count()))

            page.locator("#res-empty .res-chip").first.click()
            page.wait_for_selector(".res-group", timeout=5000)
            check("点建议分组后出现一张分组卡", page.locator(".res-group").count() == 1)
            check("新分组自动进入编辑态（可以直接加内容）",
                  page.locator(".res-edit, .res-add").count() >= 1)

            # ── ② 新增一条链接：名称 / 地址 / 备注 ──
            page.click(".res-add")
            page.wait_for_selector(".res-edit .el-input__inner", timeout=5000)
            inputs = page.locator(".res-edit .el-input__inner")
            check("编辑态给三格输入（名称 / 地址 / 备注）", inputs.count() == 3, str(inputs.count()))
            inputs.nth(0).fill("订单需求 PRD")
            inputs.nth(0).press("Tab")
            # 故意不带协议头：后端要补成 http://（内网地址最常见的写法）
            inputs.nth(1).fill("order.test.local:8080/prd")
            inputs.nth(1).press("Tab")
            inputs.nth(2).fill("产品在 ONES 里维护的那份")
            inputs.nth(2).press("Tab")

            board = wait_board(srv.base, lambda b: any(g["items"] for g in b["groups"]))
            items = [it for g in board["groups"] for it in g["items"]]
            check("失焦即回存（板上真有了这一条）", len(items) == 1, str(items))
            check("没写协议头的地址被补成 http://",
                  bool(items) and items[0]["url"] == "http://order.test.local:8080/prd",
                  items[0]["url"] if items else "")
            check("名称与备注都存下来了",
                  bool(items) and items[0]["label"] == "订单需求 PRD"
                  and items[0]["note"] == "产品在 ONES 里维护的那份",
                  str(items[0]) if items else "")

            # ── ③ 浏览态：点条目真的新开页签 ──
            page.click("#btn-res-edit")
            page.wait_for_selector(".res-link", timeout=5000)
            check("点「完成」回到浏览态（编辑框收起）",
                  page.locator(".res-edit").count() == 0 and page.locator(".res-link").count() == 1)
            href, target, rel = page.eval_on_selector(
                ".res-link", "el => [el.getAttribute('href'), el.getAttribute('target'), el.getAttribute('rel')]")
            check("条目 href 是补全后的地址", href == "http://order.test.local:8080/prd", str(href))
            check("条目带 target=_blank", target == "_blank", str(target))
            check("条目带 rel=noopener（外部页面拿不到 window.opener）",
                  "noopener" in (rel or ""), str(rel))
            check("地址显示去掉了协议头（一行放得下）",
                  "order.test.local:8080/prd" in page.inner_text(".res-link"),
                  page.inner_text(".res-link"))

            # 换成同源地址再点，避免用例真去连一个不存在的内网域名
            post_board(srv.base, {"groups": [{"id": "g-demo", "label": "环境地址", "items": [
                {"id": "i-demo", "label": "健康检查", "url": srv.base + "/api/health", "note": ""},
            ]}]})
            page.reload(wait_until="domcontentloaded")
            # __APP_READY__ 在挂载前就已赋值（= 那个 Promise 本身），等它没用；
            # switchTab 是 App.vue setup 里挂上去的，等它才等于「界面真的起来了」
            page.wait_for_function("() => typeof window.switchTab === 'function'", timeout=20000)
            page.evaluate("() => switchTab('resources')")
            page.wait_for_selector(".res-link", timeout=8000)
            check("刷新后条目仍在（落盘了，不是只挂在内存里）",
                  page.locator(".res-link").count() == 1,
                  str(page.locator(".res-link").count()))
            with page.expect_popup(timeout=8000) as pop_info:
                page.click(".res-link")
            popup = pop_info.value
            popup.wait_for_load_state("domcontentloaded")
            check("点条目在新页签打开（不是原地跳走）", popup.url.startswith(srv.base + "/api/health"),
                  popup.url)
            check("原页面没被导航走（#pane-resources 仍在）",
                  page.eval_on_selector("#pane-resources", "el => el.classList.contains('active')"))
            popup.close()

            # ── ④ 过滤 ──
            page.fill("#res-filter", "zzz-不存在")
            page.wait_for_timeout(200)
            check("过滤无命中时给出提示", page.locator("#res-nomatch").count() == 1)
            check("过滤无命中时不显示分组卡", page.locator(".res-group").count() == 0,
                  str(page.locator(".res-group").count()))
            page.fill("#res-filter", "健康")
            page.wait_for_timeout(200)
            check("过滤命中时条目还在", page.locator(".res-link").count() == 1)
            page.fill("#res-filter", "")
            page.wait_for_timeout(200)

            # ── ⑤ 编辑态：改名 + 删分组（走 confirm） ──
            page.click("#btn-res-edit")
            page.wait_for_selector(".res-group-input .el-input__inner", timeout=5000)
            page.fill(".res-group-input .el-input__inner", "测试环境")
            page.locator(".res-group-input .el-input__inner").press("Tab")
            board = wait_board(srv.base, lambda b: any(g["label"] == "测试环境" for g in b["groups"]))
            check("分组名改完落盘", any(g["label"] == "测试环境" for g in board["groups"]),
                  str([g["label"] for g in board["groups"]]))

            page.once("dialog", lambda d: d.accept())
            page.click(".res-group-head .res-del")
            board = wait_board(srv.base, lambda b: not b["groups"])
            check("删除分组（有条目时先确认）后板上清空", board["groups"] == [], str(board["groups"]))
            check("清空后回到空板引导", page.locator("#res-empty").count() == 1)

            # ── ⑥ 后端安全边界：只放行 http/https ──
            board = post_board(srv.base, {"groups": [{"id": "g-x", "label": "环境地址", "items": [
                {"id": "i-1", "label": "伪协议", "url": "javascript:alert(1)", "note": ""},
                {"id": "i-2", "label": "数据地址", "url": "data:text/html,<h1>x</h1>", "note": ""},
                {"id": "i-3", "label": "正常地址", "url": "https://example.com/ok", "note": ""},
            ]}]})
            urls = [it["url"] for g in board["groups"] for it in g["items"]]
            check("javascript: / data: 地址被丢掉", "" in urls and "https://example.com/ok" in urls,
                  str(urls))
            check("被丢地址的条目降级成「未填链接」（不静默消失）",
                  any(it["label"] == "伪协议" and not it["url"]
                      for g in board["groups"] for it in g["items"]),
                  str([(it["label"], it["url"]) for g in board["groups"] for it in g["items"]]))

            # host:端口 不能被当成协议头（内网地址最常见的写法，踩过）
            board = post_board(srv.base, {"groups": [{"id": "g-y", "label": "环境地址", "items": [
                {"id": "i-1", "label": "测试环境", "url": "order.test.local:8080/prd", "note": ""},
                {"id": "i-2", "label": "本机", "url": "localhost:3000/x", "note": ""},
            ]}]})
            got = [it["url"] for g in board["groups"] for it in g["items"]]
            check("host:端口 被识别成地址（不是协议头）",
                  got == ["http://order.test.local:8080/prd", "http://localhost:3000/x"], str(got))

            check("无 pageerror", not [e for e in errors if e.startswith("pageerror")],
                  "; ".join(errors)[:200])
            check("无控制台报错", not [e for e in errors if e.startswith("console")],
                  "; ".join(errors)[:200])
            browser.close()

    print(f"\n{'全部通过' if not BAD else '失败 %d 项：%s' % (len(BAD), '；'.join(BAD))}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
