"""Browser test: configure a non-standard relay entirely from the UI, verify it."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_relay  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from ui_select import pick_select  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "ui_settings.json"
BASE = "http://127.0.0.1:8765"
SHOTS = ROOT / "screenshots"
FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def main():
    backup = SETTINGS.read_text(encoding="utf-8") if SETTINGS.exists() else None
    srv, base = fake_relay.start()
    host, port = base.split("//")[1].split(":")
    port = port.split("/")[0]
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True)
            page = br.new_context(viewport={"width": 1500, "height": 980}).new_page()
            errs = []
            posted = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("request", lambda r: posted.append(r.post_data)
                    if "/api/settings" in r.url and r.method == "POST" else None)
            page.goto(BASE, wait_until="networkidle")
            page.evaluate("() => switchTab('extensions')")   # AI 配置在扩展页；默认页签是统计面板
            # 大模型配置自 2026-09-21 起改成独立弹窗（#aimodal 默认 hidden）。
            # 不点开它，后面所有需要"可见"的操作（uncheck / click / is_visible）都会失败。
            page.click("#ai-summary-group button")
            page.wait_for_selector("#aimodal:not(.hidden)")

            print("\n[1] 表单结构")
            check("协议下拉有 3 个选项",
                  page.locator("#cfg-ai-provider option").count() == 3)
            check("鉴方式下拉有 5 个选项",
                  page.locator("#cfg-ai-auth option").count() == 5)
            check("快速模板已加载",
                  page.locator("#cfg-ai-preset option").count() >= 5,
                  f"{page.locator('#cfg-ai-preset option').count()} 项")
            check("默认隐藏自定义鉴权字段名",
                  not page.locator("#ai-auth-header-field").is_visible())

            print("\n[2] 模板自动填充且仍可修改")
            pick_select(page, "#cfg-ai-preset", "relay")
            page.wait_for_timeout(300)
            check("模板填了 Base URL", "your-relay" in page.input_value("#cfg-ai-base"),
                  page.input_value("#cfg-ai-base"))
            check("模板切到 openai 协议", page.input_value("#cfg-ai-provider") == "openai")
            check("模板填了取值路径",
                  page.input_value("#cfg-ai-content-path") == "choices.0.message.content")

            print("\n[3] 手工配一个非标准中转站")
            page.uncheck("#cfg-ai-mock")   # 不开真实调用，后面全是 mock 假通过
            page.click("details.adv > summary")   # 高级区默认折叠，先展开
            page.wait_for_timeout(250)
            check("高级区可展开", page.is_visible("details.adv .adv-body"))
            pick_select(page, "#cfg-ai-preset", "custom")
            page.wait_for_timeout(200)
            page.fill("#cfg-ai-base", f"http://{host}:{port}")
            page.fill("#cfg-ai-endpoint", "/gw/chat")
            page.fill("#cfg-ai-models-path", "/gw/models")
            page.fill("#cfg-ai-content-path", "data.reply")
            pick_select(page, "#cfg-ai-auth", "header")
            page.wait_for_timeout(200)
            check("切到 header 后出现字段名输入框",
                  page.locator("#ai-auth-header-field").is_visible())
            page.fill("#cfg-ai-auth-header", "X-Gw-Token")
            page.fill("#cfg-ai-key", "gw-secret-abcdef123456")
            page.fill("#cfg-ai-model", "gw-claude-pro")
            page.fill("#cfg-ai-temp", "0.2")
            page.fill("#cfg-ai-max-tokens", "1500")
            page.fill("#cfg-ai-headers", "X-Tenant: h3yun")
            page.uncheck("#cfg-ai-json-mode")
            check("高级区字段可填写", page.is_visible("#cfg-ai-content-path"))
            page.screenshot(path=str(SHOTS / "ui_ai_form.png"), full_page=True)

            print("\n[4] 只校验配置（不发请求）")
            page.click("#btn-ai-dry")
            page.wait_for_selector("#ai-test-result:not(.hidden)")
            res = page.inner_text("#ai-test-result")
            check("校验通过", "配置校验通过" in res, res[:120].replace("\n", " | "))
            check("模式不是 mock", "mock" not in res.split("\n")[1], res.split("\n")[:2])
            check("回显真实请求地址", f"{host}:{port}/gw/chat" in res)
            check("密钥掩码显示", "gw-sec" in res and "gw-secret-abcdef123456" not in res,
                  [l for l in res.split("\n") if "Gw" in l][:1])

            print("\n[5] 真实连通性测试")
            page.click("#btn-ai-test")
            page.wait_for_timeout(2500)
            res = page.inner_text("#ai-test-result")
            check("连接成功", "连接成功" in res, res[:150].replace("\n", " | "))
            check("模型回显正确", "收到" in res)
            check("显示耗时", "s" in res.split("连接成功")[-1][:40])
            check("结果面板标绿", page.locator("#ai-test-result.good").count() == 1)
            page.screenshot(path=str(SHOTS / "ui_ai_test_ok.png"), full_page=True)

            print("\n[6] 拉取模型列表")
            page.click("#btn-ai-models")
            page.wait_for_timeout(2000)
            opts = page.locator("#model-presets option").all_inner_texts()
            check("datalist 填入 3 个模型", len(opts) == 3, str(opts))
            check("提示已更新", "3 个模型" in page.inner_text("#cfg-ai-model-hint"),
                  page.inner_text("#cfg-ai-model-hint"))

            print("\n[7] 保存后重载仍然一致")
            page.click("#btn-save")
            page.wait_for_timeout(2000)
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(800)
            check("协议保留 custom", page.input_value("#cfg-ai-provider") == "custom")
            check("Base URL 完整保留",
                  page.input_value("#cfg-ai-base") == f"http://{host}:{port}",
                  page.input_value("#cfg-ai-base"))
            check("取值路径保留", page.input_value("#cfg-ai-content-path") == "data.reply")
            check("鉴权字段名保留", page.input_value("#cfg-ai-auth-header") == "X-Gw-Token")
            check("Key 不回显明文", page.input_value("#cfg-ai-key") == "")
            check("Key 输入框提示已保存并掩码",
                  "已保存" in page.input_value("#cfg-ai-key")
                  or "已保存" in page.get_attribute("#cfg-ai-key", "placeholder"),
                  page.get_attribute("#cfg-ai-key", "placeholder"))
            health = page.inner_text("#health")
            ai_seg = next((s for s in health.split("·") if "AI:" in s), "")
            check("顶栏 AI 段显示真实模型",
                  "mock" not in ai_seg.lower() and "gw-claude-pro" in ai_seg, ai_seg.strip())

            print("\n[8] 配错时能看懂错在哪")
            page.click("details.adv > summary")   # 重载后折叠区又关上了
            page.wait_for_timeout(250)
            page.fill("#cfg-ai-content-path", "data.nope")
            page.click("#btn-ai-test")
            page.wait_for_timeout(2200)
            res = page.inner_text("#ai-test-result")
            check("报出缺字段", "缺少字段" in res, res[:150].replace("\n", " | "))
            check("给出建议改法", "响应取值路径" in res)
            check("面板标红", page.locator("#ai-test-result.bad").count() == 1)
            page.screenshot(path=str(SHOTS / "ui_ai_test_bad.png"), full_page=True)

            print("\n[9] 地址写错时的网络错误可读")
            page.fill("#cfg-ai-content-path", "data.reply")
            page.fill("#cfg-ai-base", "http://127.0.0.1:1")
            page.click("#btn-ai-test")
            page.wait_for_timeout(6000)
            res = page.inner_text("#ai-test-result")
            check("报出连接失败而非静默", "不通" in res and ("失败" in res or "refused" in res.lower()),
                  res[:130].replace("\n", " | "))
            check("错误里带上打的地址", "127.0.0.1:1" in res)

            check("全程无 pageerror", not errs, str(errs[:2])[:200])
            br.close()
    finally:
        srv.shutdown()
        # 恢复测试开始时看到的状态（用户真实配置优先），并核对确实恢复了
        if backup is not None:
            SETTINGS.write_text(backup, encoding="utf-8")
        try:
            state = json.loads(SETTINGS.read_text(encoding="utf-8"))
            a = state.get("ai", {})
            print(f"\n[restore] provider={a.get('provider')} mock={a.get('mock')} "
                  f"base={a.get('base_url', '')!r} key={'已设' if a.get('api_key') else '无'}")
        except Exception as e:
            print("\n[restore] 失败:", e)
    print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
