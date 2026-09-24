"""UI 检查：新增的「Figma 取稿通道」与「页面验证登录」是否真的渲染出来、能联动。

走真实浏览器（Playwright，线程内调用），断言的是 DOM 事实，不是「代码看起来对」。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8765"
FAILED = []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


try:
    from playwright.sync_api import sync_playwright
except ImportError as e:
    print(f"playwright 未安装，跳过: {e}")
    sys.exit(0)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.goto(BASE, wait_until="networkidle")
    time.sleep(1.5)
    page.locator('.tab[data-tab="reqdev"]').click()
    time.sleep(0.8)

    print("== 1. 需求开发页：取稿通道分段控件 ==")
    check("分段控件存在", page.locator("#req-figma-mode").count() == 1)
    check("两个按钮", page.locator("#req-figma-mode .seg-btn").count() == 2)
    check("默认选中 Token",
          "active" in (page.locator("#req-figma-mode .seg-btn").first.get_attribute("class") or ""))
    check("Token 输入框初始可见", page.locator("#req-figma-token-field").is_visible())
    check("通道说明文案存在", page.locator("#req-figma-hint").count() == 1)
    hint = page.locator("#req-figma-hint").inner_text()
    check("说明里点明只认 Token", "Token" in hint and "账号密码" in hint, hint[:60])

    print("== 2. 切到「浏览器打开」→ Token 框隐藏 ==")
    page.locator("#req-figma-mode .seg-btn").nth(1).click()
    time.sleep(0.4)
    check("Token 输入框已隐藏", not page.locator("#req-figma-token-field").is_visible())
    check("按钮切换为 active",
          "active" in (page.locator("#req-figma-mode .seg-btn").nth(1).get_attribute("class") or ""))
    saved = page.evaluate("() => localStorage.getItem('figma-fetch-mode')")
    check("通道选择被记住", saved == "browser", str(saved))

    print("== 3. 切回 Token → 输入框恢复 ==")
    page.locator("#req-figma-mode .seg-btn").first.click()
    time.sleep(0.4)
    check("Token 输入框恢复可见", page.locator("#req-figma-token-field").is_visible())

    print("== 4. 配置面板：Figma 说明 + 页面验证登录组 ==")
    page.locator('.tab[data-tab="defect"]').click()   # 配置面板在所有页签下都在左栏
    time.sleep(0.6)
    check("配置面板可见", page.locator("#settings-panel").is_visible())
    page.locator("#cfg-pagelogin-enabled").scroll_into_view_if_needed()
    time.sleep(0.5)
    check("页面登录开关存在", page.locator("#cfg-pagelogin-enabled").count() == 1)
    check("页面登录账号框存在", page.locator("#cfg-pagelogin-email").count() == 1)
    check("页面登录密码框存在", page.locator("#cfg-pagelogin-password").count() == 1)
    check("开关默认未勾选", not page.locator("#cfg-pagelogin-enabled").is_checked())
    ph = page.locator("#cfg-pagelogin-password").get_attribute("placeholder") or ""
    check("密码框提示不是「已保存」（当前未配置）",
          "已保存" not in ph and "登录密码" in ph, ph)
    figma_hint = page.locator("text=Figma 官方接口只有 Token 一种凭据").count()
    check("Figma 说明点明不支持账号密码", figma_hint == 1)
    check("新样式 .field-check 生效",
          page.evaluate("() => getComputedStyle(document.querySelector('#cfg-pagelogin-enabled').closest('label')).flexDirection") == "row")

    print("== 5. 无 JS 报错 ==")
    errs = page.evaluate("() => window.__errs || []")
    check("页面无未捕获错误", not errs, str(errs))

    browser.close()

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + "; ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
