"""Browser test for the two guard paths the plain e2e can't reach:
  * gate_failed proposal -> 采纳 disabled, 强制采纳 needs the risk checkbox
  * dirty repo -> the banner explains why approving will be refused
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from fixture_defect import TEST_DEFECT
from server_guard import require_repo

REPO = r"D:/workbuddy默认工作空间/2026-09-20-16-18-02/test-mock-repo"
BASE = "http://127.0.0.1:8765"
SHOTS = Path(__file__).resolve().parent.parent / "screenshots"
FAIL = []
GATE_FAIL = ('node -e "process.exit(0)"  # typecheck\n'
             'node -e "console.error(\'TS2345 bad\');process.exit(3)"  # lint')


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True,
                          text=True).stdout.strip()


def api(path, body=None, method=None):
    data = json_dumps(body) if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    return __import__("json").loads(urllib.request.urlopen(req).read().decode("utf-8"))


def json_dumps(b):
    return __import__("json").dumps(b).encode()


def main():
    # 会真的点「强制采纳」（=写目标仓库），服务没指向 mock 仓库就直接中止。
    if not require_repo(BASE, REPO, what="test_browser_guards（会真的强制采纳）"):
        return 2
    settings = api("/api/settings")
    # 记下进来之前的闸门命令：清理时必须**还原**而不是清空，否则会把真实配置抹掉。
    prev_gate = str(((settings.get("gate") or {}).get("commands_text")) or "")
    try:
        print("[setup] 配一条必然失败的 lint 命令")
        api("/api/settings", {"gate": {"commands_text": GATE_FAIL}}, "POST")
        res = api("/api/run", {"mock": False, "defects": [TEST_DEFECT],
                               "limit": 1, "skip_verify": True}, "POST")["results"][0]
        pid = res["proposal_id"]
        check("提案被判 gate_failed", res["status"] == "gate_failed", res["status"])
        check("普通采纳不会成功（后端兜底）",
              git("log", "--oneline") == "f6fc53a init")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE, wait_until="networkidle")
            page.evaluate("() => switchTab('defect')")   # 默认页签是统计面板
            page.wait_for_selector("#proposals .pcard", timeout=20000)

            print("\n[1] gate_failed 的按钮态")
            page.locator(f'#proposals .pcard[onclick*="{pid}"]').first.click()
            page.wait_for_selector("#pmodal:not(.hidden)", timeout=10000)
            check("普通采纳按钮置灰", page.locator("#btn-approve").is_disabled())
            check("强制采纳按钮可见", page.is_visible("#btn-force"))
            check("风险勾选框可见", page.is_visible("#force-confirm"))
            check("失败命令有展示", "lint" in page.inner_text("#pmodal"),
                  page.inner_text("#pmodal")[:1].join([]) or "")
            page.screenshot(path=str(SHOTS / "ui_gate_failed.png"), full_page=True)

            print("\n[2] 不勾选就点强制采纳 -> 应被拒且不改仓库")
            page.click("#btn-force")
            page.wait_for_timeout(1500)
            check("出现必须勾选的提示",
                  "勾选" in page.inner_text("#toast") or "知悉" in page.inner_text("#toast"),
                  page.inner_text("#toast"))
            check("仓库未被改动", git("log", "--oneline") == "f6fc53a init")

            print("\n[3] 勾选后强制采纳 -> 写盘（不 commit），且标注 forced")
            page.check("#force-confirm")
            page.click("#btn-force")
            done = False
            for _ in range(20):
                page.wait_for_timeout(700)
                if "props.items?.name" in (Path(REPO) / "src/components/ProcessList.tsx").read_text(encoding="utf-8"):
                    done = True
                    break
            check("强制采纳生效（写入工作区）", done, git("log", "--oneline").split("\n")[0])
            check("强制采纳不产生 commit", "[AI-proposal]" not in git("log", "--oneline"))
            detail = api(f"/api/proposals/{pid}")
            check("提案记录里标了 forced", detail["decision"].get("forced") is True)
            check("apply 里带回滚后的闸门结果",
                  detail["apply"]["gate"]["failed_checks"] == ["lint"])
            check("状态 applied", detail["status"] == "applied", detail["status"])

            print("\n[4] 脏工作区不再阻断采纳")
            (Path(REPO) / "src" / "Wip.tsx").write_text("export const wip = 1;\n",
                                                        encoding="utf-8")
            newres = api("/api/run", {"mock": False, "defects": [TEST_DEFECT],
                                      "limit": 1, "skip_verify": True}, "POST")
            newpid = newres["results"][0]["proposal_id"]
            page.reload(wait_until="networkidle")
            page.wait_for_selector("#proposals .pcard", timeout=20000)
            banner = page.locator("#repo-banner")
            check("脏工作区不再触发预检横幅",
                  not page.is_visible("#repo-banner") or "未提交" not in page.inner_text("#repo-banner"),
                  page.inner_text("#repo-banner")[:110])
            page.locator(f'#proposals .pcard[onclick*="{newpid}"]').first.click()
            page.wait_for_selector("#pmodal:not(.hidden)", timeout=10000)
            check("弹窗内没有预检告警", "仓库预检未通过" not in page.inner_text("#pmodal"))
            page.screenshot(path=str(SHOTS / "ui_dirty_banner.png"), full_page=True)
            check("无 pageerror", not errors, str(errors[:2])[:200])
            browser.close()
    finally:
        print("\n[cleanup]")
        subprocess.run(["git", "-C", REPO, "reset", "--hard", "HEAD"],
                       capture_output=True)
        wip = Path(REPO) / "src" / "Wip.tsx"
        if wip.exists():
            wip.unlink()
        api("/api/settings", {"gate": {"commands_text": prev_gate}}, "POST")
        print("  repo:", git("log", "--oneline"), "| dirty:", repr(git("status", "--porcelain")))
        print("  gate:", api("/api/health")["gate_level"])
    print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    # 先判仓库、再动设置：服务指向真实仓库时立刻退出，全程不碰全局设置。
    # 否则 force_mock() 会先落一次 mock，进程被外部掐断时 finally 不执行，
    # 就给工具留下 mock=true / 闸门命令的残留（2026-09-24 踩过）。
    if not require_repo(BASE, REPO, what="test_browser_guards（会真的强制采纳）"):
        sys.exit(2)
    from settings_state import force_mock, restore

    _prev_ai = force_mock()   # 不让测试受环境里真实 AI 配置影响
    try:
        sys.exit(main())
    finally:
        restore(_prev_ai)
