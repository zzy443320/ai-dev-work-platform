"""Browser end-to-end: load UI, open a proposal, approve it, undo it.

前置：服务在 :8765，且指向 **mock 仓库**（`python tests/make_mock_repo.py --set-server`）。
mock 仓库的路径由 tests/mock_repo.py 统一解析，不再写死本机路径。
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

import mock_repo
from fixture_defect import TEST_DEFECT
from server_guard import require_repo
from ui_select import pick_select

REPO = str(mock_repo.path())
BASE = "http://127.0.0.1:8765"
SHOTS = Path(__file__).resolve().parent.parent / "screenshots"
FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True,
                          text=True).stdout.strip()


def api(path, method=None, body=None):
    import json
    data = json.dumps(body or {}).encode() if method else None
    req = urllib.request.Request(BASE + path, data=data, method=method or "GET",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read().decode("utf-8"))


def main():
    # 本用例会真的采纳提案（=写目标仓库）。服务没指向 mock 仓库就直接中止，
    # 否则测试补丁会落进真实工作区，而且断言还会全红。
    if not require_repo(BASE, REPO, what="test_browser_e2e（会真的采纳提案）"):
        return 2
    print("repo before:", git("log", "--oneline"), "| dirty:", repr(git("status", "--porcelain")))
    fresh = api("/api/run", "POST", {"mock": False, "defects": [TEST_DEFECT],
                                     "limit": 1, "skip_verify": True})
    pid = fresh["results"][0]["proposal_id"]
    print("fresh proposal:", pid, fresh["results"][0]["status"])

    console = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}")
                if m.type in ("error",) else None)
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

        print("\n[1] 打开界面")
        page.goto(BASE, wait_until="networkidle")
        page.evaluate("() => switchTab('defect')")   # 默认页签是统计面板
        page.wait_for_selector("#proposals .pcard", timeout=20000)
        check("提案卡片渲染", page.locator("#proposals .pcard").count() > 0,
              f"{page.locator('#proposals .pcard').count()} 张")
        check("健康条含分支与闸门层级", mock_repo.BRANCH in page.inner_text("#health"),
              page.inner_text("#health")[:130])
        check("计数徽标有内容", page.inner_text("#proposal-count").strip() != "",
              page.inner_text("#proposal-count"))
        check("闸门层级有徽标",
              page.locator("#proposals .gate-badge").count() > 0,
              f'{page.locator("#proposals .gate-badge").count()} 个徽标, '
              f'degraded={page.locator("#proposals .gate-badge.gate-degraded").count()}')
        page.screenshot(path=str(SHOTS / "ui_overview.png"), full_page=True)

        print("\n[2] 筛选")
        pick_select(page, "#proposal-filter", "pending")
        page.wait_for_timeout(400)
        cards = page.locator("#proposals .pcard")
        n = cards.count()
        classes = page.locator("#proposals .pcard").evaluate_all(
            "els => els.map(e => e.className)")
        check("筛后只剩可复审状态", n > 0 and all(
            any(k in c for k in ("pc-pending", "pc-gate_failed", "pc-apply_failed"))
            for c in classes), f"{n} 张")
        pick_select(page, "#proposal-filter", "")
        page.wait_for_timeout(400)

        print("\n[3] 打开新提案")
        page.locator(f'#proposals .pcard[onclick*="{pid}"]').first.click()
        page.wait_for_selector("#pmodal:not(.hidden)", timeout=10000)
        check("详情弹窗打开", page.is_visible("#pmodal"))
        txt = page.inner_text("#pmodal")
        check("有根因", "根因" in txt)
        check("有 SEARCH/REPLACE 原文", "SEARCH" in txt.upper())
        check("有闸门区块", "闸门" in txt)
        check("有预防建议", "预防" in txt)
        colored = page.locator("#pmodal .dl.add, #pmodal .dl.del").count()
        check("diff 逐行着色", colored >= 2, f"{colored} 行")
        check("新增行文本正确",
              "props.items?.name" in page.locator("#pmodal .dl.add").first.inner_text())
        check("按钮组齐备",
              all(page.locator(s).count() == 1
                  for s in ("#btn-approve", "#btn-reject", "#btn-force")))
        page.screenshot(path=str(SHOTS / "ui_proposal_modal.png"), full_page=True)

        print("\n[4] UI 采纳")
        page.fill("#pm-note", "browser e2e")
        page.click("#btn-approve")
        approved = False
        for _ in range(20):
            page.wait_for_timeout(700)
            if "props.items?.name" in (Path(REPO) / "src/components/ProcessList.tsx").read_text(encoding="utf-8"):
                approved = True
                break
        check("点击采纳后写入工作区", approved, git("log", "--oneline").split("\n")[0])
        check("采纳不产生 commit", "[AI-proposal]" not in git("log", "--oneline")
              and git("log", "--oneline").count("\n") == 0)
        body = (Path(REPO) / "src/components/ProcessList.tsx").read_text(encoding="utf-8")
        check("只有目标行被改", "props.items?.name" in body and "return <div>" in body)
        check("采纳备注进提案", "browser e2e" in
              str(api(f"/api/proposals/{pid}").get("decision", {})))
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOTS / "ui_after_approve.png"), full_page=True)

        print("\n[5] 重复采纳被拦")
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#proposals .pcard", timeout=20000)
        page.locator(f'#proposals .pcard[onclick*="{pid}"]').first.click()
        page.wait_for_selector("#pmodal:not(.hidden)", timeout=10000)
        check("已采纳后出现撤销按钮", page.locator("#btn-undo").count() == 1
              and page.is_visible("#btn-undo"))
        check("已采纳后采纳/拒绝按钮被隐藏",
              not page.is_visible("#btn-approve") and not page.is_visible("#btn-reject"),
              f'approve visible={page.is_visible("#btn-approve")}')
        page.screenshot(path=str(SHOTS / "ui_applied.png"), full_page=True)

        print("\n[6] UI 撤销")
        page.on("dialog", lambda d: d.accept())
        page.click("#btn-undo")
        reverted = False
        for _ in range(15):
            page.wait_for_timeout(700)
            orig_now = (Path(REPO) / "src/components/ProcessList.tsx").read_text(encoding="utf-8")
            if "props.items?.name" not in orig_now and "props.items.name" in orig_now:
                reverted = True
                break
        check("撤销后文件恢复原样", reverted)
        check("撤销不产生 commit", "[AI-proposal]" not in git("log", "--oneline"))
        orig = (Path(REPO) / "src/components/ProcessList.tsx").read_text(encoding="utf-8")
        check("文件内容复原", "props.items.name" in orig and "?." not in orig)

        print("\n[7] 主题与控制台")
        page.evaluate("toggleTheme()")
        page.wait_for_timeout(400)
        check("切到 light", page.evaluate("document.documentElement.dataset.theme") == "light")
        page.screenshot(path=str(SHOTS / "ui_light.png"), full_page=True)
        print("\nconsole:", console or "无错误")
        check("页面无 console 错误", not console, str(console[:3])[:300])
        browser.close()

    print("\nrepo after:", git("log", "--oneline"), "| dirty:", repr(git("status", "--porcelain")))
    print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    # 先判仓库、再动设置：服务指向真实仓库时立刻退出，全程不碰全局设置。
    # 否则 force_mock() 会先落一次 mock，进程被外部掐断时 finally 不执行，
    # 就给工具留下 mock=true 的残留（2026-09-24 踩过）。
    if not require_repo(BASE, REPO, what="test_browser_e2e（会真的采纳提案）"):
        sys.exit(2)
    # 闸门通过后才动手：确保 mock 仓库就绪且干净（幂等，只碰 mock 仓库）
    mock_repo.reset()
    from settings_state import force_mock, restore

    _prev_ai = force_mock()   # 不让测试受环境里真实 AI 配置影响
    try:
        sys.exit(main())
    finally:
        restore(_prev_ai)
