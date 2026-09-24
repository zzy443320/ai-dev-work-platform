"""Proposal store — the human-approval boundary.

The pipeline writes a *proposal* (diff + gate result + screenshots) and stops.
Nothing touches the target repo until someone approves a proposal, which is the
only code path allowed to call CodeFixer.apply().
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

STATUS_PENDING = "pending"
STATUS_GATE_FAILED = "gate_failed"
STATUS_INVALID = "invalid"        # patch could not be constructed at all
STATUS_APPLIED = "applied"
STATUS_APPLY_FAILED = "apply_failed"
STATUS_REJECTED = "rejected"

OPEN_STATUSES = (STATUS_PENDING, STATUS_GATE_FAILED, STATUS_INVALID)
TERMINAL_STATUSES = (STATUS_APPLIED, STATUS_APPLY_FAILED, STATUS_REJECTED)


def _slug(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", str(s or "local")).strip("-")[:60] or "local"


class ProposalStore:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    # ---------- paths ----------
    def _path(self, pid: str) -> Path:
        return self.base / f"{_slug(pid)}.json"

    # ---------- write ----------
    def create(self, defect: Dict, analysis: Dict, patch: Dict, gate: Dict,
               verify: Optional[Dict] = None) -> Dict:
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
            "apply": {},
            "decision": {},
            "kb_path": "",
        }
        self._write(proposal)
        return proposal

    def update(self, proposal: Dict) -> Dict:
        proposal["updated"] = datetime.now().isoformat(timespec="seconds")
        self._write(proposal)
        return proposal

    def set_status(self, pid: str, status: str, **extra) -> Optional[Dict]:
        p = self.get(pid)
        if not p:
            return None
        p["status"] = status
        p.update(extra)
        return self.update(p)

    def _write(self, proposal: Dict) -> None:
        target = self._path(proposal["id"])
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(proposal, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(target)

    # ---------- read ----------
    def get(self, pid: str) -> Optional[Dict]:
        path = self._path(pid)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

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
        }
