"""Artifact store + applier — the human-approval boundary for the three
frontend-dev tasks (reqdev / apidebug / codetest), same contract as proposals:

The task runner only ever produces an *artifact* (files + plan). Nothing touches
the target repo until someone approves, which is the only code path that writes
files. Apply writes to the working tree with per-file backups, runs the gate,
auto-restores on failure. No commit, no push.
"""
import difflib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_APPLY_FAILED = "apply_failed"
STATUS_REJECTED = "rejected"

OPEN_STATUSES = (STATUS_PENDING, STATUS_APPLY_FAILED)
TERMINAL_STATUSES = (STATUS_APPLIED, STATUS_REJECTED)

TYPE_LABELS = {
    "chat": "问答",
    "reqdev": "需求开发",
    "apidebug": "接口联调",
    "codetest": "代码测试",
    # 长任务作业（多子 Agent 协作），见 scripts/team_run.py
    "team": "长任务作业",
}


def _slug(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", str(s or "task")).strip("-")[:40] or "task"


class ArtifactStore:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, aid: str) -> Path:
        return self.base / f"{_slug(aid)}.json"

    def create(self, kind: str, title: str, payload: Dict, ctx: Optional[Dict] = None) -> Dict:
        now = datetime.now().isoformat(timespec="seconds")
        aid = f"{_slug(kind)}-{datetime.now():%Y%m%d-%H%M%S}"
        artifact = {
            "id": aid,
            "type": kind,
            "type_label": TYPE_LABELS.get(kind, kind),
            "title": (title or "（无标题）")[:120],
            "created": now,
            "updated": now,
            "status": STATUS_PENDING,
            "ai_mode": payload.get("ai_mode", ""),
            "summary": payload.get("summary", ""),
            "plan": payload.get("plan", ""),
            "checklist": payload.get("checklist", []),
            "endpoints": payload.get("endpoints", []),
            "mock_data": payload.get("mock_data", ""),
            "cases": payload.get("cases", []),
            "files": payload.get("files", []),
            "error": payload.get("error", ""),
            "raw": payload.get("raw", ""),
            # 长任务作业的协作上下文（角色卡、工作项、复核结论）；其他类型为空
            "team": payload.get("team") or {},
            "context": _ctx_summary(ctx or {}),
            "apply": {},
            "decision": {},
        }
        self._write(artifact)
        return artifact

    def update(self, artifact: Dict) -> Dict:
        artifact["updated"] = datetime.now().isoformat(timespec="seconds")
        self._write(artifact)
        return artifact

    def set_status(self, aid: str, status: str, **extra) -> Optional[Dict]:
        a = self.get(aid)
        if not a:
            return None
        a["status"] = status
        a.update(extra)
        return self.update(a)

    def _write(self, artifact: Dict) -> None:
        target = self._path(artifact["id"])
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(target)

    def get(self, aid: str) -> Optional[Dict]:
        path = self._path(aid)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list(self, type_filter: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
        out: List[Dict] = []
        for f in sorted(self.base.glob("*.json"), key=lambda p: p.name, reverse=True):
            try:
                a = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if type_filter and a.get("type") != type_filter:
                continue
            if status and a.get("status") != status:
                continue
            out.append(self.summary(a))
        return out

    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for s in self.list():
            c[s["status"]] = c.get(s["status"], 0) + 1
        return c

    @staticmethod
    def summary(a: Dict) -> Dict:
        files = a.get("files") or []
        return {
            "id": a.get("id"),
            "type": a.get("type"),
            "type_label": a.get("type_label") or TYPE_LABELS.get(a.get("type"), a.get("type")),
            "title": a.get("title", ""),
            "status": a.get("status"),
            "created": a.get("created"),
            "updated": a.get("updated"),
            "ai_mode": a.get("ai_mode", ""),
            "summary": (a.get("summary") or "")[:200],
            "files": [f.get("path") for f in files],
            "file_count": len(files),
            "error": a.get("error", ""),
        }


def _ctx_summary(ctx: Dict) -> Dict:
    keep = {}
    for k in ("framework", "base_url", "notes", "file_path", "scope"):
        if ctx.get(k):
            keep[k] = str(ctx[k])[:200]
    for k in ("skills_used", "tools_used"):
        v = ctx.get(k)
        if v:
            keep[k] = [str(x)[:60] for x in (v if isinstance(v, list) else [v])][:20]
    return keep


def safe_rel_path(repo_root: Path, rel: str) -> Optional[Path]:
    """Resolve rel inside repo_root; None if it escapes the repo."""
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel or any(p == ".." for p in rel.split("/")):
        return None
    if any(seg.endswith(":") for seg in rel.split("/")[:1]):
        return None
    target = (repo_root / rel).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return target


def diff_lines(old_text: str, new_text: str) -> Tuple[List[Dict[str, str]], int, int]:
    """Line-level aligned diff for display.

    Returns (lines, added, removed) where lines is [{"t": "same"|"add"|"del",
    "s": <line text>}, ...] covering the FULL new content: unchanged lines as
    "same", produced lines as "add", and the old lines they replace as "del"
    (rendered in place, before their "add" counterparts).
    """
    a = str(old_text or "").split("\n")
    b = str(new_text or "").split("\n")
    # 结尾换行 split 出来的尾部空串是换行符不是内容，去掉，避免
    # 「旧空文件 vs 新文件」出现幻影 same 行。
    if a and a[-1] == "":
        a.pop()
    if b and b[-1] == "":
        b.pop()
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: List[Dict[str, str]] = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for s in a[i1:i2]:
                out.append({"t": "same", "s": s})
            continue
        for s in a[i1:i2]:
            out.append({"t": "del", "s": s})
            removed += 1
        for s in b[j1:j2]:
            out.append({"t": "add", "s": s})
            added += 1
    return out, added, removed


def diff_against_repo(artifact: Dict, repo_root) -> Dict:
    """Diff each artifact file against the CURRENT working-tree content.

    Read-only, never writes. Files missing from the repo come back as all-add
    (treated as new). Large or unreadable files degrade to exists=True with an
    empty diff so the UI falls back to the plain full-file view.
    """
    root = Path(repo_root)
    files_out: List[Dict] = []
    for f in artifact.get("files") or []:
        rel = str(f.get("path", ""))
        target = safe_rel_path(root, rel)
        new_text = str(f.get("content") or "")
        exists = bool(target and target.is_file())
        old_text = ""
        if exists:
            try:
                old_text = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                files_out.append({"path": rel, "exists": True, "added": 0,
                                  "removed": 0, "lines": [], "stale": True})
                continue
        lines, added, removed = diff_lines(old_text, new_text)
        files_out.append({"path": rel, "exists": exists,
                          "added": added, "removed": removed, "lines": lines})
    return {"files": files_out}


class ArtifactApplier:
    """Writes approved artifact files into the working tree. Never commits."""

    def __init__(self, repo_path: str, gate_cfg: Optional[Dict] = None):
        from .fixer import CodeFixer  # reuse preflight / gate plumbing
        self.repo_path = Path(repo_path).resolve()
        self.fixer = CodeFixer(repo_path, "", gate_cfg)
        self.gate_cfg = gate_cfg or {}

    def preflight(self) -> Dict:
        return self.fixer.preflight()

    def apply(self, artifact: Dict, force_gate: bool = False) -> Dict:
        files = artifact.get("files") or []
        if not files:
            return {"ok": False, "status": "failed", "error": "产出物里没有可写入的文件"}

        pre = self.preflight()
        if pre["problems"]:
            return {"ok": False, "status": "failed",
                    "error": "仓库预检未通过: " + "; ".join(pre["problems"]), "preflight": pre}

        written: List[Dict[str, str]] = []
        try:
            for f in files:
                target = safe_rel_path(self.repo_path, f.get("path", ""))
                if target is None:
                    raise ValueError(f"非法文件路径: {f.get('path', '')}（必须相对仓库根且不能越界）")
                backup = target.read_text(encoding="utf-8") if target.exists() else ""
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f.get("content", ""), encoding="utf-8")
                written.append({"file": str(f.get("path", "")), "backup": backup})
        except Exception as e:
            self._restore(written)
            return {"ok": False, "status": "failed", "error": f"写入失败，已回滚: {e}"}

        from .gate import run_gate
        recheck = run_gate(str(self.repo_path), self.gate_cfg,
                           [f.get("path", "") for f in files], patched_contents=None)
        if not recheck.ok and not force_gate:
            self._restore(written)
            return {"ok": False, "status": "apply_failed",
                    "error": "写入后验收未通过，已回滚文件内容（工作区未改动）",
                    "gate": recheck.to_dict()}

        return {"ok": True, "status": "ok", "files": [w["file"] for w in written],
                "gate": recheck.to_dict(), "level": recheck.level, "forced": bool(force_gate),
                "committed": False,
                "backups": {w["file"]: w["backup"] for w in written}}

    def _restore(self, written: List[Dict[str, str]]) -> None:
        for item in written:
            try:
                target = safe_rel_path(self.repo_path, item["file"])
                if target is None:
                    continue
                if item["backup"]:
                    target.write_text(item["backup"], encoding="utf-8")
                elif target.exists():
                    target.unlink()
            except Exception:
                pass

    def undo(self, artifact: Dict) -> Dict:
        backups = (artifact.get("apply") or {}).get("backups") or {}
        if not backups:
            return {"ok": False, "error": "没有找到采纳时的备份，无法撤销"}
        restored = []
        for rel, content in backups.items():
            target = safe_rel_path(self.repo_path, rel)
            if target is None:
                continue
            if content:
                target.write_text(content, encoding="utf-8")
            elif target.exists():
                target.unlink()
            restored.append(rel)
        return {"ok": True, "restored": restored}
