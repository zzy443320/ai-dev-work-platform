"""长任务作业的运行记录存储（`team_runs/<run_id>.json`）。

一次运行 = 一份 JSON：任务输入、各角色状态、计划与工作项、事件轨迹、人工介入
记录、最终产出物 id。存下来的意义有三个：

1. **可回看**：跑完（或中断）之后刷新页面还能看到每个角色做过什么；
2. **可续聊**：中断的运行带着上下文，用户能对照着写下一步指令；
3. **可审计**：人工干预何时被哪个角色消费，都要留痕——否则"我明明纠正过它"
   就成了一句无法验证的话。

⚠ 这里**不落盘 AI 的逐字增量**（那会产生几十 MB 的写放大），只落关键事件：
角色状态、工作项流转、人工介入、阶段结论。逐字输出只走 SSE 实时流。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from .json_store import JsonRecordStore
from .textutil import slug

# 只落盘这些类型的事件（ai_delta 这类高频增量刻意排除）
PERSIST_TYPES = (
    "run_start", "run_status", "run_finish", "agent_status", "agent_note", "plan",
    "item_status", "item_files", "review", "artifact", "log",
    "intervention", "intervention_ack", "fatal",
)
MAX_EVENTS = 600
MAX_RUNS_KEPT = 200


def _slug(s: str) -> str:
    return slug(s, maxlen=40, default="run")


class TeamRunStore(JsonRecordStore):
    """一次长任务作业 = team_runs/<id>.json。

    锁必须是**进程级**的：服务端每次请求都新建一个 store 实例（见 web/state.py 的
    _tstore），实例级锁保护不了「介入接口线程」与「编排 worker 线程」之间的
    读-改-写竞争——那会让人工介入记录被后写的 agent/plan 更新整片覆盖掉
    （丢更新丢得很安静，只有对账时才发现"我明明纠正过它"没留痕）。
    这条约束现在由 scripts/json_store.JsonRecordStore 的类级 RLock 统一提供。
    """

    SLUG_LEN = 40
    DEFAULT_SLUG = "run"
    KEEP = MAX_RUNS_KEPT          # 只保留最近 N 份，避免目录无限膨胀

    @staticmethod
    def new_id() -> str:
        """运行 id：秒级时间戳。同一秒内重复时加序号，避免两次运行互相覆盖。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"team-{stamp}"

    def reserve_id(self) -> str:
        """分配一个当前目录下不冲突的 id。"""
        base = self.new_id()
        rid, n = base, 1
        while self._path(rid).exists():
            n += 1
            rid = f"{base}-{n}"
        return rid

    # ------------------------------------------------------------ 基础
    def _write(self, run: Dict) -> None:
        # 沿用本 store 的既有语义：每一次落盘都盖 updated（create 之后还会 _write 一次，
        # 时间戳因此总是"最后一次写入"的时间）。原子替换与唯一临时名由基类负责。
        run["updated"] = datetime.now().isoformat(timespec="seconds")
        super()._write(run)

    def create(self, spec: Dict, roles: List[Dict]) -> Dict:
        now = datetime.now().isoformat(timespec="seconds")
        # run_id 允许外部预分配：服务端要先拿到 id 才能注册介入总线，
        # 否则「运行刚起来、第一个事件还没到」的空窗期里用户没法介入。
        rid = str(spec.get("run_id") or "").strip() or self.new_id()
        run = {
            "id": rid,
            "title": str(spec.get("title") or spec.get("task") or "长任务")[:120],
            "task": str(spec.get("task") or "")[:4000],
            "scope": str(spec.get("scope") or "")[:400],
            "framework": str(spec.get("framework") or "")[:120],
            "created": now,
            "updated": now,
            "status": "planning",
            "ai_mode": "",
            "repo": str(spec.get("repo_path") or ""),
            "roles": roles,
            "agents": [
                {"id": r["id"], "name": r["name"], "emoji": r.get("emoji", ""),
                 "color": r.get("color", ""), "stage": r.get("stage", ""),
                 "duty": r.get("duty", ""), "outputs": r.get("outputs", ""),
                 "status": "idle", "current": "", "progress": {"done": 0, "total": 0},
                 "files": [], "notes": []}
                for r in roles
            ],
            "plan": {"summary": "", "risks": [], "checklist": [], "items": []},
            "round": 0,
            "max_rounds": int(spec.get("max_rounds") or 2),
            "events": [],
            "interventions": [],
            "artifact": "",
            "summary": "",
            "verdict": "",
            "error": "",
            "finished": "",
        }
        self._write(run)
        self._prune()
        return run

    def list(self) -> List[Dict]:
        out: List[Dict] = []
        for f in sorted(self.base.glob("*.json"), key=lambda p: p.name, reverse=True):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append(self.summary(r))
        return out

    def summary(self, run: Dict) -> Dict:
        items = (run.get("plan") or {}).get("items") or []
        done = len([i for i in items if i.get("status") == "done"])
        return {
            "id": run.get("id"),
            "title": run.get("title", ""),
            "status": run.get("status", ""),
            "created": run.get("created"),
            "updated": run.get("updated", run.get("created")),
            "ai_mode": run.get("ai_mode", ""),
            "summary": (run.get("summary") or "")[:200],
            "verdict": run.get("verdict", ""),
            "item_total": len(items),
            "item_done": done,
            "file_count": len(run.get("output_files") or []),
            "artifact": run.get("artifact", ""),
            "interventions": len(run.get("interventions") or []),
            "error": run.get("error", ""),
        }

    # 基类按 KEEP 做保留清理；保留旧方法名，create() 的调用点不变
    _prune = JsonRecordStore.prune

    # --------------------------------------------------------- 事件/介入
    def _mutate(self, rid: str, fn) -> Optional[Dict]:
        """读-改-写。介入接口（事件循环线程）与编排器（worker 线程）都会写同一
        份 JSON，必须串行，否则两边的改动会互相覆盖。"""
        with self._lock:
            run = self.get(rid)
            if not run:
                return None
            fn(run)
            self._write(run)
            return run

    def append_event(self, rid: str, evt: Dict) -> None:
        """追加一条关键事件（高频增量由调用方过滤掉）。"""
        payload = dict(evt)
        payload["type"] = evt.get("type", "")

        def _do(run: Dict) -> None:
            events = run.setdefault("events", [])
            events.append(payload)
            if len(events) > MAX_EVENTS:
                del events[: len(events) - MAX_EVENTS]

        self._mutate(rid, _do)

    def add_intervention(self, rid: str, rec: Dict) -> None:
        self._mutate(rid, lambda run: run.setdefault("interventions", []).append(dict(rec)))

    def ack_intervention(self, rid: str, seq: int, by: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")

        def _do(run: Dict) -> None:
            for rec in run.get("interventions") or []:
                if rec.get("seq") == seq:
                    rec["consumed_by"] = by
                    rec["consumed_at"] = now

        self._mutate(rid, _do)

    def set_agents(self, rid: str, agents: List[Dict]) -> None:
        self._mutate(rid, lambda run: run.__setitem__("agents", agents))

    def set_fields(self, rid: str, **fields) -> None:
        """按键更新少量字段。**不要**用 update() 回写整个内存副本：那份副本可能
        已经落后于磁盘（事件是逐条追加的），整份回写会把中间写入抹掉。"""
        self._mutate(rid, lambda run: run.update(fields))

    def set_plan(self, rid: str, plan: Dict) -> None:
        self._mutate(rid, lambda run: run.__setitem__("plan", plan))

    def finish(self, rid: str, status: str, **extra) -> Optional[Dict]:
        def _do(run: Dict) -> None:
            run["status"] = status
            run["finished"] = datetime.now().isoformat(timespec="seconds")
            run.update(extra)

        return self._mutate(rid, _do)
