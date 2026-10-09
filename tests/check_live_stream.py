# -*- coding: utf-8 -*-
"""验证实时流式链路（/api/run/stream + complete_stream + SSE 前端契约）。

Part A 单元级：AIModel.complete(on_delta=...) 真实网关流式冒烟——
              必须收到 reasoning/content 增量且最终 JSON 可解析。
Part B 端到端：POST /api/run/stream 注入工单（skip_verify），
              断言 SSE 事件序列：fetch→defect→ai_delta→locate→…→done。
Part C 兼容：老接口 /api/run 仍然可用（一次性契约不被破坏）。
Part D 试运行流式：POST /api/probe/stream（locate:false 不走 AI）
              ——防线是 pipeline.probe 签名漏 emit 这类回归（表现为 fatal 事件）。

⚠ Part B/C/D **一律打在 temp_server 起的临时实例上，不打开发者正在用的 8765**。
原先它们硬编码 http://127.0.0.1:8765，代价有两个：① 注入的合成工单
（STREAM-TEST-1 / COMPAT-TEST-1）会往真实 `proposals/` 与 `knowledge_base/` 落
提案和卡片，跑一次积一份，混在待审批列表里分不清真假；② SSE 客户端被杀或脚本
超时退出时，服务端工作线程仍会把整条流水线跑完（真实 AI 要几分钟），期间所有端点
409——把别人正在用的槽位占死。临时实例的全部数据目录都在系统临时目录里，退出即销毁。

为了让 B/C/D 仍然测到真东西，临时实例**透传真实 AI 配置与仓库路径**（ai_delta 的
plumbing 只有真模型 + 真仓库才出得来），同时关掉 agentic 修复循环、清空闸门：
本用例断言的是事件序列与契约字段，不是"补丁对不对"，没必要为此花 8 分钟跑沙箱。

用法：
    PYTHONUTF8=1 python tests/check_live_stream.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/：temp_server

import requests  # noqa: E402
import temp_server  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- Part A
print("== Part A: AIModel 流式冒烟（真实网关） ==")
from scripts.ai_model import AIModel  # noqa: E402

settings = json.loads((ROOT / "ui_settings.json").read_text(encoding="utf-8"))
model = AIModel(dict(settings.get("ai") or {}))
if model.cfg.is_mock:
    print("  [SKIP] AI 是 mock 模式，跳过真实流式冒烟")
else:
    deltas = {"reasoning": 0, "content": 0}
    result = model.complete(
        "分析这个缺陷并给出 JSON：页面按钮点击后列表不刷新。只输出 JSON。",
        max_tokens=2000,
        on_delta=lambda kind, text: deltas.__setitem__(kind, deltas[kind] + len(text or "")),
    )
    check("流式收到思考增量", deltas["reasoning"] > 0, f"reasoning_chars={deltas['reasoning']}")
    check("流式收到输出增量", deltas["content"] > 0, f"content_chars={deltas['content']}")
    check("最终结果可解析（有 root_cause 或 error 字段结构）",
          isinstance(result, dict) and ("root_cause" in result),
          f"keys={sorted(result.keys())[:8]}")

# ------------------------------------------------------ 临时实例的透传配置
ISOLATED = {
    # 真模型才有流式增量，真仓库才有关键词可 grep —— 这两条是 Part B 的价值所在
    "ai": dict(settings.get("ai") or {}),
    "ones": dict(settings.get("ones") or {}),
    "repo": dict(settings.get("repo") or {}),
    # 但不跑 agentic 沙箱循环、不跑 npm 闸门：测的是事件序列，不是补丁对错
    "agent": {"enabled": False},
    "gate": {"commands_text": "", "degraded": True},
    "playwright": {"base_url": "http://127.0.0.1:1", "headless": True},
}

DEFECT_B = {
    "id": "STREAM-TEST-1",
    "title": "【流式验证】重复周期应该必填",
    "description": "日程组件 demo-workspace/schedule 的重复周期字段没有校验必填，"
                   "终止时间勾选了也应该必填。",
}
DEFECT_C = {
    "id": "COMPAT-TEST-1",
    "title": "【兼容验证】列表不刷新",
    "description": "提交后列表不刷新，demo-workspace/schedule 页面。",
}


def sse(base, path, payload, timeout=300):
    """POST-SSE：返回 (content_type, 事件列表)。"""
    events = []
    with requests.post(base + path, json=payload, stream=True, timeout=timeout) as resp:
        ctype = resp.headers.get("content-type", "")
        print("  HTTP", resp.status_code, ctype)
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            body = raw[5:].strip()
            if body:
                try:
                    events.append(json.loads(body))
                except ValueError:
                    pass
    return ctype, events


with temp_server.serve(settings=ISOLATED) as srv:
    BASE = srv.base
    health = srv.api("/api/health")
    mock_ai = health.get("ai_mode") == "mock"
    print(f"\n临时实例 {BASE}（AI={health.get('ai_mode')} · "
          f"仓库{'已透传' if health.get('repo_exists') else '不可用'} · "
          "数据目录=系统临时目录，退出即销毁）")

    # ------------------------------------------------------------ Part B
    print("\n== Part B: /api/run/stream 端到端（注入工单 + 跳过截图） ==")
    ctype, events = sse(BASE, "/api/run/stream",
                        {"defects": [DEFECT_B], "skip_verify": True, "limit": 1})
    check("SSE Content-Type", "text/event-stream" in ctype, ctype)

    types = [e.get("type") for e in events]
    check("收到 stage 事件", "stage" in types)
    check("收到 defect 事件", "defect" in types)
    fetch_done = [e for e in events if e.get("type") == "stage"
                  and e.get("stage") == "fetch" and e.get("status") == "done"]
    check("fetch 阶段完成事件（注入来源）", bool(fetch_done))
    deltas = [e for e in events if e.get("type") == "ai_delta"]
    if not mock_ai:
        check("收到 ai_delta（思考或输出）", bool(deltas),
              "真实 AI 模式下必须能观察到流式增量")
        check("思考增量存在", any(e.get("kind") == "reasoning" for e in deltas))
    done = [e for e in events if e.get("type") == "done"]
    check("收到 done 事件", len(done) == 1)
    if done:
        payload = done[0].get("payload") or {}
        check("done.payload.count == 1", payload.get("count") == 1, str(payload.get("count")))
        rows = payload.get("results") or []
        check("结果行 id 一致", bool(rows) and rows[0].get("id") == "STREAM-TEST-1",
              str(rows[:1]))
        check("前端契约字段齐全", bool(rows) and all(
            k in rows[0] for k in ("proposal_id", "status", "files", "gate_ok", "kb_path")))
    check("跑完占用标记自动释放（不留 busy）",
          srv.api("/api/health").get("running") is False)

    # ------------------------------------------------------------ Part C
    print("\n== Part C: 旧接口 /api/run 兼容 ==")
    resp = requests.post(BASE + "/api/run",
                         json={"defects": [DEFECT_C], "skip_verify": True, "limit": 1},
                         timeout=300)
    check("/api/run HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
    data = resp.json()
    check("/api/run 返回 count/results", data.get("count") == 1 and bool(data.get("results")))
    check("/api/run 返回 stopped（被停止时结果不是全集）", "stopped" in data,
          str(sorted(data.keys())))

    # ------------------------------------------------------------ Part D
    print("\n== Part D: /api/probe/stream 端到端（locate=false） ==")
    ctype, events = sse(BASE, "/api/probe/stream", {"limit": 1, "locate": False}, timeout=180)
    check("probe SSE Content-Type", "text/event-stream" in ctype, ctype)
    types = [e.get("type") for e in events]
    check("probe 无 fatal 事件（签名/桥接没有回归）", "fatal" not in types,
          next((e.get("error") for e in events if e.get("type") == "fatal"), ""))
    fetch_starts = [e for e in events if e.get("type") == "stage"
                    and e.get("stage") == "fetch" and e.get("status") == "start"]
    check("probe 收到 fetch start", bool(fetch_starts))
    done = [e for e in events if e.get("type") == "done"]
    check("probe 收到 done 事件", len(done) == 1)
    if done:
        payload = done[0].get("payload") or {}
        check("probe payload 结构完整", all(
            k in payload for k in ("count", "source", "results")),
            str(sorted(payload.keys()))[:120])
        check("probe done 是终态事件后连接收尾", types[-1] == "done",
              f"last_types={types[-3:]}")

    # 老接口 /api/probe 兼容（同样曾因签名漏 emit 而 NameError 500）
    resp = requests.post(BASE + "/api/probe", json={"limit": 1, "locate": False}, timeout=180)
    check("/api/probe HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
    pdata = resp.json()
    check("/api/probe 返回 count/source/results", all(
        k in pdata for k in ("count", "source", "results")), str(pdata)[:160])

    # 合成工单只该落在临时目录：真实 proposals/ 不能再被喂进 STREAM / COMPAT
    leaked = sorted(p.name for p in (ROOT / "proposals").glob("*TEST-*"))
    check("没有往开发者真实数据目录写合成工单", not leaked, str(leaked[:3]))

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} 项未通过 -> {FAILS}")
    sys.exit(1)
print("PASS: 实时流式链路全部通过")
