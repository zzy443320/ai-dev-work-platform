# -*- coding: utf-8 -*-
"""验证实时流式链路（/api/run/stream + complete_stream + SSE 前端契约）。

Part A 单元级：AIModel.complete(on_delta=...) 真实网关流式冒烟——
              必须收到 reasoning/content 增量且最终 JSON 可解析。
Part B 端到端：服务在跑时，POST /api/run/stream 注入工单（skip_verify），
              断言 SSE 事件序列：fetch→defect→ai_delta→locate→…→done。
Part C 兼容：老接口 /api/run 仍然可用（一次性契约不被破坏）。
Part D 试运行流式：POST /api/probe/stream（locate:false 不走 AI，真实拉取）
              ——防线是 pipeline.probe 签名漏 emit 这类回归（表现为 fatal 事件）。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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

# ---------------------------------------------------------------- Part B
print("== Part B: /api/run/stream 端到端（注入工单 + 跳过截图） ==")
import requests  # noqa: E402

BASE = "http://127.0.0.1:8765"
try:
    health = requests.get(f"{BASE}/api/health", timeout=5).json()
    server_up = bool(health.get("ok"))
except Exception:
    server_up = False

if not server_up:
    print("  [SKIP] 服务未启动（8765），跳过 HTTP 端到端")
else:
    # 孤儿流水线防护：SSE 客户端被杀/断开时，服务端 worker 仍会把整条流水线跑完
    # （真实 AI 需要几分钟），期间所有端点 409。等到 running=False 再开跑，
    # 避免把「上一次测试的残留」误判成本次回归。
    waited = False
    for _ in range(30):
        try:
            h = requests.get(f"{BASE}/api/health", timeout=5).json()
        except Exception:
            h = {}
        if not h.get("running"):
            break
        if not waited:
            print("  …上一次流水线仍在运行，等待其释放（最多 5 分钟）…")
            waited = True
        time.sleep(10)
    else:
        print("  [FAIL] 服务持续 busy（running=True 超过 5 分钟），跳过 HTTP 端到端")
        sys.exit(1)
    if health.get("ai_mode") == "mock":
        print("  [SKIP] 服务端 AI 是 mock 模式，注入工单也能跑但不会有 ai_delta；仍验证事件序列")
    defect = {
        "id": "STREAM-TEST-1",
        "title": "【流式验证】重复周期应该必填",
        "description": "日程组件 agent-workspace/schedule 的重复周期字段没有校验必填，"
                       "终止时间勾选了也应该必填。",
    }
    events = []
    with requests.post(
        f"{BASE}/api/run/stream",
        json={"defects": [defect], "skip_verify": True, "limit": 1},
        stream=True, timeout=300,
    ) as resp:
        print("  HTTP", resp.status_code, resp.headers.get("content-type", ""))
        check("SSE Content-Type", "text/event-stream" in resp.headers.get("content-type", ""),
              resp.headers.get("content-type", ""))
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[5:].strip()
            if payload:
                try:
                    events.append(json.loads(payload))
                except ValueError:
                    pass

    types = [e.get("type") for e in events]
    check("收到 stage 事件", "stage" in types)
    check("收到 defect 事件", "defect" in types)
    fetch_done = [e for e in events if e.get("type") == "stage"
                  and e.get("stage") == "fetch" and e.get("status") == "done"]
    check("fetch 阶段完成事件（注入来源）", bool(fetch_done))
    deltas = [e for e in events if e.get("type") == "ai_delta"]
    if health.get("ai_mode") != "mock":
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

# ---------------------------------------------------------------- Part C
print("== Part C: 旧接口 /api/run 兼容 ==")
if not server_up:
    print("  [SKIP] 服务未启动")
else:
    resp = requests.post(
        f"{BASE}/api/run",
        json={"defects": [{
            "id": "COMPAT-TEST-1",
            "title": "【兼容验证】列表不刷新",
            "description": "提交后列表不刷新，agent-workspace/schedule 页面。",
        }], "skip_verify": True, "limit": 1},
        timeout=300,
    )
    check("/api/run HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
    data = resp.json()
    check("/api/run 返回 count/results", data.get("count") == 1 and bool(data.get("results")))

# ---------------------------------------------------------------- Part D
print("== Part D: /api/probe/stream 端到端（locate=false，真实拉取） ==")
if not server_up:
    print("  [SKIP] 服务未启动")
else:
    events = []
    with requests.post(
        f"{BASE}/api/probe/stream",
        json={"limit": 1, "locate": False},
        stream=True, timeout=120,
    ) as resp:
        print("  HTTP", resp.status_code, resp.headers.get("content-type", ""))
        check("probe SSE Content-Type",
              "text/event-stream" in resp.headers.get("content-type", ""),
              resp.headers.get("content-type", ""))
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[5:].strip()
            if payload:
                try:
                    events.append(json.loads(payload))
                except ValueError:
                    pass

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
    resp = requests.post(
        f"{BASE}/api/probe",
        json={"limit": 1, "locate": False},
        timeout=120,
    )
    check("/api/probe HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
    pdata = resp.json()
    check("/api/probe 返回 count/source/results", all(
        k in pdata for k in ("count", "source", "results")),
        str(pdata)[:160])

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} 项未通过 -> {FAILS}")
    sys.exit(1)
print("PASS: 实时流式链路全部通过")
