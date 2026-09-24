# -*- coding: utf-8 -*-
"""长任务作业的 HTTP 层自检：SSE 实时流 + 人工介入 + 运行记录。

离线逻辑（编排、总线、产出物）由 `tests/check_team.py` 覆盖；这个脚本补的是
「经 HTTP 走一遍」的那一层，因为 SSE、运行锁、介入投递都只在接口层才能验证：

1. 角色接口返回四个子 Agent；
2. POST /api/team/stream 能实时吐出 run_id / agent_status / item_status，
   并且**以 done 结束**（不收尾的话前端会永远挂在连接上）；
3. 运行途中 POST /api/team/runs/{id}/intervene 能把指令送进运行（总线广播 +
   被对应角色消费的 ack）；
4. 运行结束后再投递指令会明确返回 409（不假装送达）；
5. 运行记录可回看（GET /api/team/runs/{id}），产出物挂在运行记录上。

需要 web.server 已在 base_url 运行。会临时把 AI 切成 Mock 再还原，
不依赖环境里是否配置了真实密钥。

用法：
    python tests/check_team_api.py [base_url]
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"   → {extra}" if (extra and not cond) else ""))


def get(path: str):
    with OPENER.open(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict = None, timeout: int = 30):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def sse(path: str, body: dict, on_event, timeout: int = 180) -> dict:
    """POST + SSE：逐事件回调；返回 done 事件的 payload。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    done_payload = {}
    with OPENER.open(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[5:].strip())
            except Exception:
                continue
            if evt.get("type") == "done":
                done_payload = evt.get("payload") or {}
                break
            on_event(evt)
    return done_payload


def force_mock():
    prev = get("/api/settings").get("ai") or {}
    post("/api/settings", {"ai": {"mock": True}})
    if get("/api/health").get("ai_mode") != "mock":
        raise RuntimeError("无法切到 Mock 模式（服务端 AI 配置异常）")
    return prev


def restore(prev: dict):
    if not prev:
        return
    payload = {k: v for k, v in prev.items()
               if k not in ("api_key_masked", "resolved", "api_key")}
    try:
        post("/api/settings", {"ai": payload})
    except Exception as e:
        print("  [warn] 恢复 AI 配置失败:", e)


def main() -> int:
    try:
        get("/api/health")
    except Exception as e:
        print(f"FAIL: 服务不可达 {BASE}（{e}）")
        return 1

    print(f"目标: {BASE}")
    prev_ai = force_mock()
    try:
        # ── 1. 角色接口 ──
        print("\n[1] 角色接口")
        roles = get("/api/team/roles").get("roles") or []
        ids = [r.get("id") for r in roles]
        check("返回四个子 Agent 角色", ids == ["planner", "coder", "tester", "reviewer"],
              str(ids))
        check("角色带职责说明",
              all(r.get("duty") and r.get("name") for r in roles))

        # ── 2. SSE 实时流 ──
        print("\n[2] SSE 实时流（含中途人工介入）")
        events = []
        state = {"run_id": "", "posted": [], "seen_doing": False}
        token = "【HTTP-人工指令-请优先处理这一项】"

        def on_evt(evt):
            events.append(evt)
            if evt.get("type") == "run_id":
                state["run_id"] = evt.get("run_id") or ""
            # 第一个工作项开始做时投递指令：验证「跑到一半还能插手」
            if (evt.get("type") == "item_status" and evt.get("status") == "doing"
                    and not state["seen_doing"] and state["run_id"]):
                state["seen_doing"] = True

                def _send():
                    code, data = post(f"/api/team/runs/{state['run_id']}/intervene",
                                      {"kind": "message", "agent": "coder", "text": token})
                    state["posted"].append((code, data))

                threading.Thread(target=_send, daemon=True).start()

        payload = sse("/api/team/stream", {
            "title": "HTTP 层自检 · 组件库升级",
            "task": "把项目里的旧版组件全面升级到新版本，保持对外接口不变。",
            "scope": "src/**",
            "framework": "React + TypeScript",
            "max_rounds": 1,
        }, on_evt)

        check("收到 run_id", bool(state["run_id"]), str(state))
        check("以 done 收尾（前端不会挂住）", bool(payload), str(payload)[:200])
        types = [e.get("type") for e in events]
        check("有 agent_status 事件", "agent_status" in types, str(sorted(set(types))))
        check("有 plan / item_status 事件",
              "plan" in types and "item_status" in types, str(sorted(set(types))))
        check("推送了产出物事件", "artifact" in types or bool(payload.get("artifact")),
              str(payload)[:200])
        check("推送了 run_finish",
              any(e.get("type") == "run_finish" for e in events))
        agents = {e.get("agent") for e in events if e.get("type") == "agent_status"}
        check("四个角色都在流里出现过",
              {"planner", "coder", "tester", "reviewer"} <= agents, str(sorted(agents)))
        check("推送了逐字思考/输出增量",
              any(e.get("type") == "agent_delta" for e in events))
        rid = state["run_id"]

        # ── 3. 介入送达 ──
        print("\n[3] 人工介入")
        deadline = time.time() + 5
        while time.time() < deadline and not state["posted"]:
            time.sleep(0.2)
        check("介入请求被受理", bool(state["posted"]),
              "5 秒内没有发出介入请求（可能没有 item_status=doing 事件）")
        if state["posted"]:
            code, data = state["posted"][0]
            check("介入返回 200", code == 200, f"{code} {data}")
            check("介入返回 delivered=True", data.get("delivered") is True, str(data))
            check("如实说明是延迟生效", data.get("deferred") is True and bool(data.get("detail")),
                  str(data))
        check("运行视图收到了介入广播",
              any(e.get("type") == "intervention" for e in events),
              str([e for e in events if e.get("type") == "intervention"]))
        check("介入被对应角色签收（ack）",
              any(e.get("type") == "intervention_ack" and e.get("agent") == "coder"
                  for e in events),
              str([e for e in events if e.get("type") == "intervention_ack"]))

        # ── 4. 运行结束后再投递 ──
        print("\n[4] 运行结束后的介入（必须明确拒绝）")
        code, data = post(f"/api/team/runs/{rid}/intervene",
                          {"kind": "message", "agent": "coder", "text": "迟到的指令"})
        check("结束后投递返回 409", code == 409, f"{code} {data}")
        check("拒绝理由是「已结束」", "结束" in json.dumps(data, ensure_ascii=False),
              str(data))
        code, data = post(f"/api/team/runs/{rid}/abort")
        check("结束后终止返回 409", code == 409, f"{code} {data}")

        # ── 5. 运行记录 ──
        print("\n[5] 运行记录与产出物")
        run = get(f"/api/team/runs/{rid}")
        check("运行记录可读", run.get("id") == rid, str(run)[:200])
        check("运行状态为 done", run.get("status") == "done", str(run.get("status")))
        check("记录里带事件轨迹", bool(run.get("events")), str(len(run.get("events") or [])))
        check("记录里带工作项", bool((run.get("plan") or {}).get("items")),
              json.dumps((run.get("plan") or {}).get("items"), ensure_ascii=False)[:200])
        check("记录里带人工介入", bool(run.get("interventions")),
              json.dumps(run.get("interventions"), ensure_ascii=False)[:200])
        check("记录里没有逐字增量（避免写放大）",
              all(e.get("type") != "agent_delta" for e in run.get("events") or []))
        check("运行已结束标记正确", run.get("running") is False, str(run.get("running")))
        check("产出物 id 挂在运行上", bool(run.get("artifact")), str(run.get("artifact")))
        if run.get("artifact"):
            art = get(f"/api/artifacts/{run['artifact']}")
            check("产出物类型为 team", art.get("type") == "team", str(art.get("type")))
            check("产出物元信息带协作上下文",
                  bool((art.get("team") or {}).get("items")),
                  str(list((art.get("team") or {}).keys())))
        listed = get("/api/team/runs")
        check("运行列表包含本次", any(x.get("id") == rid for x in listed),
              str(len(listed)))
        arts = get("/api/artifacts?type=team")
        check("产出物可按 team 过滤",
              any(a.get("id") == run.get("artifact") for a in arts), str(len(arts)))

        # ── 6. 输入校验 ──
        print("\n[6] 输入校验")
        code, data = post("/api/team/stream", {"task": "   "})
        check("空任务被拒绝", code == 400, f"{code} {data}")
        code, data = post(f"/api/team/runs/{rid}/intervene", {"kind": "乱写"})
        check("非法干预类型被拒绝", code == 400, f"{code} {data}")
        code, data = post("/api/team/runs/no-such-run/intervene", {"kind": "message",
                                                                  "text": "x"})
        check("不存在的运行返回 404", code == 404, f"{code} {data}")
    finally:
        restore(prev_ai)

    print(f"\n通过 {len(OK)} 项，失败 {len(BAD)} 项")
    for b in BAD:
        print(f"  - {b}")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
