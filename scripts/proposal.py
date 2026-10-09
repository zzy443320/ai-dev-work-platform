"""Proposal store — the human-approval boundary.

The pipeline writes a *proposal* (diff + gate result + screenshots) and stops.
Nothing touches the target repo until someone approves a proposal, which is the
only code path allowed to call CodeFixer.apply().
"""
import json
from datetime import datetime
from typing import Dict, List, Optional

from .json_store import JsonRecordStore
from .textutil import slug

STATUS_PENDING = "pending"
STATUS_GATE_FAILED = "gate_failed"
STATUS_INVALID = "invalid"        # patch could not be constructed at all
STATUS_APPLIED = "applied"
STATUS_APPLY_FAILED = "apply_failed"
STATUS_REJECTED = "rejected"

OPEN_STATUSES = (STATUS_PENDING, STATUS_GATE_FAILED, STATUS_INVALID)
TERMINAL_STATUSES = (STATUS_APPLIED, STATUS_APPLY_FAILED, STATUS_REJECTED)

# 人工自测后「没修好」的四种判据。做成枚举而不是自由文本，是因为这句话要被机器用：
# 重跑时它进 prompt 当硬判据（复现用例必须覆盖这条现象），事后它还要参与返工率统计。
# 前端下拉里的四项文案与此一一对应（frontend/src/components/ProposalModal.vue）。
REWORK_VERDICTS = {
    "not_fixed": "现象完全没变",
    "partial": "有变化，但没修对/只修了一半",
    "regression": "引出别的问题",
    "cannot_test": "环境或入口原因，我没法自测",
}


def rework_text(verdict: str, detail: str = "") -> str:
    """把返工判据拼成一句人读的话（同时进 decision.note 与知识卡片）。"""
    label = REWORK_VERDICTS.get(str(verdict or "").strip(), "自测未通过")
    body = str(detail or "").strip()
    return f"【人工返工】{label}" + (f"：{body}" if body else "")


def _slug(s: str) -> str:
    return slug(s, maxlen=60, default="local")


class ProposalStore(JsonRecordStore):
    """一份提案 = proposals/<id>.json。读写与加锁语义见 scripts/json_store.py。

    注意锁的归属：这里原先用的是**实例级** RLock，而服务端每个请求都新建一个
    ProposalStore（web/state.py 的 _store()）——实例级锁跨请求根本不共享，等于没加。
    现在由基类提供类级 RLock。
    """

    SLUG_LEN = 60
    DEFAULT_SLUG = "local"

    # ---------- write ----------
    def create(self, defect: Dict, analysis: Dict, patch: Dict, gate: Dict,
               verify: Optional[Dict] = None, agent: Optional[Dict] = None) -> Dict:
        now = datetime.now().isoformat(timespec="seconds")
        if patch.get("ok"):
            status = STATUS_PENDING if gate.get("ok", True) else STATUS_GATE_FAILED
        else:
            status = STATUS_INVALID
        pid = f"{_slug(defect.get('id') or 'DEFECT')}-{datetime.now():%Y%m%d-%H%M%S}"
        proposal = {
            "id": pid,
            "created": now,
            "updated": now,
            "status": status,
            "defect": defect,
            "analysis": analysis,
            "patch": patch,
            "gate": gate,
            "verify": verify or {},
            # agentic 修复循环的完整轨迹：轮次、每次候选补丁的真实验证输出、收敛结论。
            # 它是「这份补丁凭什么可信」的唯一凭据，审批界面必须能展开看到原文。
            "agent": agent or {},
            "apply": {},
            "decision": {},
            "kb_path": "",
        }
        # 提案 id 精确到秒：同一条工单在同一秒内跑两次会撞同一个文件名。create 是整份
        # 写入（不是读-改-写），但仍在锁内，避免与并发的 update 交叉。
        with self._lock:
            self._write(proposal)
        return proposal

    # ---------- read ----------
    def list(self, status: Optional[str] = None) -> List[Dict]:
        out: List[Dict] = []
        for f in sorted(self.base.glob("*.json"), key=lambda p: p.name):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if status and p.get("status") != status:
                continue
            out.append(self.summary(p))
        return sorted(out, key=lambda x: x.get("created", ""), reverse=True)

    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for s in self.list():
            c[s["status"]] = c.get(s["status"], 0) + 1
        return c

    @staticmethod
    def summary(p: Dict) -> Dict:
        patch = p.get("patch") or {}
        changes = [c for c in patch.get("changes", []) if not c.get("error")]
        added = sum((c.get("stats") or {}).get("added", 0) for c in changes)
        removed = sum((c.get("stats") or {}).get("removed", 0) for c in changes)
        gate = p.get("gate") or {}
        agent = p.get("agent") or {}
        return {
            "id": p.get("id"),
            "status": p.get("status"),
            "created": p.get("created"),
            "updated": p.get("updated"),
            "defect_id": (p.get("defect") or {}).get("id"),
            "title": (p.get("defect") or {}).get("title", ""),
            "priority": (p.get("defect") or {}).get("priority", ""),
            "category": (p.get("analysis") or {}).get("category", ""),
            "ai_mode": (p.get("analysis") or {}).get("ai_mode", ""),
            "locate_empty": bool((p.get("analysis") or {}).get("locate_empty")),
            "root_cause": ((p.get("analysis") or {}).get("root_cause", "") or "")[:200],
            "files": [c.get("file_path") for c in changes],
            "added": added,
            "removed": removed,
            "gate_level": gate.get("level"),
            "gate_ok": gate.get("ok", True),
            "gate_failed": gate.get("failed_checks", []),
            "warning_count": len(patch.get("warnings", []) or []),
            "error_count": len(patch.get("errors", []) or []),
            "sha": (p.get("apply") or {}).get("sha", ""),
            # 修复循环的结论摘要：列表页不加载整份提案也能标出「真跑过 / 没跑过 / 认输」
            "agent_conclusion": agent.get("conclusion", ""),
            "agent_rounds": agent.get("rounds", 0),
            "agent_attempts": len(agent.get("attempts") or []),
            "agent_needs_human": bool(agent.get("needs_human")),
            "agent_verified": bool(agent.get("conclusion")
                                   in ("verified", "checks_pass")),
            "sandbox_available": bool((agent.get("sandbox") or {}).get("available")),
        }
