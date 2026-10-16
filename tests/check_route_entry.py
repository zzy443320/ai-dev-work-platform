r"""回归：复现/验收路由不许丢掉「入口段」（工单 215890 验收侧复盘）。

工单 215890 的复现地址是

    https://test-develop-mysql.cloudpivot.cn/mobile#/agent-workspace/schedule

旧 `route_for` 只认 `#/` **之后**的部分，返回 `/agent-workspace/schedule`，
于是 before/after 两张截图拍的都是**桌面端**的同一个页面 —— 那一页根本没有
「选不了模型」这个缺陷，两图必然一致，验收结论永远是「看不出来 / 现象未变」：
**哪怕补丁写对了也会被判成没修好**，白烧一轮返工。

`/mobile` 这个入口段决定这套前端加载哪一份页面壳，必须一起带上。
顺带把「只给完整 URL、不给裸路径」的工单也救回来：`strip_html` 现在保留
协议+域名，`_ROUTE_RE` 的 `(?<![\w:/])` 会被域名尾字挡住、`_BARE_PATH_RE`
又不肯从斜杠后起 match，于是这类工单过去抽出的是**空路由**（退化成拍首页）。

纯离线，不需要服务与私有仓库。用法：python tests/check_route_entry.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verifier import ScreenshotVerifier  # noqa: E402

_PASS = 0
_FAIL = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [ok]   {label}" + (f" — {detail[:110]}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {detail[:220]}" if detail else ""))


def route(url: str = "", **kw) -> str:
    defect = {k: v for k, v in kw.items()}
    if url:
        defect["url"] = url
    return ScreenshotVerifier.route_for(defect)


def main() -> int:
    print("[1] 入口段 + hash 路由：必须整段保留（215890 的死点）")
    t215890 = "https://test-develop-mysql.cloudpivot.cn/mobile#/agent-workspace/schedule"
    r = route(t215890)
    ok(r == "/mobile/#/agent-workspace/schedule",
       "移动端工单取到带入口段的路由", repr(r))
    ok(r != "/agent-workspace/schedule",
       "不再退化成桌面端页（旧行为会让验收永远「看不出来」）")
    ok(route("https://x.cn/admin/#/demo-skill-log") == "/admin/#/demo-skill-log",
       "admin/#/ 形态同样保留入口段")
    ok(route("https://x.cn/app/mobile#/task/list") == "/app/mobile/#/task/list",
       "多段入口（app/mobile）整段保留")
    ok(route("https://x.cn/#/demo-skill-log") == "/#/demo-skill-log",
       "没有入口段时给根 hash 路由（/ 而不是裸段）")

    print("\n[2] 只有完整 URL 的工单也要抽得出路由（以前是空串 → 拍首页）")
    ok(route("http://localhost:9100/agent-workspace/schedule") == "/agent-workspace/schedule",
       "完整 URL 的 path 段被抽出", repr(route("http://localhost:9100/agent-workspace/schedule")))
    ok(route("https://x.cn/app/application-list/vd_customer") == "/app/application-list/vd_customer",
       "多段 path 完整保留")
    ok(route("https://x.cn/app/list?tid=1#top") == "/app/list",
       "查询串与锚点被剥掉（存进提案的 route 必须可直接 goto）")

    print("\n[3] 原有契约不许回退")
    html_defect = {
        "title": "【演示】重复周期应该必填",
        "description": ('<p>问题描述：<strong>重复周期</strong>应该必填。</p>'
                        '<p>复现步骤：</p><ol><li>打开 '
                        'https://demo.example.com/demo-workspace/schedule 页面</li></ol>'
                        '<p>路径：demo-workspace/schedule</p>'),
    }
    r = ScreenshotVerifier.route_for(html_defect)
    ok(r != "/strong", "HTML 残渣不会被抽成假路由", repr(r))
    ok(r == "/demo-workspace/schedule", "HTML 工单仍抽出正确路由", repr(r))
    ok(route(description="路径：demo-workspace/schedule") == "/demo-workspace/schedule",
       "正文裸路径形态仍可用")
    ok(route(description="页面定位：admin/#/demo-skill-log，点击新增") == "/admin/#/demo-skill-log",
       "正文里的裸 hash 路由形态可用")

    print("\n[4] 抽不出、以及抽到非页面资源时，如实返回空（由 capture 兜底 /）")
    ok(route(title="首页样式异常", description="打开首页就能看到按钮错位。") == "",
       "没有任何路径提示 → 空串")
    ok(route("https://x.cn/#/api/user/list") == "",
       "接口路径不是页面路由（/api/ 跳过）")
    ok(route(route="/agent-workspace/schedule") == "/agent-workspace/schedule",
       "显式 route 字段优先")

    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
