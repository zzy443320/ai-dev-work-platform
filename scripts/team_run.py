"""长任务作业的多子 Agent 编排器。

面向「工作量大、执行时间长、覆盖面广」的任务（代码重构、组件升级/迁移、
批量接口改造等），把活拆给不同职责的子 Agent 去做：

    决策官 ──拆解──▶ 工作项 ──▶ 编码工程师 ──产出──▶ 测试工程师 ──▶ 复核官
      ▲                              │                              │
      └──────────── 人工介入（追问 / 纠偏 / 重规划） ◀──────────────┘

三条硬性约束，与项目既有的安全契约保持一致：

1. **任何角色都不写目标仓库**。所有产出先合成一份「产出物」（artifact），
   只有人工在产出物详情里点「采纳」才写工作区，且永不 commit。
2. **角色之间不自由对话**，消息一律经编排器转发——所以每一步都可回放、可审计。
3. **人工介入在安全边界消费**（见 `team_bus.py`）：模型调用不可中断，界面如实
   说明「暂停会等当前步骤结束」，不做假的即时中断。

模型调用是同步的，本模块整体跑在 worker 线程里（server 侧 `_queue_stream`）。
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import usage
from .ai_model import AIModel
from .artifact import ArtifactStore, safe_rel_path
from .team_bus import TeamBus
from .team_roles import DEFAULT_ROLES, get_role, inject_human
from .team_store import PERSIST_TYPES, TeamRunStore

AI_BUDGET = 16000          # 推理模型的 hidden reasoning 与正文共享该预算，留足余量
MAX_ITEM_FILES = 6         # 单个工作项最多产出的文件数
MAX_TEST_FILES = 3
MAX_FILE_CHARS = 20000
MAX_CTX_FILES = 5          # 注入编码提示词的「当前文件内容」数量上限
CTX_FILE_CHARS = 9000
CTX_TOTAL_CHARS = 40000
MAX_LISTED_FILES = 300
REVIEW_FILE_CHARS = 1500
MAX_ITEMS = 8

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage",
             "__pycache__", ".venv", "venv", ".idea", ".vscode", "out", ".cache"}

MOCK_PREFIX = "src/__team_mock__/"


class TeamTaskError(Exception):
    """输入不合法 / 前置条件不满足（可直接展示给用户）。"""


# ------------------------------------------------------------------ 归一化
def _clip(s, n: int) -> str:
    return str(s or "")[:n]


def _str_list(v, limit: int = 20) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()][:limit]
    if isinstance(v, str) and v.strip():
        return [l.strip("-•* 0123456789.") for l in v.splitlines() if l.strip()][:limit]
    return []


def _norm_files(raw, by: str, limit: int) -> List[Dict]:
    """把模型返回的 files 归一化成可落库的形状（带产出角色）。"""
    out: List[Dict] = []
    seen = set()
    for f in (raw or [])[: limit * 3] if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        path = str(f.get("path") or f.get("file_path") or "").strip() \
            .replace("\\", "/").lstrip("/")
        content = str(f.get("content") or "")
        if not path or not content.strip() or path in seen:
            continue
        seen.add(path)
        action = "overwrite" if str(f.get("action", "")).lower() == "overwrite" else "create"
        out.append({
            "path": path,
            "action": action,
            "description": _clip(f.get("description"), 300),
            "content": content[:MAX_FILE_CHARS],
            "by": by,
        })
        if len(out) >= limit:
            break
    return out


def _norm_items(raw) -> List[Dict]:
    out: List[Dict] = []
    for i, it in enumerate((raw or [])[:MAX_ITEMS] if isinstance(raw, list) else [], 1):
        if not isinstance(it, dict):
            continue
        title = _clip(it.get("title") or it.get("name"), 120)
        if not title:
            continue
        files = []
        for f in (it.get("files") or [])[:10] if isinstance(it.get("files"), list) else []:
            p = str(f or "").strip().replace("\\", "/").lstrip("/")
            if p:
                files.append(p)
        out.append({
            "id": _clip(it.get("id") or f"t{i}", 24) or f"t{i}",
            "title": title,
            "detail": _clip(it.get("detail") or it.get("description"), 2000),
            "files": files,
            "acceptance": _clip(it.get("acceptance") or it.get("done_when"), 400),
            "status": "todo",
            "note": "",
        })
    return out


def _norm_plan(res: Dict) -> Dict:
    return {
        "summary": _clip(res.get("summary"), 1200),
        "risks": _str_list(res.get("risks")),
        "checklist": _str_list(res.get("checklist")),
        "items": _norm_items(res.get("items") or res.get("tasks") or res.get("plan")),
    }


def _norm_review(res: Dict) -> Dict:
    verdict = str(res.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "pass_with_risks", "block"):
        verdict = "pass_with_risks" if (res.get("risks") or res.get("must_fix")) else "pass"
    must_fix = []
    for m in (res.get("must_fix") or [])[:8] if isinstance(res.get("must_fix"), list) else []:
        if isinstance(m, dict):
            must_fix.append({"item": _clip(m.get("item"), 120),
                             "why": _clip(m.get("why") or m.get("reason"), 400),
                             "how": _clip(m.get("how") or m.get("fix"), 400)})
        elif str(m).strip():
            must_fix.append({"item": "", "why": _clip(m, 400), "how": ""})
    return {
        "verdict": verdict,
        "summary": _clip(res.get("summary"), 1200),
        "risks": _str_list(res.get("risks"), 12),
        "must_fix": must_fix,
        "suggestions": _str_list(res.get("suggestions"), 12),
    }


def _needs_repair(review: Optional[Dict]) -> bool:
    if not review:
        return False
    return review.get("verdict") == "block" or bool(review.get("must_fix"))


def _match_glob(path: str, pat: str) -> bool:
    pat = pat.replace("\\", "/")
    if fnmatch.fnmatch(path, pat):
        return True
    if "/" not in pat:
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pat)
    return False


# -------------------------------------------------------------- 编排器
class TeamOrchestrator:
    def __init__(self, config: Dict, store: TeamRunStore, bus: TeamBus):
        self.config = config or {}
        self.store = store
        self.bus = bus
        self.ai = AIModel(dict(self.config.get("ai") or {}))
        self.repo_path = str((self.config.get("repo") or {}).get("path") or "")
        art_dir = (self.config.get("artifacts") or {}).get("output_dir") or "./artifacts"
        self.artifacts = ArtifactStore(art_dir)

        self.rid = ""
        self.record: Dict = {}
        self.agents: List[Dict] = []
        self.enabled: List[str] = []
        self.files: Dict[str, Dict] = {}
        self.repo_files: List[str] = []
        self.emit = None
        self.stopped = False

    # ------------------------------------------------------------- 事件
    def _ev(self, evt: Dict) -> None:
        """关键事件同时落盘（供刷新后回看）与推送（供实时视图）。

        事件回调异常必须吞掉（否则一个订阅方的问题会毁掉整轮运行），但要打日志——
        静默吞异常曾经把「人工指令根本没进收件箱」这类问题藏了整整一轮排查。
        """
        if evt.get("type") in PERSIST_TYPES:
            try:
                self.store.append_event(self.rid, evt)
            except Exception as e:
                print(f"[team] 事件落盘失败 {evt.get('type')}: {e}")
        if self.emit is None:
            return
        try:
            self.emit(evt)
        except Exception as e:
            print(f"[team] 事件回调异常 {evt.get('type')}: {type(e).__name__}: {e}")

    def _agent(self, agent_id: str) -> Dict:
        for a in self.agents:
            if a["id"] == agent_id:
                return a
        return {}

    def _set_agent(self, agent_id: str, status: Optional[str] = None,
                   current: Optional[str] = None, progress: Optional[Dict] = None,
                   note: str = "", files: Optional[List[str]] = None,
                   announce: bool = True) -> None:
        ag = self._agent(agent_id)
        if not ag:
            return
        if status:
            ag["status"] = status
        if current is not None:
            ag["current"] = _clip(current, 300)
        if progress:
            ag["progress"] = progress
        if note:
            ag["notes"] = (ag.get("notes") or [])[-5:] + [_clip(note, 400)]
        if files is not None:
            ag["files"] = files[:40]
        try:
            self.store.set_agents(self.rid, self.agents)
        except Exception:
            pass
        if announce:
            self._ev({"type": "agent_status", "agent": agent_id,
                      "status": ag.get("status", ""), "current": ag.get("current", ""),
                      "progress": ag.get("progress") or {}, "files": ag.get("files") or [],
                      "note": note})

    def _ack(self, ck: Dict, agent_id: str) -> None:
        """人工介入被消费 → 留痕 + 广播，让用户看见「它收到了」。"""
        for rec in ck.get("records") or []:
            try:
                self.store.ack_intervention(self.rid, rec["seq"], agent_id)
            except Exception:
                pass
            self._ev({"type": "intervention_ack", "seq": rec["seq"], "kind": rec["kind"],
                      "agent": agent_id, "text": _clip(rec.get("text"), 300)})
        for n in ck.get("notes") or []:
            self._ev({"type": "log", "level": "human",
                      "text": f"人工指令已交给「{get_role(agent_id)['name']}」：{_clip(n, 200)}"})

    def _progress(self, plan: Dict) -> Dict:
        items = plan.get("items") or []
        return {"done": len([i for i in items if i.get("status") == "done"]),
                "total": len(items)}

    # ------------------------------------------------------------- 主流程
    def run(self, spec: Dict, emit=None) -> Dict:
        self.emit = emit
        # 介入总线的事件出口指向本编排器：HTTP 线程投递的指令也能立刻
        # 出现在实时运行视图里，不必等下一个安全边界才"有反应"。
        try:
            self.bus.set_emitter(self._ev)
        except Exception:
            pass
        want = [i for i in (spec.get("roles") or DEFAULT_ROLES)]
        roles = []
        for i in want:
            try:
                roles.append(get_role(i))
            except KeyError:
                continue
        if not roles:
            roles = [get_role(i) for i in DEFAULT_ROLES]
        if "planner" not in [r["id"] for r in roles]:
            roles.insert(0, get_role("planner"))
        self.enabled = [r["id"] for r in roles]

        run = self.store.create(spec, [self._role_card(r) for r in roles])
        self.rid, self.record, self.agents = run["id"], run, run["agents"]
        self._ev({"type": "run_start", "run": run})

        try:
            task = (spec.get("task") or "").strip()
            if not task:
                raise TeamTaskError("请先填写任务描述（要重构/迁移什么、目标是什么）")
            if self.ai.mode:
                self.record["ai_mode"] = self.ai.mode
                self.store.set_fields(self.rid, ai_mode=self.ai.mode)

            self.repo_files = self._list_repo_files(spec.get("scope") or "")
            self._ev({"type": "log", "level": "dim",
                      "text": f"目标仓库文件清单：{len(self.repo_files)} 个"
                              f"（范围：{spec.get('scope') or '全仓库'}）"})
            if not self.repo_files:
                self._ev({"type": "log", "level": "warn",
                          "text": "没有读到仓库文件清单（仓库路径未配置或不是 git 仓库），"
                                  "决策官只能按任务描述盲拆，文件路径可能不准。"})

            # ---- 1) 决策
            plan = self._stage_plan(spec)
            if not plan["items"]:
                raise TeamTaskError(
                    "决策官没有拆出任何工作项（模型输出可能不是合法 JSON，可重试或换模型）")

            # ---- 2) 逐工作项：编码 → 测试
            self.stopped = not self._run_items(spec, plan)

            # ---- 3) 复核 + 有界返工
            review: Optional[Dict] = None
            rounds = 0
            max_rounds = int(spec.get("max_rounds") or 2)
            if "reviewer" in self.enabled and not self.stopped:
                review = self._stage_review(spec, plan)
            while (not self.stopped) and _needs_repair(review) and rounds < max_rounds \
                    and "coder" in self.enabled:
                rounds += 1
                self._ev({"type": "log", "level": "warn",
                          "text": f"复核判定需要返工（{review.get('verdict')}），"
                                  f"开始第 {rounds} 轮修复"})
                self._add_fix_items(plan, review, rounds)
                self.stopped = not self._run_items(spec, plan)
                if self.stopped:
                    break
                review = self._stage_review(spec, plan, repair_round=rounds)

            # ---- 4) 产出物（中途终止也要交付已完成的部分）
            artifact = self._create_artifact(spec, plan, review)
            status = "aborted" if self.stopped else ("done" if artifact else "failed")
            summary = (review or {}).get("summary") or plan.get("summary") or ""
            if self.stopped:
                summary = "人工终止：" + (summary or "已交付完成的部分")
            finish_extra = {
                "plan": plan, "output_files": list(self.files.keys()),
                "summary": summary, "verdict": (review or {}).get("verdict", ""),
                "review": review or {},
                "artifact": (artifact or {}).get("id", ""),
                "error": "" if artifact else "没有任何文件产出，未生成产出物",
            }
            self.store.finish(self.rid, status, **finish_extra)
            self.record.update(finish_extra)
            self.record["status"] = status
            self._ev({"type": "run_finish", "status": status,
                      "summary": summary, "verdict": (review or {}).get("verdict", ""),
                      "artifact": (artifact or {}).get("id", "")})
            # SSE 收尾契约与 /api/run、/api/probe 一致：必须以 done / fatal 结束，
            # 否则前端会一直挂在连接上（只有心跳，永远等不到结果）。
            payload = {"run_id": self.rid, "status": status, "summary": summary,
                       "verdict": (review or {}).get("verdict", ""),
                       "artifact": (artifact or {}).get("id", ""),
                       "file_count": len(self.files),
                       "item_total": len(plan.get("items") or [])}
            self._ev({"type": "done", "payload": payload})
            out = {"ok": bool(artifact), "run": self.record,
                   "artifact": artifact or {}, "review": review or {}}
            out.update(payload)
            return out
        except TeamTaskError as e:
            self.store.finish(self.rid, "failed", error=str(e))
            self.record["status"] = "failed"
            self.record["error"] = str(e)
            self._ev({"type": "fatal", "error": str(e)})
            return {"ok": False, "run": self.record, "error": str(e)}
        except Exception as e:  # 兜底：任何异常都要在界面上有交代
            msg = f"{type(e).__name__}: {e}"
            self.store.finish(self.rid, "failed", error=msg)
            self.record["status"] = "failed"
            self.record["error"] = msg
            self._ev({"type": "fatal", "error": msg})
            return {"ok": False, "run": self.record, "error": msg}

    @staticmethod
    def _role_card(role: Dict) -> Dict:
        return {
            "id": role["id"], "name": role["name"], "emoji": role.get("emoji", ""),
            "color": role.get("color", ""), "stage": role.get("stage", ""),
            "duty": role.get("duty", ""), "outputs": role.get("outputs", ""),
            "status": "idle", "current": "", "progress": {"done": 0, "total": 0},
            "files": [], "notes": [],
        }

    # ------------------------------------------------------- 阶段：规划
    def _stage_plan(self, spec: Dict) -> Dict:
        self._set_agent("planner", "working", current="拆解任务、制定方案…")
        prompt = self._planner_prompt(spec)
        res = self._ask("planner", prompt, mock={"spec": spec})
        plan = _norm_plan(res)
        if not plan["summary"]:
            plan["summary"] = _clip(res.get("error") or "（决策官没有给出方案摘要）", 300)
        self.store.set_plan(self.rid, plan)
        self._set_agent("planner", "done" if plan["items"] else "error",
                        current=plan["summary"] or "没有拆出工作项",
                        progress={"done": 0, "total": len(plan["items"])})
        self._ev({"type": "plan", "plan": plan, "revised": False})
        return plan

    def _replan(self, spec: Dict, plan: Dict, notes: List[str], reason: str) -> Dict:
        self._set_agent("planner", "working", current="根据人工指令重新规划…")
        prompt = self._planner_prompt(spec, revise=True, prev=plan, reason=reason)
        res = self._ask("planner", prompt, mock={"spec": spec}, extra_notes=notes)
        fresh = _norm_plan(res)
        if not fresh["items"]:
            self._ev({"type": "log", "level": "warn",
                      "text": "重规划没有产出新的工作项，沿用原计划继续"})
            self._set_agent("planner", "working", current="沿用原计划（重规划无有效输出）")
            return plan
        # 已完成/已跳过的工作项保留，不让重规划把它们抹掉
        kept = [it for it in plan.get("items") or [] if it.get("status") != "todo"]
        used = {it["id"] for it in kept}
        for i, it in enumerate(fresh["items"], 1):
            if it["id"] in used:
                it["id"] = f"{it['id']}-r{i}"
            used.add(it["id"])
        new_plan = {**fresh, "items": kept + fresh["items"]}
        self.store.set_plan(self.rid, new_plan)
        self._set_agent("planner", "working", current="已按人工指令更新计划",
                        progress=self._progress(new_plan))
        self._ev({"type": "plan", "plan": new_plan, "revised": True, "reason": reason})
        return new_plan

    # ----------------------------------------------------- 阶段：逐工作项
    def _run_items(self, spec: Dict, plan: Dict) -> bool:
        """按计划推进所有 todo 工作项。返回 False 表示被人为终止。"""
        replans = 0
        max_rounds = int(spec.get("max_rounds") or 2)
        while True:
            pending = [it for it in plan.get("items") or [] if it.get("status") == "todo"]
            if not pending:
                return True
            item = pending[0]

            # 边界 1：决策官视角的人工指令（追加要求 / 重规划 / 跳过）
            if "planner" in self.enabled:
                ck = self.bus.checkpoint("planner", self._ev)
                self._ack(ck, "planner")
                if ck["stop"]:
                    return False
                if ck.get("skip") and self._apply_skips(plan, ck["skip"], item):
                    continue
                if (ck["notes"] or ck["replan"]) and replans < max_rounds:
                    replans += 1
                    plan = self._replan(
                        spec, plan, ck["notes"],
                        reason="人工要求重新规划" if ck["replan"] else "人工补充/纠正了要求")
                    continue

            # 边界 2：编码前（发给编码工程师的指令在这里被消费）
            ck = self.bus.checkpoint("coder", self._ev)
            self._ack(ck, "coder")
            if ck["stop"]:
                return False
            if ck.get("skip") and self._apply_skips(plan, ck["skip"], item):
                continue

            # 单个工作项失败不终止整轮：长任务里"部分完成"依然有价值，
            # 失败会记在该工作项上并出现在最终产出物的说明里。
            self._implement(spec, plan, item, ck["notes"])

            # 边界 3：测试
            if "tester" in self.enabled and item["status"] == "done":
                ck_t = self.bus.checkpoint("tester", self._ev)
                self._ack(ck_t, "tester")
                if ck_t["stop"]:
                    return False
                self._stage_test(spec, plan, item, ck_t["notes"])

            self._set_agent("planner", current=f"计划进度 {self._progress(plan)['done']}/"
                                               f"{self._progress(plan)['total']}",
                            progress=self._progress(plan))
            self.store.set_plan(self.rid, plan)

    def _apply_skips(self, plan: Dict, skips: List[str], current: Dict) -> bool:
        """执行「跳过」指令；返回当前工作项是否已被跳过。

        `@current` 表示"跳过正在做的这一项"，与写死的 id 区分开——否则界面上的
        「跳过」按钮会把计划里所有未开始的工作项一次性全跳掉。
        """
        skip_current = "@current" in skips
        hit_current = False
        for it in plan.get("items") or []:
            if it.get("status") != "todo":
                continue
            if it["id"] not in skips and not (skip_current and it["id"] == current["id"]):
                continue
            if it["id"] == current["id"]:
                hit_current = True
            it["status"] = "skipped"
            it["note"] = "按人工指令跳过"
            self._ev({"type": "item_status", "item": it["id"], "status": "skipped",
                      "title": it["title"], "note": it["note"],
                      "progress": self._progress(plan)})
        self.store.set_plan(self.rid, plan)
        return hit_current

    def _implement(self, spec: Dict, plan: Dict, item: Dict, notes: List[str]):
        """一个工作项的实现——长任务拆分的核心：每次只让编码工程师专注一项。"""
        item["status"] = "doing"
        self.store.set_plan(self.rid, plan)
        self._ev({"type": "item_status", "item": item["id"], "status": "doing",
                  "title": item["title"], "progress": self._progress(plan)})
        self._set_agent("coder", "working",
                        current=f"{item['id']} {item['title']}",
                        progress=self._progress(plan))

        ctx = self._read_files(item.get("files") or [])
        prompt = self._coder_prompt(spec, plan, item, ctx)
        res = self._ask("coder", prompt, mock={"spec": spec, "item": item},
                        extra_notes=notes)
        files = _norm_files(res.get("files"), "coder", MAX_ITEM_FILES)
        if not files:
            item["status"] = "failed"
            item["note"] = _clip(res.get("error") or "编码工程师没有输出任何文件", 300)
            self.store.set_plan(self.rid, plan)
            self._set_agent("coder", "working",
                            current=f"{item['id']} 未产出文件",
                            progress=self._progress(plan), note=item["note"])
            self._ev({"type": "item_status", "item": item["id"], "status": "failed",
                      "title": item["title"], "note": item["note"],
                      "progress": self._progress(plan)})
            return None

        for f in files:
            self.files[f["path"]] = f
        item["status"] = "done"
        item["note"] = _clip(res.get("summary"), 300)
        self.store.set_plan(self.rid, plan)
        self._set_agent("coder", "working",
                        current=f"已完成 {item['id']}：{item['title']}",
                        progress=self._progress(plan),
                        files=[f["path"] for f in files])
        self._ev({"type": "item_status", "item": item["id"], "status": "done",
                  "title": item["title"], "note": item["note"],
                  "progress": self._progress(plan)})
        self._ev({"type": "item_files", "agent": "coder", "item": item["id"],
                  "files": [{k: f[k] for k in ("path", "action", "description")}
                            for f in files],
                  "notes": _str_list(res.get("notes"), 8)})
        return files

    def _stage_test(self, spec: Dict, plan: Dict, item: Dict, notes: List[str]) -> None:
        wanted = set(item.get("files") or [])
        produced = [f for f in self.files.values()
                    if f.get("by") == "coder" and f["path"] in wanted]
        if not produced:
            # 决策官没指定文件时，退化为「本项刚产出的文件」——否则测试环节会静默跳过
            produced = [f for f in self.files.values() if f.get("by") == "coder"][-2:]
        if not produced:
            return
        self._set_agent("tester", "working", current=f"为 {item['id']} 写测试…")
        prompt = self._tester_prompt(spec, plan, item, produced)
        res = self._ask("tester", prompt, mock={"spec": spec, "item": item})
        files = _norm_files(res.get("files"), "tester", MAX_TEST_FILES)
        for f in files:
            self.files[f["path"]] = f
        cases = res.get("cases") if isinstance(res.get("cases"), list) else []
        gaps = _str_list(res.get("gaps"), 8)
        self._set_agent("tester", "working",
                        current=f"{item['id']} 已写 {len(cases)} 个用例",
                        note="；".join(gaps[:2]),
                        files=[f["path"] for f in files])
        self._ev({"type": "item_files", "agent": "tester", "item": item["id"],
                  "files": [{k: f[k] for k in ("path", "action", "description")}
                            for f in files],
                  "cases": cases[:20], "gaps": gaps})

    # ------------------------------------------------------- 阶段：复核
    def _stage_review(self, spec: Dict, plan: Dict, repair_round: int = 0) -> Dict:
        self._set_agent("reviewer", "working", current="通盘审查产出…")
        prompt = self._reviewer_prompt(spec, plan)
        res = self._ask("reviewer", prompt, mock={"spec": spec})
        review = _norm_review(res)
        self._set_agent("reviewer", "done" if review["verdict"] != "block" else "error",
                        current=review["summary"] or review["verdict"],
                        note="；".join(review["risks"][:2]))
        self._ev({"type": "review", "verdict": review["verdict"],
                  "summary": review["summary"], "risks": review["risks"],
                  "must_fix": review["must_fix"], "suggestions": review["suggestions"],
                  "round": repair_round})
        return review

    def _add_fix_items(self, plan: Dict, review: Dict, round_no: int) -> None:
        for i, m in enumerate(review.get("must_fix") or [], 1):
            plan["items"].append({
                "id": f"fix{round_no}-{i}",
                "title": f"返工：{m.get('item') or '复核提出的问题'}",
                "detail": f"复核意见：{m.get('why')}\n修复方式：{m.get('how')}",
                "files": [],
                "acceptance": "复核提出的问题已修复",
                "status": "todo",
                "note": f"由复核官第 {round_no} 轮提出",
            })
        self.store.set_plan(self.rid, plan)
        self._ev({"type": "plan", "plan": plan, "revised": True,
                  "reason": f"复核官第 {round_no} 轮要求返工"})

    # ------------------------------------------------------- 阶段：产出物
    def _create_artifact(self, spec: Dict, plan: Dict, review: Optional[Dict]) -> Optional[Dict]:
        if not self.files:
            return None
        title = (spec.get("title") or spec.get("task") or "长任务").strip()[:120]
        if self.stopped:
            title = "[已终止·部分产出] " + title
        payload = {
            "ai_mode": self.ai.mode,
            "summary": (review or {}).get("summary") or plan.get("summary", ""),
            "plan": self._plan_text(plan),
            "checklist": plan.get("checklist") or [],
            "files": list(self.files.values()),
            "team": {
                "run_id": self.rid,
                "roles": [{k: a.get(k) for k in ("id", "name", "emoji", "stage",
                                                 "status", "files")}
                          for a in self.agents],
                "items": plan.get("items") or [],
                "review": review or {},
                "aborted": bool(self.stopped),
                "scope": spec.get("scope") or "",
            },
        }
        artifact = self.artifacts.create("team", title, payload, ctx=spec)
        self._ev({"type": "artifact", "id": artifact["id"], "title": artifact["title"],
                  "file_count": len(self.files),
                  "roles": sorted({f.get("by", "") for f in self.files.values()})})
        return artifact

    # ------------------------------------------------------------ 模型调用
    def _ask(self, agent_id: str, prompt: str, mock: Optional[Dict] = None,
             extra_notes: Optional[List[str]] = None) -> Dict:
        role = get_role(agent_id)
        if extra_notes:
            prompt = prompt + inject_human(extra_notes)
        if self.ai.cfg.is_mock:
            res = self._mock_reply(agent_id, mock or {})
            self._ev({"type": "agent_delta", "agent": agent_id, "kind": "content",
                      "text": "（Mock 模式：未调用真实模型，以下为演示输出）\n"})
            return res

        def on_delta(kind: str, text: str) -> None:
            self._ev({"type": "agent_delta", "agent": agent_id,
                      "kind": kind, "text": text})

        try:
            # 用量按角色分开记：这样「哪个子 Agent 最烧 token」能直接看出来
            with usage.step(agent_id, agent=agent_id):
                res = self.ai.complete(prompt, role["system"], max_tokens=AI_BUDGET,
                                       on_delta=on_delta)
        except Exception as e:      # 网络层异常不该让整轮运行崩掉
            res = {"error": f"{type(e).__name__}: {e}"}
        if res.get("error"):
            self._ev({"type": "log", "level": "bad",
                      "text": f"「{role['name']}」调用异常：{_clip(res.get('error'), 300)}"})
            self._set_agent(agent_id, "error",
                            current=_clip(res.get("error"), 200), announce=True)
        return res

    # -------------------------------------------------------------- 提示词
    def _planner_prompt(self, spec: Dict, revise: bool = False, prev: Optional[Dict] = None,
                        reason: str = "") -> str:
        parts = [
            f"## 任务\n标题：{spec.get('title') or '（未填）'}\n{(spec.get('task') or '').strip()[:6000]}",
            f"## 改造范围\n{spec.get('scope') or '（未限定，请根据任务描述自行判断）'}",
            f"## 技术栈\n{spec.get('framework') or '（未指定，沿用仓库既有技术栈）'}",
        ]
        if (spec.get("notes") or "").strip():
            parts.append(f"## 其他约定\n{_clip(spec['notes'], 2000)}")
        if self.repo_path:
            parts.append(f"## 目标仓库\n{self.repo_path}")
        if self.repo_files:
            shown = self.repo_files[:MAX_LISTED_FILES]
            tail = "" if len(self.repo_files) <= MAX_LISTED_FILES else \
                f"\n（共 {len(self.repo_files)} 个文件，此处只列前 {MAX_LISTED_FILES} 个）"
            parts.append("## 仓库文件清单（判断路径用，**不要臆造不存在的路径**）\n"
                         + "\n".join(shown) + tail)
        skills = (spec.get("skills_text") or "").strip()
        if skills:
            parts.append(f"## 团队技能与规范（必须遵守）\n{_clip(skills, 4000)}")
        if revise and prev:
            parts.append(
                "## 这是**重新规划**，不是从零开始\n"
                f"原因：{reason or '人工要求调整'}\n"
                f"上一版计划：\n{self._plan_text(prev)}\n"
                "上表里状态为 done / skipped 的工作项**不要重做**；"
                "请针对还没做的部分给出新的 items（必要时可以调整已完成项之后的做法）。")
        parts.append(
            "## 输出要求\n"
            "只输出 JSON（字段见系统提示）。items 3~8 个，按依赖顺序排列；"
            "每个工作项都要写明涉及的**真实文件路径**；"
            "工作项粒度控制在一轮内能改完（不要出现「把所有文件都改一遍」这种无法验收的项）。")
        return "\n\n".join(parts)

    def _coder_prompt(self, spec: Dict, plan: Dict, item: Dict, ctx: List[Tuple[str, str]]) -> str:
        others = [f"- [{it['status']}] {it['id']} {it['title']}"
                  for it in plan.get("items") or []]
        parts = [
            f"## 任务\n{(spec.get('title') or '')}\n{_clip(spec.get('task'), 3000)}",
            f"## 整体方案\n{plan.get('summary', '')}",
            "## 计划全貌（你只做「当前工作项」，其他项只作上下文）\n" + "\n".join(others[:20]),
            f"## 当前工作项\n{item['id']} · {item['title']}\n{item.get('detail', '')}\n"
            f"验收标准：{item.get('acceptance') or '（未指定）'}",
        ]
        if ctx:
            blocks = []
            for path, content in ctx:
                blocks.append(f"### {path}\n```\n{content}\n```")
            parts.append("## 涉及文件的当前内容（基于此改写，不要凭空重写无关部分）\n"
                         + "\n\n".join(blocks))
        else:
            parts.append("## 涉及文件的当前内容\n（没有读到已有内容：这些文件不存在，属于新建）")
        done = [f for f in self.files.values() if f["path"] not in (item.get("files") or [])]
        if done:
            parts.append("## 已经产出的文件（其他工作项/本项早前的产出，避免冲突；"
                         "若你本次要改其中某个文件，必须输出该文件的完整新内容）\n"
                         + "\n".join(f"- {f['path']}（{f.get('by', '')}）："
                                    f"{f.get('description', '')}" for f in done[-20:]))
        parts.append("## 输出要求\n只输出 JSON（字段见系统提示）。"
                     f"文件数不超过 {MAX_ITEM_FILES} 个，content 必须是完整文件内容。")
        return "\n\n".join(parts)

    def _tester_prompt(self, spec: Dict, plan: Dict, item: Dict, produced: List[Dict]) -> str:
        blocks = []
        for f in produced[:3]:
            blocks.append(f"### {f['path']}（{f.get('description', '')}）\n"
                          f"```\n{_clip(f.get('content'), CTX_FILE_CHARS)}\n```")
        return "\n\n".join([
            f"## 任务\n{_clip(spec.get('task'), 1500)}",
            f"## 本次工作项\n{item['id']} · {item['title']}\n{item.get('detail', '')}",
            "## 这一项的产出（完整内容）\n" + "\n\n".join(blocks),
            f"## 测试框架\n{spec.get('framework') or '（沿用仓库既有测试框架，未配置则用常见约定）'}",
            "## 输出要求\n只输出 JSON（字段见系统提示）。"
            f"最多 {MAX_TEST_FILES} 个测试文件，内容要能直接运行。",
        ])

    def _reviewer_prompt(self, spec: Dict, plan: Dict) -> str:
        blocks = []
        for f in list(self.files.values())[:30]:
            blocks.append(f"### {f['path']}（{f.get('by', '')}｜{f.get('description', '')}）\n"
                          f"```\n{_clip(f.get('content'), REVIEW_FILE_CHARS)}\n```")
        notes = []
        for a in self.agents:
            for n in (a.get("notes") or [])[-2:]:
                notes.append(f"- {a['name']}：{n}")
        parts = [
            f"## 任务\n{_clip(spec.get('task'), 2000)}",
            f"## 计划与工作项完成情况\n{self._plan_text(plan)}",
            f"## 计划里声明的验收清单\n" + "\n".join(f"- {c}" for c in plan.get("checklist") or []),
            f"## 全部产出文件（内容已截断，只用于结构审查）\n" + "\n\n".join(blocks),
        ]
        if notes:
            parts.append("## 各角色留下的说明\n" + "\n".join(notes[:12]))
        parts.append(
            "## 输出要求\n只输出 JSON（字段见系统提示）。重点找："
            "① 漏改的地方（改造不彻底）；② 破坏既有行为；③ 类型/接口不一致；"
            "④ 测试没覆盖的关键分支。没有问题就老实说 pass，不要为了显得认真而编造问题。")
        return "\n\n".join(parts)

    # ------------------------------------------------------------ 仓库读取
    def _list_repo_files(self, scope: str) -> List[str]:
        root = Path(self.repo_path) if self.repo_path else None
        files: List[str] = []
        if root and root.is_dir():
            try:
                proc = subprocess.run(["git", "ls-files", "--exclude-standard"],
                                      cwd=str(root), capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=25)
                files = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]
            except Exception:
                files = []
            if not files:      # 非 git 仓库：退化为目录扫描
                try:
                    files = [str(p.relative_to(root)).replace("\\", "/")
                             for p in root.rglob("*")
                             if p.is_file() and not (set(p.parts) & SKIP_DIRS)]
                except Exception:
                    files = []
        if scope:
            pats = [s for s in re.split(r"[,;\s]+", scope) if s]
            hit = [f for f in files if any(_match_glob(f, p) for p in pats)]
            if hit:
                files = hit
            else:
                self._ev({"type": "log", "level": "warn",
                          "text": f"范围「{scope}」没有匹配到任何文件，已按全仓库清单继续"})
        return files[:2000]

    def _read_files(self, paths: List[str]) -> List[Tuple[str, str]]:
        """读「当前内容」：**内存里的产出优先于磁盘**——同一文件被前一个工作项
        改过时，磁盘上还是旧内容，拿旧的去改写会把前一项的改动抹掉。"""
        root = Path(self.repo_path) if self.repo_path else None
        out: List[Tuple[str, str]] = []
        total = 0
        for p in paths[:MAX_CTX_FILES]:
            content = ""
            if p in self.files:
                content = self.files[p].get("content", "")
            elif root and root.is_dir():
                target = safe_rel_path(root, p)
                if target and target.is_file():
                    try:
                        content = target.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        content = ""
            if not content:
                continue
            content = content[:CTX_FILE_CHARS]
            if total + len(content) > CTX_TOTAL_CHARS:
                break
            total += len(content)
            out.append((p, content))
        return out

    @staticmethod
    def _plan_text(plan: Dict) -> str:
        lines = []
        for it in plan.get("items") or []:
            files = "、".join(it.get("files") or []) or "未指定"
            lines.append(f"- [{it.get('status', 'todo')}] {it.get('id')} {it.get('title')}"
                         f"｜{_clip(it.get('detail'), 200)}｜文件：{files}"
                         + (f"｜备注：{_clip(it.get('note'), 120)}" if it.get("note") else ""))
        return "\n".join(lines) or "（无工作项）"

    # ------------------------------------------------------------ Mock 演示
    def _mock_reply(self, agent_id: str, ctx: Dict) -> Dict:
        """离线演示输出。刻意把路径限制在 `src/__team_mock__/` 下，
        避免 demo 产出被采纳后覆盖真实文件。"""
        if agent_id == "planner":
            spec = ctx.get("spec") or {}
            base = re.sub(r"[^A-Za-z0-9]+", "-", (spec.get("title") or "task")).strip("-")[:24] or "task"
            items = [
                {"id": "t1", "title": f"（Mock 演示）梳理 {base} 的现状与公共依赖",
                 "detail": "（Mock 演示内容）盘点受影响的模块与公共依赖，列出改造清单。",
                 "files": [], "acceptance": "产出一份改造清单"},
                {"id": "t2", "title": "（Mock 演示）改造核心实现",
                 "detail": "（Mock 演示内容）按新方案重写核心逻辑，保持对外接口不变。",
                 "files": [], "acceptance": "核心实现完成且类型通过"},
                {"id": "t3", "title": "（Mock 演示）补齐类型导出与兼容层",
                 "detail": "（Mock 演示内容）补类型声明与过渡适配，保证调用方无需改动。",
                 "files": [], "acceptance": "调用方无需修改即可编译"},
            ]
            return {"summary": "（Mock 演示）把任务拆成 3 个工作项：盘点 → 改造 → 兼容收口。",
                    "items": items,
                    "risks": ["（Mock 演示）没有真实读取仓库内容，路径与结论仅供界面联调"],
                    "checklist": ["（Mock 演示）逐项跑 type-check", "（Mock 演示）回归核心链路"]}
        if agent_id == "coder":
            item = ctx.get("item") or {}
            iid = _clip(item.get("id") or "t1", 16)
            path = f"{MOCK_PREFIX}{iid}.ts"
            return {
                "summary": f"（Mock 演示）实现工作项 {iid}",
                "files": [{
                    "path": path, "action": "create",
                    "description": "（Mock 演示）该工作项的代码产出",
                    "content": f"// MOCK 演示内容 —— 配置真实大模型后会生成完整实现\n"
                               f"// 工作项：{_clip(item.get('title'), 80)}\n"
                               f"export function {re.sub(r'[^A-Za-z0-9]', '', iid) or 'task'}() {{\n"
                               f"  return 'mock';\n}}\n",
                }],
                "notes": ["（Mock 演示）真实跑批时会输出整份改动文件"],
            }
        if agent_id == "tester":
            item = ctx.get("item") or {}
            iid = _clip(item.get("id") or "t1", 16)
            name = re.sub(r"[^A-Za-z0-9]", "", iid) or "task"
            return {
                "summary": f"（Mock 演示）为工作项 {iid} 写 3 个用例",
                "cases": [{"name": f"{name} 正常路径", "purpose": "（Mock 演示）主流程可用", "kind": "normal"},
                          {"name": f"{name} 边界值", "purpose": "（Mock 演示）空输入", "kind": "edge"},
                          {"name": f"{name} 异常分支", "purpose": "（Mock 演示）依赖抛错时的表现", "kind": "error"}],
                "files": [{
                    "path": f"{MOCK_PREFIX}{iid}.test.ts", "action": "create",
                    "description": "（Mock 演示）单元测试",
                    "content": f"// MOCK 演示内容\nimport {{ describe, it, expect }} from 'vitest';\n\n"
                               f"describe('{iid}', () => {{\n"
                               f"  it('works', () => {{ expect(true).toBe(true); }});\n}});\n",
                }],
                "gaps": ["（Mock 演示）未接入真实断言"],
            }
        return {
            "verdict": "pass_with_risks",
            "summary": "（Mock 演示）结构完整，但未经过真实类型检查与测试执行。",
            "risks": ["（Mock 演示）这是离线演示输出，不代表真实复核结论"],
            "must_fix": [],
            "suggestions": ["（Mock 演示）配置真实模型后重跑本任务"],
        }
