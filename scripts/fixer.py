"""Stage 3: apply an *approved* proposal to the working tree.

Nothing here runs unless a human approved a proposal. We re-derive the patch
from SEARCH/REPLACE blocks against the current on-disk content, refuse if the
file drifted, write the files, and re-run the gate on the real tree. No commit
is created — the user reviews the diff and commits manually. Any failure
restores the original file contents. NEVER pushes to remote.
"""
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .gate import GateResult, run_gate
from .patch_engine import Block, build_patch, parse_blocks


class RepoNotUsable(Exception):
    pass


class CodeFixer:
    def __init__(self, repo_path: str, branch: str = "", gate_cfg: Optional[Dict] = None):
        self.repo_path = Path(repo_path).resolve()
        self.branch = (branch or "").strip()
        self.gate_cfg = gate_cfg or {}

    # ------------------------------------------------------------ git utils
    def _git(self, *args: str, check: bool = False, timeout: int = 60) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, ["git", *args], proc.stdout, proc.stderr
            )
        return (proc.stdout or "").strip()

    def current_branch(self) -> str:
        try:
            return self._git("rev-parse", "--abbrev-ref", "HEAD")
        except Exception:
            return ""

    def is_dirty(self) -> bool:
        try:
            return bool(self._git("status", "--porcelain"))
        except Exception:
            return True

    def preflight(self) -> Dict:
        """Preconditions for touching the repo at all. Reported on every proposal."""
        info = {
            "repo": str(self.repo_path),
            "is_git": False,
            "current_branch": "",
            "expected_branch": self.branch,
            "branch_ok": True,
            "dirty": False,
            "problems": [],
        }
        if not (self.repo_path / ".git").exists():
            info["problems"].append("目标路径不是 git 仓库，无法安全提交或回滚")
            return info
        info["is_git"] = True
        info["current_branch"] = self.current_branch()
        if self.branch and info["current_branch"] != self.branch:
            info["branch_ok"] = False
            info["problems"].append(
                f"当前分支 {info['current_branch']!r} 与配置分支 {self.branch!r} 不一致；"
                "流水线不会自行切换分支，请先切到目标分支再采纳"
            )
        if self.is_dirty():
            info["dirty"] = True
        return info

    # -------------------------------------------------------------- preview
    def preview(self, patch_text: str) -> Dict:
        """Build the diff in memory. Writes nothing."""
        blocks, parse_errors = parse_blocks(patch_text)
        result = build_patch(str(self.repo_path), blocks)
        return {
            "ok": result.ok,
            "blocks": [
                {
                    "file_path": b.file_path,
                    "search": b.search,
                    "replace": b.replace,
                    "match_mode": b.match_mode,
                    "match_start": b.match_start,
                }
                for b in blocks
            ],
            "changes": [
                {
                    "file_path": c.file_path,
                    "applied_blocks": c.applied_blocks,
                    "error": c.error,
                    "warnings": c.warnings,
                    "diff": c.diff(),
                    "stats": c.stats(),
                }
                for c in result.changes
            ],
            "errors": list(dict.fromkeys(result.errors + parse_errors)),
            "rejected": result.rejected,
            "warnings": result.warnings,
            "combined_diff": result.combined_diff(),
            "files": result.files(),
            "patched_contents": {
                c.file_path: c.patched for c in result.changes if c.ok and c.patched
            },
        }

    # ---------------------------------------------------------------- apply
    def apply(self, proposal: Dict, force_gate: bool = False) -> Dict:
        """Write the approved patch to the working tree and verify on the real
        tree. Does NOT create any commit — the user reviews and commits manually."""
        patch = proposal.get("patch") or {}
        patch_text = patch.get("patch_text", "")
        if not patch_text.strip():
            return {"status": "failed", "error": "提案里没有 SEARCH/REPLACE 块，无法应用"}

        pre = self.preflight()
        if pre["problems"]:
            return {"status": "failed", "error": "; ".join(pre["problems"]), "preflight": pre}

        # Rebuild against whatever is on disk *now* — the repo may have moved since
        # the proposal was created.
        fresh = self.preview(patch_text)
        if not fresh["ok"]:
            return {
                "status": "failed",
                "error": "提案已失效：仓库内容相对提案发生变化，或补丁无法重新匹配",
                "detail": fresh["errors"] + fresh["rejected"],
            }
        expected = {
            c["file_path"]: c.get("diff", "") for c in (patch.get("changes") or [])
        }
        drifted = [
            c["file_path"]
            for c in fresh["changes"]
            if c["file_path"] in expected and c.get("diff", "") != expected[c["file_path"]]
        ]
        if drifted:
            return {
                "status": "failed",
                "error": "目标文件在提案生成后被改过，diff 已不一致，拒绝自动采纳",
                "detail": drifted,
            }

        written: List[Dict[str, str]] = []
        try:
            for change in fresh["changes"]:
                rel = change["file_path"]
                target = self.repo_path / rel
                backup = target.read_text(encoding="utf-8")
                target.write_text(fresh["patched_contents"][rel], encoding="utf-8")
                written.append({"file": rel, "backup": backup})

            recheck: GateResult = run_gate(
                str(self.repo_path),
                self.gate_cfg,
                fresh["files"],
                patched_contents=None,  # run against the real tree now
            )
            if not recheck.ok and not force_gate:
                self._restore(written)
                return {
                    "status": "apply_failed",
                    "error": "写入后验收未通过，已回滚文件内容（工作区未改动）",
                    "gate": recheck.to_dict(),
                    "files": fresh["files"],
                }

            return {
                "status": "ok",
                "files": fresh["files"],
                "gate": recheck.to_dict(),
                "level": recheck.level,
                "forced": bool(force_gate),
                "committed": False,
                # 原始内容快照：撤销采纳时用于恢复文件
                "backups": {w["file"]: w["backup"] for w in written},
            }
        except Exception as e:
            self._restore(written)
            return {"status": "failed", "error": f"应用失败，已回滚: {e}"}

    def _restore(self, written: List[Dict[str, str]]) -> None:
        for item in written:
            try:
                (self.repo_path / item["file"]).write_text(item["backup"], encoding="utf-8")
            except Exception:
                pass

    # ------------------------------------------------------------------ misc
    def diff_against(self, ref: str = "HEAD", paths: Optional[List[str]] = None) -> str:
        args = ["diff", ref, "--"] + (paths or [])
        try:
            return self._git(*args, timeout=20)[:20000]
        except Exception:
            return ""
