# -*- coding: utf-8 -*-
"""运行占用租约回归：看得见是谁在跑、停得掉、异常路径不漏占用、强制解除不被僵尸清掉。

守护的是 2026-10-09 那次现场：一个外部脚本 POST /api/run 跑批量修复，界面上
只剩一句「已有流水线在运行，请等待完成」——不知道是哪条工单、不知道跑了多久、
没有任何停止入口，唯一出路是重启进程。占用标记从裸布尔升级成带身份的租约之后，
这四条性质必须有人盯着，退化时本机不会有任何其他信号：

① 互斥且**原子**（并发申请只有一个赢，不再隔着 MCP 握手抢槽位）；
② 快照/409 里能读出身份（类型、工单、第几单、阶段、来源、已耗时）；
③ 取消是协作式的：置起事件 → 流水线在边界收口 → 结果里如实标 stopped；
④ 强制解除后，僵尸线程收尾**不能**把新一次运行的占用清掉（释放比对租约）。

跑法（不依赖 cwd）： PYTHONUTF8=1 .venv/Scripts/python.exe tests/check_run_lease.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 隔离：所有落盘目录指到临时目录，**必须在 import web.* 之前**设好 ----
_ROOT = Path(tempfile.mkdtemp(prefix="lease_tmp_"))
for _d in ("proposals", "artifacts", "team_runs", "chat_sessions", "screenshots",
           "usage", "knowledge_base", "repo", "attachments"):
    (_ROOT / _d).mkdir(parents=True, exist_ok=True)
_SETTINGS = _ROOT / "ui_settings.json"
_SETTINGS.write_text(json.dumps({
    "repo": {"path": str(_ROOT / "repo"), "branch": "main"},
    "ai": {"mock": True, "api_key": "", "provider": "openai", "model": "mock"},
    "gate": {"commands_text": ""},
    "agent": {"enabled": False},
}, ensure_ascii=False, indent=2), encoding="utf-8")
os.environ.update({
    "SETTINGS_FILE": str(_SETTINGS),
    "KB_DIR": str(_ROOT / "knowledge_base"),
    "PROPOSAL_DIR": str(_ROOT / "proposals"),
    "ARTIFACT_DIR": str(_ROOT / "artifacts"),
    "TEAM_DIR": str(_ROOT / "team_runs"),
    "CHAT_DIR": str(_ROOT / "chat_sessions"),
    "SCREENSHOT_DIR": str(_ROOT / "screenshots"),
    "ATTACH_DIR": str(_ROOT / "attachments"),
    "USAGE_DIR": str(_ROOT / "usage"),
    "REPO_PATH": str(_ROOT / "repo"),
})

from web import state  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


# ===================================================== 1. 纯状态层（不起服务）
print("\n--- 租约语义（web.state 直调） ---")

state._release_run()   # 归零，避免被别的用例残留影响
ev_a = state._acquire_run("run", label="最近 3 条 · 只看我负责的", source="batch-x")
check("空闲时申请成功并拿到取消事件", isinstance(ev_a, threading.Event), repr(ev_a))
check("占用中再申请返回 None",
      state._acquire_run("run", label="第二次") is None)

snap = state._run_snapshot()
check("快照带类型与中文标签", snap.get("kind") == "run"
      and snap.get("kind_label") == "缺陷流水线", json.dumps(snap, ensure_ascii=False)[:120])
check("快照带来源与参数口径", snap.get("source") == "batch-x"
      and "只看我负责的" in (snap.get("label") or ""))
check("快照带起始时间与已耗时", bool(snap.get("started_at"))
      and isinstance(snap.get("elapsed_seconds"), float), str(snap.get("elapsed_seconds")))

state._run_progress(ev_a, index=2, total=3, defect="DEFid00000002",
                    defect_title="列表为空", stage="agent", stage_detail="第 4/8 轮")
snap = state._run_snapshot()
check("进度镜像写入租约", snap.get("index") == 2 and snap.get("defect") == "DEFid00000002"
      and snap.get("stage") == "agent", json.dumps(snap, ensure_ascii=False)[:160])

detail = state._busy_detail("运行流水线")
check("409 正文说清是谁在跑", "缺陷流水线" in detail and "DEFid00000002" in detail
      and "第 2/3 条" in detail and "batch-x" in detail, detail)
check("409 正文给出停止出路", "停止" in detail and "强制解除" in detail, detail)
check("快照里不含取消事件对象（可 JSON 化）",
      "cancel_event" not in snap and "_t0" not in snap)
try:
    json.dumps(snap, ensure_ascii=False)
    serializable = True
except Exception as e:
    serializable = False
    detail = str(e)
check("快照能直接进 JSONResponse", serializable, detail if not serializable else "")

# 释放必须比对租约：拿别人的事件来释放等于没释放
state._release_run(threading.Event())
check("用别人的事件释放不动本次占用", state._run_snapshot().get("running") is True)

r = state._request_run_cancel(False)
check("协作式停止：置起取消事件", r.get("ok") and ev_a.is_set())
check("协作式停止：占用仍在（等边界收口）",
      state._run_snapshot().get("running") is True
      and state._run_snapshot().get("cancel_requested") is True)
check("再次停止不炸且有说明", state._request_run_cancel(False).get("ok") is True)
state._release_run(ev_a)
check("按租约释放后槽位空出", state._run_snapshot().get("running") is False)
check("空闲时取消给出明确说明",
      state._request_run_cancel(False).get("ok") is False)

# 强制解除：槽位立刻可用，且旧线程的收尾不能清掉新占用
ev_old = state._acquire_run("run", label="僵尸", source="zombie")
check("强制解除前是占用状态", state._run_snapshot().get("running") is True)
fr = state._request_run_cancel(True)
check("强制解除立刻腾出槽位", fr.get("force") and not state._run_snapshot().get("running"))
check("强制解除时旧线程的取消事件已置起", ev_old.is_set())
ev_new = state._acquire_run("probe", label="新一次试运行", source="human")
check("强制解除后能马上发起新运行", isinstance(ev_new, threading.Event))
state._run_progress(ev_old, defect="GHOST0000000001")   # 僵尸线程还在写进度
snap = state._run_snapshot()
check("僵尸线程的进度写不进新租约", snap.get("defect") != "GHOST0000000001"
      and snap.get("kind") == "probe", json.dumps(snap, ensure_ascii=False)[:120])
state._release_run(ev_old)                                # 僵尸线程收尾
check("僵尸线程收尾不会清掉新运行", state._run_snapshot().get("running") is True)
state._release_run(ev_new)

# 并发申请只有一个赢（老写法 `if running` + `running = True` 分两步是能抢到的）
winners = []
barrier = threading.Barrier(8)


def _race():
    barrier.wait()
    if state._acquire_run("run", label="race") is not None:
        winners.append(threading.current_thread().name)


threads = [threading.Thread(target=_race) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("8 个并发申请只有 1 个赢", len(winners) == 1, f"winners={len(winners)}")
state._release_run()


# ===================================================== 2. 端到端（真起一个服务）
print("\n--- 端到端：/api/run 可见 + /api/run/cancel 可停 ---")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakePipeline:
    """替身流水线：只做「发事件 + 睡一会儿 + 读取消信号」，不碰 ONES 与模型。

    被测性质恰恰只在事件与取消信号上——用真流水线反而测不出「边界收口」。
    """

    class ai:
        mode = "mock"

    preflight = {"current_branch": "main", "expected_branch": "main",
                 "dirty": False, "problems": []}

    def __init__(self, per_defect=0.6, n=4):
        self.per_defect = per_defect
        self.n = n
        self.got_cancel = None

    def _defects(self):
        return [{"id": f"LEASE{i}000000000{i}", "title": f"占用回归工单 {i}",
                 "description": ""} for i in range(1, self.n + 1)]

    def run(self, defect_id=None, mock=False, dry_run=False, limit=5,
            skip_verify=False, mine_only=True, defects=None, emit=None,
            should_cancel=None):
        self.got_cancel = should_cancel
        out = []
        ds = self._defects()
        half = self.per_defect / 2.0
        for i, d in enumerate(ds):
            if should_cancel is not None and should_cancel():
                if emit:
                    emit({"type": "stage", "stage": "stopped", "status": "warn",
                          "detail": f"已按请求停止：剩余 {len(ds) - i} 条工单未处理"})
                break
            if emit:
                emit({"type": "defect", "index": i + 1, "total": len(ds),
                      "id": d["id"], "title": d["title"]})
            # 阶段事件与下一条工单的 defect 事件之间**必须留出时间窗**：
            # defect 事件会把 stage 清空（新工单还没阶段），两条之间零间隔时
            # 0.1 秒轮询永远抓不到阶段描述——那是夹具的锅，不是产品的锅。
            time.sleep(half)
            if emit:
                emit({"type": "stage", "stage": "locate", "status": "done",
                      "defect": d["id"], "detail": "定位到 1 个嫌疑文件"})
            time.sleep(half)
            out.append({"defect": d, "analysis": {"category": "逻辑"},
                        "proposal": {"id": f"P{i}", "status": "pending",
                                     "patch": {"files": ["a.ts"], "errors": [],
                                               "warnings": []},
                                     "gate": {"level": "sandbox", "ok": True},
                                     "agent": {"conclusion": "verified",
                                               "rounds": 2, "attempts": [1]}},
                        "verify": {"status": "ok"}, "kb_path": "x.md"})
        return out

    def probe(self, defect_id=None, mock=False, limit=5, locate=True, days=0,
              mine_only=True, emit=None, should_cancel=None):
        data = {"count": 0, "source": "ones", "fetch_error": "", "notes": [],
                "hints": [], "results": []}
        for i in range(3):
            if should_cancel is not None and should_cancel():
                break
            time.sleep(self.per_defect)
            data["count"] += 1
        return data


import web.routers.pipeline as pr   # noqa: E402
import web.urls as urls            # noqa: E402

urls._pipeline_preflight = lambda: FakePipeline.preflight   # 不起 git 子进程

PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def api(path, method="GET", payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def wait_running(want=True, deadline=12.0):
    end = time.time() + deadline
    while time.time() < end:
        code, body = api("/api/run/status")
        if code == 200 and bool(body.get("running")) == want:
            return body
        time.sleep(0.1)
    return None


def wait_field(pred, deadline=8.0):
    """轮询 /api/run/status 直到 pred 满足。

    不能拿「刚 running 的那一帧」断言阶段描述——那一帧 locate 事件还没发出来，
    断言会变成随机失败发生器。
    """
    end = time.time() + deadline
    last = None
    while time.time() < end:
        code, body = api("/api/run/status")
        last = body if code == 200 else None
        if os.environ.get("LEASE_TRACE"):
            print("   trace", code, json.dumps(last, ensure_ascii=False)[:150] if last else last)
        if last is not None and pred(last):
            return last
        time.sleep(0.1)
    return last


_server = None
_srv_thread = None
try:
    import uvicorn
    from web.server import app

    _cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
    _server = uvicorn.Server(_cfg)

    def _serve():
        try:
            _server.run()
        except Exception as e:  # 起不来就直接判失败，别让后面的用例瞎跑
            print(f"  FAIL 临时服务启动异常: {e}")

    _srv_thread = threading.Thread(target=_serve, daemon=True, name="lease-server")
    _srv_thread.start()

    ready = False
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.1)
    check("临时服务已启动", ready, BASE)

    if ready:
        fake = FakePipeline(per_defect=0.6, n=4)
        pr._fresh_pipeline = lambda: fake

        resp = {}

        def _post_run():
            code, body = api("/api/run", "POST",
                             {"limit": 4, "mine_only": True,
                              "source": "batch-lease-check"}, timeout=120)
            resp["code"], resp["body"] = code, body

        t = threading.Thread(target=_post_run, daemon=True)
        t.start()

        snap = wait_running(True)
        check("运行中 /api/run/status 报 running", bool(snap), json.dumps(snap or {}, ensure_ascii=False)[:160])
        check("状态里能看到来源（外部脚本自报家门）",
              bool(snap) and snap.get("source") == "batch-lease-check", str((snap or {}).get("source")))
        check("状态里能看到正在处理哪条工单",
              bool(snap) and str(snap.get("defect", "")).startswith("LEASE"),
              str((snap or {}).get("defect")))
        check("状态里能看到第几单 / 共几单",
              bool(snap) and snap.get("index", 0) >= 1 and snap.get("total") == 4,
              f"{(snap or {}).get('index')}/{(snap or {}).get('total')}")
        staged = wait_field(lambda s: "嫌疑文件" in str(s.get("stage_detail", "")))
        check("状态里能看到当前阶段描述",
              bool(staged) and "嫌疑文件" in str(staged.get("stage_detail", "")),
              str((staged or {}).get("stage_detail")))
        check("状态里能看到已耗时", isinstance((staged or {}).get("elapsed_seconds"), float)
              and (staged or {}).get("elapsed_seconds", 0) > 0,
              str((staged or {}).get("elapsed_seconds")))

        code, body = api("/api/run", "POST", {"limit": 1})
        check("第二次运行被拒（409）", code == 409, f"code={code}")
        check("409 正文带身份（不再是那句空话）",
              "缺陷流水线" in str(body.get("error")) and "batch-lease-check" in str(body.get("error")),
              str(body.get("error"))[:160])
        check("409 响应把租约一并带出（界面据此渲染停止按钮）",
              isinstance(body.get("run"), dict) and body["run"].get("running") is True)

        code, body = api("/api/run/cancel", "POST", {})
        check("停止请求被接受", code == 200 and body.get("ok") is True, json.dumps(body, ensure_ascii=False)[:160])
        check("停止是协作式的（占用先留着，边界才收口）",
              body.get("running") is True and "边界" in str(body.get("detail")),
              str(body.get("detail")))
        snap = wait_running(False, deadline=15)
        check("边界收口后槽位自动空出", snap is not None)

        t.join(timeout=30)
        check("POST /api/run 正常返回（不是 500）", resp.get("code") == 200,
              f"code={resp.get('code')} body={str(resp.get('body'))[:120]}")
        check("返回里如实标出被停止（stopped）", resp.get("body", {}).get("stopped") is True,
              json.dumps(resp.get("body", {}), ensure_ascii=False)[:120])
        check("被停止时结果不是全集", len(resp.get("body", {}).get("results", [])) < 4,
              f"got={len(resp.get('body', {}).get('results', []))}")
        check("取消事件确实传到了流水线（should_cancel 非空）",
              callable(fake.got_cancel), repr(fake.got_cancel))

        # ---- 强制解除：僵尸线程还在跑，但界面能立刻发起下一次 ----
        zombie = FakePipeline(per_defect=1.2, n=5)
        pr._fresh_pipeline = lambda: zombie
        zresp = {}

        def _post_zombie():
            code, body = api("/api/run", "POST", {"limit": 5, "source": "zombie-run"},
                             timeout=180)
            zresp["code"], zresp["body"] = code, body

        tz = threading.Thread(target=_post_zombie, daemon=True)
        tz.start()
        snap = wait_running(True)
        check("僵尸运行已起", bool(snap) and snap.get("source") == "zombie-run")
        code, body = api("/api/run/cancel", "POST", {"force": True})
        check("强制解除返回 running=false", code == 200 and body.get("force") is True
              and body.get("running") is False, json.dumps(body, ensure_ascii=False)[:160])

        fresh = FakePipeline(per_defect=0.5, n=2)
        pr._fresh_pipeline = lambda: fresh
        nresp = {}

        def _post_fresh():
            code, body = api("/api/run", "POST", {"limit": 2, "source": "fresh-run"},
                             timeout=120)
            nresp["code"], nresp["body"] = code, body

        tn = threading.Thread(target=_post_fresh, daemon=True)
        tn.start()
        snap = wait_running(True)
        check("强制解除后能马上发起新运行",
              bool(snap) and snap.get("source") == "fresh-run", str((snap or {}).get("source")))
        tn.join(timeout=60)
        check("新运行完整跑完（不被僵尸干扰）",
              nresp.get("code") == 200 and nresp.get("body", {}).get("stopped") is False,
              json.dumps(nresp.get("body", {}), ensure_ascii=False)[:120])
        check("新运行跑完后槽位空出", wait_running(False, deadline=10) is not None)
        tz.join(timeout=90)
        check("僵尸线程收尾后没有把槽位改回非运行（它早已不是租约持有者）",
              state._run_snapshot().get("running") is False)

        # ---- 试运行同样可见可停 ----
        probe_fake = FakePipeline(per_defect=0.8, n=3)
        pr._fresh_pipeline = lambda: probe_fake
        presp = {}

        def _post_probe():
            code, body = api("/api/probe", "POST", {"limit": 3, "source": "probe-check"},
                             timeout=120)
            presp["code"], presp["body"] = code, body

        tp = threading.Thread(target=_post_probe, daemon=True)
        tp.start()
        snap = wait_running(True)
        check("试运行也带身份（kind=probe）", bool(snap) and snap.get("kind") == "probe"
              and snap.get("kind_label") == "试运行（拉取 + AI 定位）",
              str((snap or {}).get("kind_label")))
        api("/api/run/cancel", "POST", {})
        tp.join(timeout=60)
        check("试运行被停止后 stopped=true",
              presp.get("code") == 200 and presp.get("body", {}).get("stopped") is True,
              json.dumps(presp.get("body", {}), ensure_ascii=False)[:120])
        check("停止后槽位空出", wait_running(False, deadline=10) is not None)

        # ---- 异常路径不漏占用：流水线直接抛异常 ----
        class Boom(FakePipeline):
            def run(self, **kw):
                raise RuntimeError("炸在半路")

        pr._fresh_pipeline = lambda: Boom()
        code, body = api("/api/run", "POST", {"limit": 1})
        check("流水线抛异常时返回 500 而不是挂住", code == 500
              and "炸在半路" in str(body.get("error")), f"code={code} {str(body)[:120]}")
        check("异常路径也释放了占用（老代码靠 finally，退化就永久卡死）",
              wait_running(False, deadline=8) is not None)

        code, body = api("/api/health")
        check("/api/health 仍带 running 且新增 run（老前端不破）",
              code == 200 and "running" in body and isinstance(body.get("run"), dict),
              f"code={code}")
finally:
    if _server is not None:
        _server.should_exit = True
        if _srv_thread is not None:
            _srv_thread.join(timeout=10)
        # 端口真释放了才算干净（否则下一个用例连上的可能是旧服务）
        released = False
        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
                    time.sleep(0.1)
            except OSError:
                released = True
                break
        check("临时服务端口已释放", released)
    state._release_run()

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
