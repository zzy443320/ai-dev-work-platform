"""Search/Replace patch engine.

The pipeline NEVER writes a whole file from an AI "suggestion" anymore. Instead the
model must return SEARCH/REPLACE blocks, which we match against the real file text
before producing an in-memory patched copy + a unified diff.

Safety properties enforced here:
  * an empty SEARCH is rejected (it would be an unconditional prepend)
  * an ambiguous SEARCH (matches more than once) is rejected
  * a SEARCH covering essentially the whole file is rejected as a whole-file rewrite
  * nothing is written to disk by this module — it is pure text transformation
"""
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SEARCH_MARK = "<<<<<<< SEARCH"
DIVIDER_MARK = "======="
REPLACE_MARK = ">>>>>>> REPLACE"

# A SEARCH block covering at least this fraction of the file is treated as an
# attempted whole-file rewrite and refused, even if it matches exactly.
WHOLE_FILE_RATIO = 0.9
# ... but only for files with at least this many lines (tiny files may legitimately
# be rewritten in full, e.g. a 5-line config module).
WHOLE_FILE_MIN_LINES = 12

_BLOCK_RE = re.compile(
    re.escape(SEARCH_MARK) + r"[ \t]*(?P<path>[^\n\r]*)\r?\n"
    r"(?P<search>.*?)"
    r"(?:^|\r?\n)" + re.escape(DIVIDER_MARK) + r"\r?\n"
    r"(?P<replace>.*?)"
    r"(?:^|\r?\n)" + re.escape(REPLACE_MARK),
    re.DOTALL | re.MULTILINE,
)

# `#### path/to/file.tsx` heading, or a fenced block whose info string is a path.
_HEADING_RE = re.compile(
    r"^[ \t]*(?:#{2,6}|\*\*)[ \t]*[\w./\\-]*?"
    r"([\w./\\-]+\.(?:tsx?|jsx?|vue|svelte|less|scss|css|py))[ \t]*(?:\*\*)?[ \t]*$",
    re.MULTILINE,
)


@dataclass
class Block:
    """One raw SEARCH/REPLACE pair as extracted from model output."""

    file_path: str
    search: str
    replace: str
    match_mode: str = ""  # exact | fuzzy | "" (not attempted)
    match_start: int = -1  # 1-based line number of the match, for the UI


@dataclass
class FileChange:
    """The result of applying all blocks for one file, in memory."""

    file_path: str
    original: str = ""
    patched: str = ""
    applied_blocks: int = 0
    warnings: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def diff(self) -> str:
        if not self.ok or self.original == self.patched:
            return ""
        # keepends=True: unified_diff relies on the source newlines and only adds
        # `lineterm` to the header/@@ lines. A file whose last line has no newline
        # would otherwise glue two diff lines together, so we pad it and mark it.
        def _lines(s: str) -> List[str]:
            out = s.splitlines(keepends=True)
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            return out

        out = "".join(
            difflib.unified_diff(
                _lines(self.original),
                _lines(self.patched),
                fromfile=f"a/{self.file_path}",
                tofile=f"b/{self.file_path}",
                lineterm="\n",
            )
        )
        if not self.patched.endswith("\n"):
            out += "\\ No newline at end of file\n"
        return out

    def stats(self) -> Dict[str, int]:
        added = removed = 0
        for line in self.diff().splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return {"added": added, "removed": removed, "files": 1 if self.ok else 0}


@dataclass
class PatchResult:
    """Aggregate outcome of trying to build a patch set from model output."""

    changes: List[FileChange] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.changes) and all(c.ok for c in self.changes) and not self.errors

    @property
    def warnings(self) -> List[str]:
        return [w for c in self.changes for w in c.warnings]

    def combined_diff(self) -> str:
        return "\n".join(c.diff() for c in self.changes if c.ok)

    def files(self) -> List[str]:
        return [c.file_path for c in self.changes]


def parse_blocks(text: str) -> Tuple[List[Block], List[str]]:
    """Extract SEARCH/REPLACE blocks, resolving each block's target file path."""
    if not text:
        # 注意区分两种"没有补丁"：
        #   * 模型真的什么都没返回（超时/空响应）
        #   * 模型返回了分析文字，但没有按 SEARCH/REPLACE 格式给补丁
        # 上游把 patch_blocks 为空和 AI 报错混在一起，这里只描述本函数看到的事实。
        return [], ["模型返回的 patch_blocks 为空（没有拿到补丁文本）"]

    errors: List[str] = []
    headings = _HEADING_RE.findall(text)

    # Position of every heading lets us attribute a path-less block to the nearest
    # preceding heading, which is how the model usually structures its answer.
    heading_pos: List[Tuple[int, str]] = [
        (m.start(), m.group(1)) for m in _HEADING_RE.finditer(text)
    ]

    blocks: List[Block] = []
    for m in _BLOCK_RE.finditer(text):
        path = (m.group("path") or "").strip().strip("`'\"")
        path = re.sub(r"^[ab]/", "", path)
        if not path:
            path = next(
                (p for start, p in reversed(heading_pos) if start < m.start()), ""
            )
        if not path:
            errors.append(
                f"跳过无文件名的 SEARCH/REPLACE 块(位置 {m.start()})：请在标记后写明文件路径"
            )
            continue
        blocks.append(
            Block(file_path=_norm_path(path), search=m.group("search"),
                  replace=m.group("replace"))
        )

    if not blocks and headings:
        errors.append(
            "模型只给了文件标题和正文，没有给出可解析的 SEARCH/REPLACE 块"
        )
    return blocks, errors


def _norm_path(p: str) -> str:
    """Normalise a model-supplied path to a repo-relative POSIX path.

    Returns "" for anything unsafe: absolute paths, drive letters, UNC,
    or any path containing a `..` segment.
    """
    p = p.replace("\\", "/").strip()
    if not p or p.startswith("/"):
        return ""
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        return ""
    if ":" in parts[0]:  # C:/... or disk-relative oddity
        return ""
    return "/".join(parts)


def _rstrip_lines(s: str) -> List[str]:
    return [ln.rstrip() for ln in s.split("\n")]


def _find_exact(content: str, search: str) -> int:
    """Return byte offset of a unique exact match, or -1 for none/ambiguous."""
    if not search.strip():
        return -1
    first = content.find(search)
    if first == -1:
        return -1
    if content.find(search, first + 1) != -1:
        return -2  # ambiguous
    return first


def _find_fuzzy(
    content: str, search: str
) -> Tuple[int, int, int]:
    """Whitespace-tolerant line match.

    Returns (start_offset, end_offset, indent_delta) or (-1, -1, 0).
    Comparison ignores trailing whitespace and lets the model re-indent the block:
    each SEARCH line must match a content line after stripping leading whitespace,
    and the whole block must share one consistent indent delta.
    """
    c_lines = content.split("\n")
    s_lines = _rstrip_lines(search.rstrip("\n"))
    s_stripped = [ln.strip() for ln in s_lines]
    if not s_stripped or not any(s_stripped):
        return -1, -1, 0

    matches: List[Tuple[int, int]] = []
    for i in range(len(c_lines) - len(s_lines) + 1):
        delta: Optional[int] = None
        good = True
        for j, want in enumerate(s_stripped):
            have = c_lines[i + j].rstrip()
            if not want:
                if have.strip():
                    good = False
                    break
                continue
            if have.strip() == want:
                # indent delta measured against the SEARCH block's own indentation
                have_indent = len(have) - len(have.lstrip())
                want_indent = len(s_lines[j]) - len(s_lines[j].lstrip())
                d = have_indent - want_indent
                if delta is None:
                    delta = d
                elif delta != d:
                    good = False
                    break
            else:
                good = False
                break
        if good:
            matches.append((i, delta or 0))

    if len(matches) != 1:
        return -1, -1, 0

    start_line, delta = matches[0]
    start_offset = sum(len(c_lines[k]) + 1 for k in range(start_line))
    end_offset = sum(
        len(c_lines[k]) + 1 for k in range(start_line + len(s_lines))
    ) - 1
    return start_offset, end_offset, delta


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def build_patch(repo_path: str, blocks: List[Block]) -> PatchResult:
    """Match every block against the real files and produce in-memory changes."""
    repo = Path(repo_path).resolve()
    result = PatchResult()
    by_file: Dict[str, List[Block]] = {}
    for b in blocks:
        if not b.file_path:
            result.errors.append("存在越界文件路径的 SEARCH 块，已拒绝（禁止 .. 与绝对路径）")
            continue
        by_file.setdefault(b.file_path, []).append(b)

    for rel, file_blocks in by_file.items():
        change = FileChange(file_path=rel)
        target = repo / rel
        if not target.is_file():
            change.error = f"文件不存在: {rel}"
            result.changes.append(change)
            result.errors.append(change.error)
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as e:  # unreadable / binary
            change.error = f"读取失败 {rel}: {e}"
            result.changes.append(change)
            result.errors.append(change.error)
            continue

        change.original = content
        working = content
        total_chars = len(content.strip())

        for idx, blk in enumerate(file_blocks, 1):
            search = blk.search.rstrip("\n")
            replace = blk.replace.rstrip("\n")

            if not search.strip():
                msg = f"{rel}#{idx}: SEARCH 为空，拒绝（会导致无条件插入）"
                result.rejected.append(msg)
                result.errors.append(msg)
                continue
            if not replace.strip():
                msg = f"{rel}#{idx}: REPLACE 为空 —— 该块会删除代码，标记为需人工复核"
                change.warnings.append(msg)

            offset = _find_exact(working, search)
            if offset == -2:
                msg = f"{rel}#{idx}: SEARCH 在文件中匹配到多次，无法定位（请让模型增加上下文行）"
                result.rejected.append(msg)
                result.errors.append(msg)
                continue
            mode = "exact"
            if offset == -1:
                start, end, delta = _find_fuzzy(working, search)
                if start == -1:
                    msg = (
                        f"{rel}#{idx}: SEARCH 未匹配到原文件内容"
                        f"（前 40 字符: {search.strip()[:40]!r}）"
                    )
                    result.rejected.append(msg)
                    result.errors.append(msg)
                    continue
                if delta:
                    replace = _reindent(replace, delta)
                working = working[:start] + replace + working[end:]
                blk.match_mode = "fuzzy"
                blk.match_start = _line_of(working, start)
                change.warnings.append(
                    f"{rel}#{idx}: 采用空白容错匹配（indent_delta={delta}），请复核"
                )
                change.applied_blocks += 1
                continue
            else:
                mode = "exact"

            blk.match_mode = mode
            blk.match_start = _line_of(working, offset)

            covered = len(search.split("\n"))
            if covered >= WHOLE_FILE_MIN_LINES and (
                len(search.strip()) >= WHOLE_FILE_RATIO * len(content.strip())
            ):
                msg = (
                    f"{rel}#{idx}: SEARCH 覆盖了整个文件（{covered} 行），"
                    "按整文件覆写拒绝——请让模型只输出被修改的函数/代码块"
                )
                result.rejected.append(msg)
                result.errors.append(msg)
                continue

            working = working[:offset] + replace + working[offset + len(search):]
            change.applied_blocks += 1

        change.patched = working
        if change.applied_blocks and not change.error:
            if len(working.strip()) < 0.5 * total_chars and total_chars > 500:
                change.warnings.append(
                    f"{rel}: 修改后文件长度不足原文件 50%，疑似大段删除，请人工复核"
                )
            if working == content:
                change.warnings.append(f"{rel}: SEARCH/REPLACE 未产生任何实际改动")
        result.changes.append(change)

    if not result.changes:
        result.errors.append("没有解析出任何可用的 SEARCH/REPLACE 块")
    return result


def _reindent(text: str, delta: int) -> str:
    if delta > 0:
        pad = " " * delta
        return "\n".join(pad + ln if ln.strip() else ln for ln in text.split("\n"))
    if delta < 0:
        out = []
        for ln in text.split("\n"):
            stripped = ln.lstrip()
            have = len(ln) - len(stripped)
            out.append(" " * max(0, have + delta) + stripped)
        return "\n".join(out)
    return text


def render_block_prompt(blocks: List[Block]) -> str:
    """Echo parsed blocks back in canonical form — used in the KB card."""
    if not blocks:
        return "(模型未给出 SEARCH/REPLACE 块)"
    parts = []
    for b in blocks:
        parts.append(
            f"<<<<<<< SEARCH {b.file_path}\n{b.search.rstrip()}\n=======\n"
            f"{b.replace.rstrip()}\n>>>>>>> REPLACE"
        )
    return "\n\n".join(parts)
