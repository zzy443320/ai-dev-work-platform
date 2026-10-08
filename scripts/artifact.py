"""Artifact store + applier — the human-approval boundary for the three
frontend-dev tasks (reqdev / apidebug / codetest), same contract as proposals:

The task runner only ever produces an *artifact* (files + plan). Nothing touches
the target repo until someone approves, which is the only code path that writes
files. Apply writes to the working tree with per-file backups, runs the gate,
auto-restores on failure. No commit, no push.
"""
import difflib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .json_store import JsonRecordStore
from .textutil import slug

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
    # 缺陷修复循环在沙箱里跑红→绿的复现用例，见 scripts/repro.py 与 pipeline._repro_artifact
    "repro": "缺陷复现用例",
    # 长任务作业（多子 Agent 协作），见 scripts/team_run.py
    "team": "长任务作业",
}


def _slug(s: str) -> str:
    return slug(s, maxlen=40, default="task")


class ArtifactStore(JsonRecordStore):
    """一份产出物 = artifacts/<id>.json。读写与加锁语义见 scripts/json_store.py。"""

    SLUG_LEN = 40
    DEFAULT_SLUG = "task"

    def create(self, kind: str, title: str, payload: Dict, ctx: Optional[Dict] = None) -> Dict:
        now = datetime.now().isoformat(timespec="seconds")
        base_id = f"{_slug(kind)}-{datetime.now():%Y%m%d-%H%M%S}"
        # id 只精确到秒：两条工单在同一秒内各出一份产出物时，后写的会**静默覆盖**前一份
        # （提案那边也有这个约定，但产出物更容易撞上——复现用例每条工单都可能挂一份）。
        # 覆盖掉的不是「一条记录」，而是用户可能已经审了半天的一份文件正文，所以避让。
        # 探测与写入必须在同一把类级锁里：分两步的话并发双方都会挑中 base_id，
        # 加后缀就成了摆设（锁的归属规则见 scripts/json_store.py 顶部注释）。
        with self._lock:
            aid = base_id
            n = 1
            while self._path(aid).exists():
                n += 1
                aid = f"{base_id}-{n}"
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
    """解析仓库内相对路径，越界返回 None。

    实现已统一到一个地方（scripts/repo_paths.resolve_in_repo）。这里只是保留
    「返回 Optional」的旧签名给 chat/team_run 的读路径用。

    顺便修掉这份实现原先的一个洞：它先 `lstrip("/")` 再判断，于是
    `/etc/passwd` 被洗成 `etc/passwd` 当成仓内路径放行；而 Windows 上
    `Path("/etc/passwd").is_absolute()` 又恰好是 False（被当成当前盘根的相对路径），
    所以那一层也拦不住。resolve_in_repo 现在在任何剥离之前先拒前导 `/`。
    """
    from .repo_paths import UnsafePath, resolve_in_repo
    try:
        return resolve_in_repo(repo_root, rel)
    except UnsafePath:
        return None


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

    `action == "edit"` 的文件（问答产生的局部改动提案）在**这里**把 edits 应用到
    当前工作区内容，算出改完后的文本再 diff——产出物里只存 find/replace，不存整份
    文件（几千行的语言包让模型吐全量内容必然被截断，截断的内容被采纳就写坏了）。
    匹配不上 / 多处命中时返回 stale=True 并带 reason，界面会退回纯文本视图。
    """
    from .patch_engine import apply_edits  # 局部替换的匹配实现与缺陷修复链路共用

    root = Path(repo_root)
    files_out: List[Dict] = []
    for f in artifact.get("files") or []:
        rel = str(f.get("path", ""))
        target = safe_rel_path(root, rel)
        exists = bool(target and target.is_file())
        old_text = ""
        if exists:
            try:
                old_text = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                files_out.append({"path": rel, "exists": True, "added": 0,
                                  "removed": 0, "lines": [], "stale": True,
                                  "reason": "文件读取失败"})
                continue
        if str(f.get("action") or "") == "edit":
            edits = f.get("edits") or []
            if not exists:
                files_out.append({"path": rel, "exists": False, "added": 0,
                                  "removed": 0, "lines": [], "stale": True,
                                  "reason": "仓库里没有这个文件，无法做局部替换"})
                continue
            new_text, errs, _warns = apply_edits(old_text, edits, rel)
            if errs:
                files_out.append({"path": rel, "exists": True, "added": 0,
                                  "removed": 0, "lines": [], "stale": True,
                                  "reason": "；".join(errs)})
                continue
            lines, added, removed = diff_lines(old_text, new_text)
            files_out.append({"path": rel, "exists": True, "added": added,
                              "removed": removed, "lines": lines,
                              "content": new_text})
            continue
        new_text = str(f.get("content") or "")
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

    def _file_content(self, f: Dict, target: Optional[Path]) -> Tuple[str, str]:
        """算出这个文件最终要写的内容（纯计算，不落盘）。

        action=edit 的条目只带 find/replace：读当前文件 → 匹配替换 → 得到新内容。
        匹配不到或匹配到多处一律返回错误，交给 apply 回滚——宁可什么都不写，
        也不能把「没替换成功」的原文件当成功写回去。
        """
        from .patch_engine import apply_edits

        if str(f.get("action") or "") != "edit":
            return str(f.get("content") or ""), ""
        rel = str(f.get("path", ""))
        if target is None or not target.is_file():
            return "", f"文件不存在，无法做局部替换: {rel}"
        try:
            old = target.read_text(encoding="utf-8")
        except Exception as e:
            return "", f"读取失败 {rel}: {e}"
        new_text, errs, _warns = apply_edits(old, f.get("edits") or [], rel)
        if errs:
            return "", "；".join(errs)
        if new_text == old:
            return "", f"{rel}: 局部替换没有产生任何实际改动（find 与 replace 相同？）"
        return new_text, ""

    def apply(self, artifact: Dict, force_gate: bool = False) -> Dict:
        from .apply_ops import write_and_gate

        files = artifact.get("files") or []
        if not files:
            return {"ok": False, "status": "failed", "error": "产出物里没有可写入的文件"}

        pre = self.preflight()
        if pre["problems"]:
            return {"ok": False, "status": "failed",
                    "error": "仓库预检未通过: " + "; ".join(pre["problems"]), "preflight": pre}

        # 先把每个文件的最终内容算出来（edit 条目要读盘做替换），此阶段不落盘、
        # 不会留下半套改动；再整体交给与提案采纳共用的落盘内核。
        staged = []
        for f in files:
            rel = str(f.get("path", ""))
            target = safe_rel_path(self.repo_path, rel)
            if target is None:
                return self._pre_write_error(
                    f"非法文件路径: {rel}（必须相对仓库根且不能越界）", pre)
            content, err = self._file_content(f, target)
            if err:
                return self._pre_write_error(f"文件内容不可用: {err}", pre)
            staged.append((rel, content))

        result = write_and_gate(self.repo_path, staged, gate_cfg=self.gate_cfg,
                                force_gate=force_gate,
                                gate_files=[str(f.get("path", "")) for f in files],
                                preflight=pre)
        return result

    def _pre_write_error(self, msg: str, pre: Dict) -> Dict:
        """算内容/校验路径阶段的失败：此时一个字节都没写，工作区是干净的。
        别套用「已回滚」那种措辞——它会让用户去核对根本不存在的变化。"""
        return {"ok": False, "status": "failed", "preflight": pre,
                "error": f"{msg}（未写入任何文件）"}

    def undo(self, artifact: Dict) -> Dict:
        from .apply_ops import restore_written

        backups = (artifact.get("apply") or {}).get("backups") or {}
        if not backups:
            return {"ok": False, "error": "没有找到采纳时的备份，无法撤销"}
        written = [{"file": rel, "backup": content} for rel, content in backups.items()]
        unrestored = restore_written(self.repo_path, written)
        if unrestored:
            return {"ok": False, "error": f"以下文件恢复失败，请手工核对：{', '.join(unrestored)}",
                    "unrestored": unrestored}
        return {"ok": True, "restored": [w["file"] for w in written]}
