"""长任务作业（多子 Agent 协作）离线自检。

不联网、不起服务：用 mock 模型 + 临时 git 仓库，把编排器与人工介入的关键行为
跑一遍。改 `scripts/team_*.py` 后必须跑这个脚本。

    .venv/Scripts/python.exe tests/check_team.py
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.artifact import ArtifactApplier, ArtifactStore  # noqa: E402
from scripts.team_bus import TeamBus  # noqa: E402
from scripts.team_run import TeamOrchestrator  # noqa: E402
from scripts.team_store import TeamRunStore  # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"   → {extra}" if (extra and not cond) else ""))


def make_repo(tmp: Path) -> Path:
    """临时 git 仓库：故意不带 scripts（闸门走 degraded 的语法兜底），
    这样离线也能验证「采纳 → 写工作区」这条链路。"""
    repo = tmp / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "index.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (repo / "package.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8")
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"],
                ["git", "add", "-A"],
                ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def make_env(tmp: Path, name: str, pause_timeout: float = 5.0):
    repo = make_repo(tmp) if name == "a" else (tmp / "repo")
    base = tmp / f"work-{name}"
    store = TeamRunStore(str(base / "team_runs"))
    bus = TeamBus("pending", pause_timeout=pause_timeout)
    config = {
        "ai": {"mock": True},
        "repo": {"path": str(repo)},
        "artifacts": {"output_dir": str(base / "artifacts")},
        "gate": {"degraded": True},
    }
    return repo, store, bus, config, base


SPEC = {
    "title": "把旧版列表页迁移到新架构",
    "task": "把 src 下的列表页从旧架构迁移到新架构，保持对外接口不变。",
    "scope": "src/**",
    "framework": "React + TypeScript",
    "roles": ["planner", "coder", "tester", "reviewer"],
    "max_rounds": 2,
}


def run_case(tmp, name, spec=None, on_event=None, pause_timeout=5.0):
    repo, store, bus, config, base = make_env(tmp, name, pause_timeout)
    orch = TeamOrchestrator(config, store, bus)
    orch.rid = ""
    events = []
    prompts = {"coder": [], "planner": [], "tester": [], "reviewer": []}
    orig_ask = orch._ask

    def ask(agent_id, prompt, mock=None, extra_notes=None):
        prompts.setdefault(agent_id, []).append(prompt + "\n".join(extra_notes or []))
        return orig_ask(agent_id, prompt, mock=mock, extra_notes=extra_notes)

    orch._ask = ask
    bus.run_id = "pending"

    def post(kind, agent="*", text="", item=""):
        """等价于 /api/team/runs/{id}/intervene 做的事：进总线 + 落运行记录。
        测试里直接调，保证断言覆盖的是同一套语义。"""
        rec = bus.post(kind, agent, text=text, item=item)
        store.add_intervention(orch.rid, rec)
        return rec

    def emit(evt):
        events.append(evt)
        if on_event:
            on_event(evt, post, orch)

    # 让 store 里 run_id 与 bus 对上：create 发生在 run() 内部，所以 bus 的
    # run_id 用占位值即可（总线不依赖 run_id 做路由）
    result = orch.run(dict(spec or SPEC), emit=emit)
    return repo, store, bus, orch, result, events, prompts, base


def main():
    tmp = Path(tempfile.mkdtemp(prefix="check-team-"))
    print(f"临时目录: {tmp}")

    # ---------------------------------------------------------- 场景 1：完整跑通
    print("\n[1] 完整跑通：拆解 → 逐项编码 → 测试 → 复核 → 产出物")
    repo, store, bus, orch, result, events, prompts, base = run_case(tmp, "a")
    run = result["run"]
    check("运行状态 done", run["status"] == "done", run.get("error", ""))
    check("ai_mode 标注为 mock", run.get("ai_mode") == "mock", str(run.get("ai_mode")))
    items = (run.get("plan") or {}).get("items") or []
    check("决策官拆出工作项", len(items) >= 3, f"items={len(items)}")
    check("所有工作项完成", all(i["status"] == "done" for i in items),
          str([(i["id"], i["status"]) for i in items]))
    check("四个角色都被调度", all(prompts[k] for k in ("planner", "coder", "tester", "reviewer")),
          str({k: len(v) for k, v in prompts.items()}))
    check("编码次数 == 工作项数", len(prompts["coder"]) == len(items),
          f"coder={len(prompts['coder'])} items={len(items)}")
    check("复核结论存在", bool(run.get("review", {}).get("verdict")),
          str(run.get("review")))
    types = {e.get("type") for e in events}
    for t in ("run_start", "plan", "item_status", "item_files", "review", "artifact", "run_finish"):
        check(f"事件流包含 {t}", t in types, str(sorted(types)))
    check("每个 agent 都有状态事件",
          {"planner", "coder", "tester", "reviewer"} <= {e.get("agent") for e in events
                                                          if e.get("type") == "agent_status"},
          str(sorted({e.get("agent") for e in events if e.get("type") == "agent_status"})))

    art_id = run.get("artifact")
    check("产出物 id 落到运行记录", bool(art_id), str(art_id))
    artifact = ArtifactStore(str(base / "artifacts")).get(art_id) if art_id else None
    check("产出物可读", bool(artifact))
    if artifact:
        files = artifact.get("files") or []
        check("产出物类型 team", artifact.get("type") == "team", str(artifact.get("type")))
        check("产出物含多个文件", len(files) >= 4, str(len(files)))
        check("每个文件标注产出角色", all(f.get("by") for f in files),
              str([f.get("path") for f in files if not f.get("by")]))
        check("产出物带协作上下文", bool((artifact.get("team") or {}).get("items")),
              str(list((artifact.get("team") or {}).keys())))
        check("产出物状态为待采纳", artifact.get("status") == "pending",
              str(artifact.get("status")))
        check("产出物不写仓库（工作区干净）",
              not subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                 capture_output=True, text=True).stdout.strip(),
              subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                             capture_output=True, text=True).stdout)

    # ---------------------------------------------- 场景 2：采纳才写工作区，永不 commit
    print("\n[2] 采纳链路：只写工作区、不产生提交")
    if artifact:
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                     capture_output=True, text=True).stdout.strip()
        applied = ArtifactApplier(str(repo), {"degraded": True}).apply(artifact)
        check("采纳成功", applied.get("ok") is True, str(applied.get("error")))
        check("确实写入了文件",
              all((repo / f["path"]).is_file() for f in artifact.get("files") or []))
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                    capture_output=True, text=True).stdout.strip()
        check("HEAD 未变化（永不 commit）", head_before == head_after,
              f"{head_before} → {head_after}")
        check("committed 标记为 False", applied.get("committed") is False)
        st = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            capture_output=True, text=True).stdout
        check("工作区出现未跟踪/已修改文件", bool(st.strip()), st)

    # ------------------------------------------------- 场景 3：人工追问注入提示词
    print("\n[3] 人工追问：投递 → 边界消费 → 注入提示词 → 留痕")
    token = "【人工指令-测试令牌-9527】"

    def on_evt(evt, post, o):
        if evt.get("type") == "plan" and not evt.get("revised"):
            post("message", "coder", token)

    repo3, store3, bus3, orch3, res3, ev3, pr3, base3 = run_case(
        tmp, "b", on_event=on_evt)
    check("注入后仍能跑完", res3["run"]["status"] == "done", str(res3["run"].get("error")))
    check("人工指令进入了编码提示词",
          any(token in p for p in pr3["coder"]), f"coder prompts={len(pr3['coder'])}")
    recs = (store3.get(res3["run"]["id"]) or {}).get("interventions") or []
    check("介入被记录且标记消费方",
          bool(recs) and recs[0].get("consumed_by") == "coder",
          json.dumps(recs, ensure_ascii=False))
    check("广播了介入确认事件",
          any(e.get("type") == "intervention_ack" and e.get("agent") == "coder" for e in ev3))
    # 决策官不该替编码工程师签收发给编码工程师的指令
    check("指令没有被其他角色误消费",
          not any(e.get("type") == "intervention_ack" and e.get("agent") != "coder"
                  for e in ev3),
          str([e for e in ev3 if e.get("type") == "intervention_ack"]))

    # ------------------------------------------------------- 场景 4：跳过与终止
    print("\n[4] 跳过工作项 + 中途终止")

    def on_evt4(evt, post, o):
        if evt.get("type") == "plan" and not evt.get("revised"):
            post("skip", "planner", item="t2")

    repo4, store4, bus4, orch4, res4, ev4, pr4, base4 = run_case(tmp, "c", on_event=on_evt4)
    items4 = (res4["run"].get("plan") or {}).get("items") or []
    skipped = [i for i in items4 if i["status"] == "skipped"]
    check("指定工作项被跳过", len(skipped) == 1 and skipped[0]["id"] == "t2",
          str([(i["id"], i["status"]) for i in items4]))
    check("跳过后其余工作项仍完成",
          len([i for i in items4 if i["status"] == "done"]) >= 2,
          str([(i["id"], i["status"]) for i in items4]))
    check("跳过被记录为人工指令",
          any(r.get("kind") == "skip" for r in
              (store4.get(res4["run"]["id"]) or {}).get("interventions") or []),
          json.dumps((store4.get(res4["run"]["id"]) or {}).get("interventions"),
                     ensure_ascii=False))

    stop_state = {"n": 0}

    def on_evt5(evt, post, o):
        if evt.get("type") == "item_status" and evt.get("status") == "done":
            stop_state["n"] += 1
            if stop_state["n"] == 1:
                post("stop", "*")

    repo5, store5, bus5, orch5, res5, ev5, pr5, base5 = run_case(tmp, "d", on_event=on_evt5)
    run5 = res5["run"]
    check("终止后状态为 aborted", run5["status"] == "aborted", str(run5["status"]))
    items5 = (run5.get("plan") or {}).get("items") or []
    check("终止保留了未完成的工作项", any(i["status"] == "todo" for i in items5),
          str([(i["id"], i["status"]) for i in items5]))
    check("终止后仍交付已完成的部分",
          bool(run5.get("artifact")) and len(run5.get("output_files") or []) > 0,
          str(run5.get("artifact")))
    if run5.get("artifact"):
        art5 = ArtifactStore(str(base5 / "artifacts")).get(run5["artifact"])
        check("部分产出的标题有标记", "[已终止·部分产出]" in (art5 or {}).get("title", ""),
              str((art5 or {}).get("title")))

    # ------------------------------------------------------------ 场景 5：暂停/继续
    print("\n[5] 暂停 / 继续：在安全边界生效")
    paused_evt = threading.Event()
    stop_watch = {"fired": False}

    def on_evt6(evt, post, o):
        if evt.get("type") == "run_status" and "暂停" in (evt.get("detail") or ""):
            paused_evt.set()

    def pause_on_second_item(evt, post, o):
        if evt.get("type") == "item_status" and evt.get("status") == "doing" \
                and not stop_watch["fired"] and evt.get("item") == "t2":
            stop_watch["fired"] = True
            post("pause", "*")

            def _resume():
                time.sleep(1.0)
                post("resume", "*")

            threading.Thread(target=_resume, daemon=True).start()
        on_evt6(evt, post, o)

    repo6, store6, bus6, orch6, res6, ev6, pr6, base6 = run_case(
        tmp, "e", on_event=pause_on_second_item, pause_timeout=30.0)
    check("触发了暂停", paused_evt.is_set() or
          any(e.get("type") == "run_status" and e.get("status") == "paused" for e in ev6),
          str([e for e in ev6 if e.get("type") == "run_status"]))
    check("暂停后能继续并跑完", res6["run"]["status"] == "done", str(res6["run"].get("error")))
    check("暂停事件同时落盘",
          any(e.get("type") == "run_status" and e.get("status") == "paused"
              for e in (store6.get(res6["run"]["id"]) or {}).get("events") or []),
          "events 里没有 paused 记录")

    # --------------------------------------------------------- 场景 6：输入校验与存储
    print("\n[6] 输入校验 / 运行记录存储")
    r7 = run_case(tmp, "f", spec={**SPEC, "task": "   "})
    check("空任务描述被拒绝", r7[4]["run"]["status"] == "failed", str(r7[4]))

    listed = store.list()
    check("运行记录可列出", any(x["id"] == run["id"] for x in listed),
          f"{len(listed)} 条")
    got = store.get(run["id"])
    check("运行记录可回读", bool(got) and got["id"] == run["id"])
    check("落盘包含事件轨迹与工作项",
          bool((got or {}).get("events")) and bool(((got or {}).get("plan") or {}).get("items")),
          f"events={len((got or {}).get('events') or [])}")
    check("逐字增量未落盘（避免写放大）",
          all(e.get("type") != "agent_delta" for e in (got or {}).get("events") or []))

    print(f"\n通过 {len(OK)} 项，失败 {len(BAD)} 项")
    if BAD:
        print("失败项：")
        for b in BAD:
            print(f"  - {b}")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
