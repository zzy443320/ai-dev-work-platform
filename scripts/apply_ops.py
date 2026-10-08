# -*- coding: utf-8 -*-
"""采纳落盘的共用内核：解析路径 → 写文件（带备份）→ 跑闸门 → 失败回滚。

为什么要抽：提案采纳（CodeFixer.apply）与产出物采纳（ArtifactApplier.apply）原本是
两套同构实现，各自维护备份、回滚、闸门复检。同构但**不同精度**——合并前它们已经有
四处实质差异，每一处都是真实风险：

  1. 路径校验：产出物侧用 safe_rel_path，提案侧用裸拼接 `repo_path / rel`；
  2. 回滚：产出物侧遇到「采纳时新建的文件」会 unlink 删掉，提案侧只会把空串写回去
     （留一个内容为空的垃圾文件）；
  3. 回滚失败：产出物侧 `except: pass` 全吞，提案侧同样，于是「已回滚（工作区未改动）」
     这句话可能是不成立的；
  4. 建父目录：只有产出物侧 mkdir -p。

现在这四条只有一份实现。返回体同时带 `ok` 与 `status`，因为两类调用方读的就是不同键
（流水线读 status，HTTP 层读 ok）。
"""
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .gate import run_gate
from .repo_paths import UnsafePath, resolve_in_repo

# 一条已写入的记录：相对路径 + 采纳前的原始内容（"" 表示当时文件不存在＝新建）
Written = Dict[str, str]


def restore_written(repo_root: Path, written: Iterable[Written]) -> List[str]:
    """把已写入的文件恢复到采纳前状态，返回**恢复失败**的相对路径。

    备份为空串意味着这次采纳新建了文件 —— 那就要删除它，而不是把空内容写回去
    （留一个空文件在用户工作区里是脏的）。失败绝不静默吞掉：调用方要据此决定
    对用户说「工作区未改动」还是「这几个文件请手工核对」。
    """
    unrestored: List[str] = []
    for item in written:
        rel = item.get("file", "")
        backup = item.get("backup", "")
        try:
            target = resolve_in_repo(repo_root, rel)
            if backup:
                target.write_text(backup, encoding="utf-8")
            elif target.exists():
                target.unlink()
        except (UnsafePath, OSError):
            unrestored.append(rel)
    return unrestored


def _fail(written: List[Written], repo_root: Path, message: str,
          **extra) -> Dict:
    """统一的失败出口：先回滚，再把「没回滚干净」如实写在错误里。"""
    unrestored = restore_written(repo_root, written)
    payload = {"ok": False, "status": "failed",
               "error": message if not unrestored else
                        f"{message}；但下列文件回滚失败，工作区仍留着改动，请手工核对："
                        f"{', '.join(unrestored)}",
               "unrestored": unrestored}
    payload.update(extra)
    return payload


def write_and_gate(repo_root: Path, files: List[Tuple[str, str]], *,
                   gate_cfg: Optional[Dict] = None, force_gate: bool = False,
                   gate_files: Optional[List[str]] = None,
                   preflight: Optional[Dict] = None) -> Dict:
    """落盘一批 (相对路径, 新内容) 并在真实工作区上复检闸门。

    全有或全无：任何一步出错都把已写入的部分恢复掉，不会留下半套改动。
    `gate_files` 缺省用 files 的路径列表（产出物侧就是它自己的 files[] 顺序）。
    """
    repo_root = Path(repo_root)
    written: List[Written] = []

    # 先整体解析一遍路径：避免「前 3 个文件写成功、第 4 个越界」才发现问题
    targets: List[Tuple[str, str, Path]] = []
    for rel, content in files:
        try:
            targets.append((rel, content, resolve_in_repo(repo_root, rel)))
        except UnsafePath as e:
            return _fail(written, repo_root,
                         f"文件路径不安全，拒绝写盘：{e}", preflight=preflight)

    try:
        for rel, content, target in targets:
            backup = target.read_text(encoding="utf-8") if target.is_file() else ""
            if target.parent != target:
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append({"file": rel, "backup": backup})
    except OSError as e:
        return _fail(written, repo_root, f"写入失败，已回滚: {e}", preflight=preflight)

    paths = gate_files if gate_files is not None else [rel for rel, _ in files]
    # patched_contents=None → 对**真实工作区**跑闸门，检的就是用户即将提交的内容
    recheck = run_gate(str(repo_root), gate_cfg or {}, paths, patched_contents=None)
    if not recheck.ok and not force_gate:
        unrestored = restore_written(repo_root, written)
        return {
            "ok": False, "status": "apply_failed",
            "error": ("写入后验收未通过，已回滚文件内容（工作区未改动）" if not unrestored
                      else "写入后验收未通过，但下列文件回滚失败，工作区里仍留着改动，"
                           f"请手工核对：{', '.join(unrestored)}"),
            "unrestored": unrestored,
            "gate": recheck.to_dict(),
            "files": [w["file"] for w in written],
        }

    return {
        "ok": True, "status": "ok",
        "files": [w["file"] for w in written],
        "gate": recheck.to_dict(),
        "level": recheck.level,
        "forced": bool(force_gate),
        "committed": False,            # 只写工作区，绝不 commit / push
        "backups": {w["file"]: w["backup"] for w in written},
    }
