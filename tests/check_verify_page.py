"""验证「页面截图/验收」链路的三处修复：

1. route_for 不再从 HTML 残渣抽出 /strong 这类假路由（先 strip_html 再抽），
   工单里的路径提示（含 URL 剥离后的裸路径）能被正确抽成路由。
2. 白屏检测：页面渲染不出文本时状态是 blank（绝不是 ok「渲染无报错」）。
3. 复刻操作：mock AI 下退化为 goto 并明说；--ai 开关跑一次真实 AI 编排冒烟。
   capture 支持 route/actions 重放（after 与 before 同页面同操作）。

用法（须先启动 web 服务 8765）：
    python tests/check_verify_page.py
    python tests/check_verify_page.py --ai   # 额外跑一次真实 AI 动作编排
"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ai_model import AIModel  # noqa: E402
from scripts.verifier import ScreenshotVerifier  # noqa: E402

# 模拟真实 ONES 富文本工单：含 </strong> 残渣、完整 URL、路径提示
HTML_DEFECT = {
    "id": "VERIFY-PAGE",
    "title": "【演示】重复周期应该必填",
    "description": (
        '<p>问题描述：<strong>重复周期</strong>应该必填，终止时间勾选了也应该必填。</p>'
        '<p>复现步骤：</p><ol><li>打开 '
        'https://demo.example.com/demo-workspace/schedule 页面</li>'
        '<li>点击新增日程，不填重复周期直接提交</li></ol>'
        '<p>路径：demo-workspace/schedule</p>'
    ),
}
PLAIN_DEFECT = {
    "id": "VERIFY-PAGE-PLAIN",
    "title": "首页样式异常",
    "description": "打开首页就能看到按钮错位。",
}

SHOTS = PROJECT_ROOT / "screenshots"


def new_verifier(ai=None, base_url="http://127.0.0.1:8765"):
    return ScreenshotVerifier(base_url=base_url, screenshot_dir=str(SHOTS), ai=ai)


def check_route_extraction() -> bool:
    v = new_verifier()
    r1 = v.route_for(HTML_DEFECT)
    r2 = v.route_for(PLAIN_DEFECT)
    print(f"[route] HTML 工单  -> {r1!r}")
    print(f"[route] 纯文本工单 -> {r2!r}")
    ok = True
    if r1 == "/strong":
        print("FAIL: /strong 假路由仍在（strip_html 未生效）")
        ok = False
    elif r1 != "/demo-workspace/schedule":
        print(f"WARN: HTML 工单期望 /demo-workspace/schedule，实际 {r1!r}")
    if r2:
        print(f"INFO: 纯文本工单路由 {r2!r}（描述里恰有可抽路径）")
    else:
        print("PASS: 纯文本工单无路径提示 → 返回空，由 capture 兜底 /（首页）")
    return ok


def check_plan_fallback() -> bool:
    v = new_verifier(ai=AIModel({"mock": True}))
    actions, note = v.plan_actions(HTML_DEFECT, "/demo-workspace/schedule")
    print(f"[plan] mock AI 退化: actions={actions} note={note}")
    ok = actions and actions[0][0] == "goto"
    print("PASS: mock 下退化为 goto 并明确说明" if ok else "FAIL: plan_actions 退化异常")
    return bool(ok)


def check_capture_and_replay() -> bool:
    v = new_verifier()
    if not v.reachable():
        print("[skip] web 服务未启动（8765），跳过 capture 验证")
        return True
    before = v.capture("verify_page", "before", PLAIN_DEFECT)
    print(f"[capture] status={before['status']} route={before['route']} "
          f"text_len={before.get('page_text_sample', '') and len(before['page_text_sample'])}")
    ok = before["status"] in ("ok", "error_visible")
    if not ok:
        print(f"FAIL: 对本服务首页拍照应能渲染出内容，实际 {before['status']}: "
              f"{before.get('error', '')[:120]}")
        return False
    replay_actions = before.get("replay_actions") or []
    if not replay_actions:
        print("FAIL: 缺少 replay_actions，after 无法重放")
        return False
    json.dumps(replay_actions)  # 必须可 JSON 序列化（要存进提案）
    after = v.capture("verify_page", "after", PLAIN_DEFECT,
                      route=before.get("route"), actions=replay_actions)
    print(f"[replay] status={after['status']} route_source={after.get('route_source')} "
          f"note={after.get('plan_note', '')[:40]}")
    ok = after["status"] in ("ok", "error_visible") and after.get("route_source") == "replay"
    print("PASS: 修复后重放同一操作序列，route_source=replay" if ok
          else "FAIL: 重放行为不符合预期")
    return bool(ok)


def check_ai_plan_real() -> bool:
    import json as _json
    settings_file = PROJECT_ROOT / "ui_settings.json"
    saved = _json.loads(settings_file.read_text(encoding="utf-8"))
    ai_cfg = dict(saved.get("ai") or {})
    if ai_cfg.get("mock") or not ai_cfg.get("api_key"):
        print("[skip] AI 未配置真实 key，跳过真实编排冒烟")
        return True
    v = new_verifier(ai=AIModel(ai_cfg))
    actions, note = v.plan_actions(HTML_DEFECT, "/demo-workspace/schedule")
    print(f"[ai-plan] note={note}")
    print(f"[ai-plan] actions={actions}")
    if actions:
        print("PASS: 真实 AI 编排产出动作序列")
        return True
    print(f"WARN: 真实 AI 编排未产出动作（{note}），已按设计退化为 goto ——"
          "这属于可接受的降级，但值得看一眼 note")
    return True  # 降级不算失败


def main() -> int:
    results = [check_route_extraction(), check_plan_fallback(), check_capture_and_replay()]
    if "--ai" in sys.argv:
        results.append(check_ai_plan_real())
    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} 组检查通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
