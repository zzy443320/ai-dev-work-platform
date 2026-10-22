# -*- coding: utf-8 -*-
"""自测模块（scripts/selftest.py）的纯逻辑自检：不开浏览器、不调模型、不连 ONES。

覆盖的是「判定口径」本身——这些地方错了，界面会给出看起来很专业的错误结论，
比直接报错难查得多：

  - 三态聚合：unknown 不能被算进 pass（这份报告唯一的信任基础）
  - 用例 / 路由解析：手贴文本、ONES 返回的形状各异，洗不出来就不能硬判
  - 需求文档附件：md / txt / pdf 与手写正文的合并顺序、截断口径；
    长文档送模型时**留头也留尾**（验收标准通常在文末）
  - Figma 数字比对：命中 / 字号不符 / 找不到元素三种结果各自的状态
  - 输入全空时必须在开浏览器**之前**就报错（否则会白等一次采证）
  - 产出物落盘：verdicts / counts / environment 必须随产出物存下来

用法：.venv\\Scripts\\python.exe tests/check_selftest.py
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import selftest as st                                  # noqa: E402
from scripts.artifact import ArtifactStore                         # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


class MockAI:
    """只声明 mock，不真发请求：验证「模型不可用时结论必须是 unknown」。"""
    cfg = SimpleNamespace(is_mock=True)
    mode = "mock"

    def complete_text(self, *a, **kw):
        return {"text": "(Mock 模式：无真实输出)", "error": ""}


class StubPage:
    def __init__(self, measured):
        self._measured = measured

    def evaluate(self, js, arg=None):
        return self._measured


def main() -> int:
    ai = MockAI()

    print("链路与路由解析")
    check("normalize_lanes(None) = 三条全开", st.normalize_lanes(None) == list(st.LANES))
    check("normalize_lanes('case,figma') 有序且只留合法值",
          st.normalize_lanes("case,figma") == ["case", "figma"])
    check("normalize_lanes(['all']) = 三条全开", st.normalize_lanes(["all"]) == list(st.LANES))
    check("normalize_lanes 丢弃未知名", st.normalize_lanes(["req", "nope"]) == ["req"])
    routes = st.parse_routes("/, /order/list, /api/x, /static/y")
    check("parse_routes 保留页面路由并剔除 /api /static",
          routes == ["/", "/order/list"], str(routes))
    check("parse_routes 纯自然语言退回默认页", st.parse_routes("订单列表页") == ["/"])
    check("parse_routes 上限 4 个", len(st.parse_routes("/a,/b,/c,/d,/e,/f")) == 4)
    hash_route = "/admin/#/ai-workflow-designer/zyy443320"
    check("哈希路由整体保留（SPA 真实页面常在 # 后）",
          st.parse_routes(hash_route) == [hash_route], str(st.parse_routes(hash_route)))

    print("用例清洗")
    raw = [
        {"name": "订单列表分页", "steps": ["打开 /order/list", "点击「下一页」"],
         "expected": "列表刷新到第 2 页"},
        {"title": "", "steps": "", "expected": ""},
        "不是字典",
        {"summary": "提交失败回滚", "step": "点击「提交」", "result": "按钮恢复可点"},
    ]
    cases = st._normalize_cases(raw)
    check("丢弃没有名称的项", len(cases) == 2, str([c["title"] for c in cases]))
    check("步骤列表拼成多行文本", "下一页" in cases[0]["steps"])
    check("从步骤里抽出路由", cases[0]["route"] == "/order/list", cases[0]["route"])
    check("兼容 step/result 字段", cases[1]["expected"] == "按钮恢复可点")
    check("id 连续编号", [c["id"] for c in cases] == ["C1", "C2"])

    text_cases = st._parse_cases_text(ai, "1. 空列表显示占位图\n2. 提交后 toast")
    check("Mock 模式下手贴文本退回「一行一条」", len(text_cases) == 2, str(len(text_cases)))
    check("删掉行首编号", text_cases[0]["title"] == "空列表显示占位图", text_cases[0]["title"])

    print("需求文档附件（md / txt / pdf → 与手写正文合并）")
    from scripts.chat_attachments import ChatAttachmentStore, merge_doc_text
    with tempfile.TemporaryDirectory() as tmp:
        store = ChatAttachmentStore(tmp)
        md = store.save("prd.md", "# 需求\n下单页要显示预计送达时间".encode("utf-8"),
                        "text/markdown")
        txt = store.save("补充.txt", "验收：空列表显示占位图".encode("utf-8"), "text/plain")
        merged = merge_doc_text(store, "只改接口，别动页面结构。", [md, txt])
        check("手写正文排在最前（约束不该淹没在 PRD 模板话里）",
              merged.startswith("只改接口"), merged[:24])
        check("附件正文各带来源标题", "prd.md" in merged and "补充.txt" in merged)
        check("手写 + 两份附件的正文都在",
              all(k in merged for k in ("只改接口", "预计送达时间", "占位图")))
        check("空正文 + 空附件 = 空串", merge_doc_text(store, "", []) == "")
        check("只有附件时也能拼出正文", "预计送达时间" in merge_doc_text(store, "", [md]))
        clipped = merge_doc_text(store, "开头\n" + "填" * 200 + "\n结尾", [], max_chars=50)
        check("超长保留尾部并注明截断（验收标准常在文末）",
              clipped.startswith("…") and clipped.endswith("结尾"), clipped[:40])

        # 长文档送模型时的窗口：只截前 14000 字符会把文末的验收标准丢掉
        class EchoAI:
            cfg = SimpleNamespace(is_mock=False)
            mode = "real"

            def __init__(self, manual=False):
                self.prompt = ""
                self._manual = manual

            def complete_text(self, prompt, system=None, **kw):
                self.prompt = prompt
                verify = ',"verify":"manual"' if self._manual else ""
                return {"text": '{"points":[{"id":"R1","title":"显示预计送达",'
                                '"expected":"页面上出现预计送达时间"' + verify + '}]}'}

        echo = EchoAI()
        long_doc = ("开头：背景与目标\n" + "中间填充。" * 3000
                    + "\n结尾：验收标准 A1 显示预计送达时间")
        points_long = st._split_req_points(echo, long_doc)
        check("长文档送模型时保留开头", "背景与目标" in echo.prompt)
        check("长文档送模型时保留结尾", "验收标准 A1" in echo.prompt)
        check("模型拆出的验收点被采用",
              bool(points_long) and points_long[0]["id"] == "R1", str(points_long[:1]))

        # 2026-10-21：工具拼的元信息行不能变成验收点，章节标题不能当验收点
        check("送模型的正文剥掉附件来源标注",
              "用户上传的 PDF" not in echo.prompt, echo.prompt[:200])
        dirty = ("### 附件 |PRD+-+AI工作流.pdf|（用户上传的 PDF，抽取正文 3253 字符）\n"
                 "PRD-D: AI工作流支持消息通知节点\n"
                 "## 业务背景\n"
                 "左侧节点面板存在「消息通知」节点\n"
                 "标准通知动作：\n"
                 "…（需求正文超过 60000 字符，已截断；原始文档仍保留在附件里）…")
        check("元信息行被剥掉",
              st._strip_tool_notes(dirty)
              == "PRD-D: AI工作流支持消息通知节点\n## 业务背景\n"
                 "左侧节点面板存在「消息通知」节点\n标准通知动作：",
              st._strip_tool_notes(dirty))
        check("标题/元信息/空标题行判为文档残留",
              all(st._is_doc_junk(t) for t in (
                  "### 附件 |PRD+-+AI工作流.pdf|（用户上传的 PDF，抽取正文 3253 字符）",
                  "## 业务背景", "标准通知动作：", "验收标准：",
                  "…（需求正文超过 60000 字符，已截断）")),
              "")
        check("正常验收点不算残留",
              not st._is_doc_junk("左侧节点面板存在「消息通知」节点"))
        pts_manual = st._split_req_points(EchoAI(manual=True), dirty)
        check("模型标 manual 的验收点保留 verify=manual",
              pts_manual and pts_manual[0].get("verify") == "manual",
              str(pts_manual[:1]))
        check("默认验收点 verify=page",
              pts_manual and st._split_req_points(EchoAI(), dirty)[0].get("verify") == "page")

    # 截断 JSON 容错：reasoning 与 content 共享 max_tokens 的模型会把输出掐断，
    # 修复器要能捞回已完成的条目，而不是整批「未做模型判读」。
    truncated = ('{"results":[{"id":"R1","status":"pass","reason":"a"},'
                 '{"id":"R2","status":"unknown","reason":"b"},{"id":"R3","sta')
    repaired = st._json_from_text(truncated)
    check("截断 JSON 捞回已完成条目",
          isinstance(repaired, dict) and len(repaired.get("results") or []) == 2
          and repaired["results"][1]["id"] == "R2", str(repaired))
    check("完整 JSON 不受修复器影响",
          st._json_from_text('{"results":[{"id":"R1","status":"pass"}]}')
          .get("results")[0]["status"] == "pass")
    check("无 JSON 返回 None", st._json_from_text("抱歉我无法输出") is None)

    print("Figma 数字比对")
    nodes = [{"text": "提交", "width": 80, "height": 32, "color": "#333333",
              "fontSize": 14, "fontWeight": 500},
             {"text": "取消", "width": 80, "height": 32, "color": "#333333",
              "fontSize": 14, "fontWeight": 500}]
    page_info = {"route": "/order/list", "screenshot": ""}
    same = st._compare_figma(StubPage({
        "提交": {"w": 80, "h": 32, "fontSize": 14, "fontWeight": "500",
                 "color": "rgb(51, 51, 51)"},
        "取消": {"w": 80, "h": 32, "fontSize": 16, "fontWeight": "500",
                 "color": "rgb(51, 51, 51)"},
    }), page_info, nodes)
    by_text = {v["id"]: v for v in same["verdicts"]}
    check("字号/字重/颜色/宽度一致 → pass", by_text["提交"]["status"] == st.STATUS_PASS,
          by_text["提交"]["reason"])
    check("字号不符 → fail", by_text["取消"]["status"] == st.STATUS_FAIL,
          by_text["取消"]["reason"])
    missing = st._compare_figma(StubPage({}), page_info, nodes)
    check("页面上找不到元素 → unknown 而不是 pass",
          all(v["status"] == st.STATUS_UNKNOWN for v in missing["verdicts"]))
    check("checked 只数真正比过的",
          st._compare_figma(StubPage({}), page_info, nodes)["checked"] == 0)

    print("空白页诊断（结论带事实、建议只在 notes 出现一次）")
    blank_page = {"blank": True, "chars": 0, "text": "",
                  "blank_reason": "可见文本只有 0 字符，页面重定向到了 http://x/login"
                                  "（停在登录页且没有配置页面验证账号）。"
                                  "常见原因：①「检查路由」不是真实页面路径；② 需要登录。"}
    req_pts = [{"id": "R1", "title": "显示预计送达", "expected": "页面上出现预计送达时间"}]
    judged, _ = st._judge_req(ai, blank_page, req_pts)
    check("空白页结论记 unknown", judged["R1"]["status"] == st.STATUS_UNKNOWN)
    check("结论带事实（重定向到哪）", "重定向到" in judged["R1"]["reason"],
          judged["R1"]["reason"])
    check("结论不复读「常见原因」（建议只进 notes）",
          "常见原因" not in judged["R1"]["reason"], judged["R1"]["reason"])
    judged_c, _ = st._judge_cases(ai, blank_page,
                                  [{"id": "C1", "title": "t", "steps": "", "expected": ""}])
    check("用例链路的空白结论同样带事实", "重定向到" in judged_c["C1"]["reason"],
          judged_c["C1"]["reason"])

    print("三态聚合")
    payload = {"verdicts": [
        {"lane": "case", "id": "C1", "title": "a", "status": st.STATUS_PASS},
        {"lane": "case", "id": "C2", "title": "b", "status": st.STATUS_FAIL,
         "reason": "列表没刷新"},
        {"lane": "req", "id": "R1", "title": "c", "status": st.STATUS_UNKNOWN,
         "reason": "页面没有依据"},
        {"lane": "figma", "id": "d", "title": "d", "status": "莫名其妙的取值"},
    ], "notes": []}
    out = st._finalize(payload)
    check("未知状态计入 unknown", out["counts"] == {"pass": 1, "fail": 1, "unknown": 2},
          str(out["counts"]))
    check("小结同时报三种结论", "不通过 1" in out["summary"] and "无法判断 2" in out["summary"],
          out["summary"])
    check("checklist 里不通过排在待确认之前",
          out["checklist"][0].startswith("【不通过】"), str(out["checklist"][:2]))
    check("plan 文本包含判定纪律", "无法判断" in out["plan"])

    print("输入不足与外部依赖缺失")
    try:
        st.run(ai, {"lanes": ["case", "req", "figma"], "url": "http://127.0.0.1:1"},
               {"ones": {}, "figma": {}, "playwright": {}})
        check("三条链路全空必须在开浏览器前报错", False, "没有抛 SelfTestError")
    except st.SelfTestError:
        check("三条链路全空必须在开浏览器前报错", True)
    got = st.fetch_ones_cases("100003", {})
    check("ONES 未配置时如实说明而不是抛异常", "未配置" in got["note"], got["note"])
    fig = st._figma_specs({"figma": {}}, "https://www.figma.com/design/abc/def")
    check("Figma Token 缺失时返回可读原因", fig["ok"] is False and "Token" in fig["note"],
          fig["note"])

    print("产出物落盘")
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        art = store.create("selftest", "自测 100003", {
            "summary": "共检查 2 项", "verdicts": [{"lane": "req", "status": "unknown"}],
            "counts": {"pass": 0, "fail": 0, "unknown": 1}, "lanes": [{"lane": "req"}],
            "environment": {"url": "http://localhost:3000"}, "notes": ["n"],
        }, {})
        saved = json.loads((Path(tmp) / f"{art['id']}.json").read_text(encoding="utf-8"))
        check("verdicts 随产出物落盘", saved.get("verdicts"), str(saved.get("verdicts")))
        check("counts 落盘为 verdict_counts", saved.get("verdict_counts", {}).get("unknown") == 1)
        check("environment 落盘", saved.get("environment", {}).get("url"))
        check("type_label 是「自测」", saved.get("type_label") == "自测", saved.get("type_label"))

    print(f"\n{len(OK)} 项通过，{len(BAD)} 项失败")
    if BAD:
        print("失败项：" + "；".join(BAD))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
