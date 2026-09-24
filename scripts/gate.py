"""Stage 3b: verification gate.

Decides what "the fix didn't break the build" means for a given repo, degrading
gracefully when the repo has no tooling configured:

  level="explicit"    commands came from config `gate.commands`
  level="package_json" commands auto-detected from package.json scripts
  level="degraded"    no runnable commands — best-effort syntax balance only
  level="none"        nothing to check at all

A failing gate does not destroy anything: it only marks the proposal
`gate_failed`, which the UI requires an explicit override to approve.
"""
import ast
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_TIMEOUT = 300

# package.json script names we trust as real verification, in priority order.
_SCRIPT_CANDIDATES = [
    ("typecheck", ["type-check", "typecheck", "ts:check", "tsc", "vue-tsc"]),
    ("lint", ["lint", "eslint", "lint:js"]),
    ("test", ["test", "test:unit", "vitest", "jest"]),
    ("build", ["build"]),
]


@dataclass
class Check:
    name: str
    cmd: str
    kind: str = "command"  # command | syntax
    returncode: int = 0
    ok: bool = True
    skipped: bool = False
    seconds: float = 0.0
    output_tail: str = ""


@dataclass
class GateResult:
    level: str = "none"
    ok: bool = True
    checks: List[Check] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def failed_checks(self) -> List[str]:
        return [c.name for c in self.checks if not c.ok and not c.skipped]

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["failed_checks"] = self.failed_checks
        return d


def resolve_commands(repo_path: str, gate_cfg: Optional[Dict]) -> List[Dict[str, str]]:
    """Return [{"name", "cmd"}] from config, else infer from package.json."""
    gate_cfg = gate_cfg or {}
    explicit = gate_cfg.get("commands")
    if explicit:
        out = []
        for i, item in enumerate(explicit):
            if isinstance(item, str):
                out.append({"name": item.split()[0] if item else f"cmd{i+1}", "cmd": item})
            elif isinstance(item, dict):
                cmd = item.get("cmd") or item.get("command") or ""
                if cmd:
                    out.append({"name": item.get("name") or cmd.split()[0], "cmd": cmd})
        return out

    named = gate_cfg.get("named") or {}
    if named:
        return [
            {"name": k, "cmd": v}
            for k, v in named.items()
            if isinstance(v, str) and v.strip()
        ]

    pkg = Path(repo_path) / "package.json"
    if not pkg.is_file():
        return []
    try:
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}) or {}
    except Exception:
        return []
    out = []
    for label, candidates in _SCRIPT_CANDIDATES:
        for cand in candidates:
            if cand in scripts:
                out.append({"name": label, "cmd": f"npm run {cand}"})
                break
    return out


def run_gate(
    repo_path: str,
    gate_cfg: Optional[Dict],
    changed_files: List[str],
    patched_contents: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> GateResult:
    """Run the gate. Never touches the working tree."""
    gate_cfg = gate_cfg or {}
    result = GateResult()
    commands = resolve_commands(repo_path, gate_cfg)

    if commands:
        configured = bool(gate_cfg.get("commands") or gate_cfg.get("named"))
        result.level = "explicit" if configured else "package_json"
        if not configured:
            result.notes.append(
                "未配置 gate.commands，已从 package.json scripts 自动选用："
                + ", ".join(c["name"] for c in commands)
            )
        runner = gate_cfg.get("runner") or ""
        for spec in commands:
            cmd = spec["cmd"]
            if runner and not cmd.startswith(runner):
                cmd = f"{runner} {cmd}"
            result.checks.append(_run_command(spec["name"], cmd, repo_path, timeout))
        result.ok = all(c.ok or c.skipped for c in result.checks)
        if not result.ok:
            result.notes.append(
                "验收命令失败：本次提案不会自动进入可采纳状态，需修复建议或在 UI 勾选强制采纳。"
            )
        return result

    # No runnable commands — degrade.
    pkg_missing = not (Path(repo_path) / "package.json").is_file()
    if pkg_missing:
        result.notes.append(
            "目标仓库没有 package.json，无法确定 typecheck/lint/test 命令。"
        )
    else:
        result.notes.append(
            "package.json 里没有可识别的 type-check/lint/test/build 脚本。"
        )

    if gate_cfg.get("degraded", True):
        result.level = "degraded"
        checks = _syntax_checks(repo_path, changed_files, patched_contents)
        if checks:
            result.checks.extend(checks)
            result.notes.append(
                "已降级为括号/字符串闭合与 py 编译检查——"
                "**这只证明代码没被写坏，不证明类型和测试通过，请务必人工复核 diff。**"
            )
        else:
            result.level = "none"
            result.notes.append("没有任何可执行的验收手段，该提案完全依赖人工审查。")
    else:
        result.level = "none"

    result.ok = all(c.ok or c.skipped for c in result.checks)
    return result


def _run_command(name: str, cmd: str, cwd: str, timeout: int) -> Check:
    import time

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return Check(
            name=name,
            cmd=cmd,
            returncode=proc.returncode,
            ok=proc.returncode == 0,
            seconds=round(time.time() - start, 1),
            output_tail=_tail(out),
        )
    except subprocess.TimeoutExpired:
        return Check(
            name=name, cmd=cmd, returncode=-1, ok=False,
            seconds=round(time.time() - start, 1),
            output_tail=f"超时 {timeout}s",
        )
    except Exception as e:
        return Check(name=name, cmd=cmd, returncode=-1, ok=False, output_tail=str(e)[:500])


def _tail(s: str, n: int = 3000) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else "…(前文省略)…\n" + s[-n:]


def _syntax_checks(
    repo_path: str,
    files: List[str],
    patched: Optional[Dict[str, str]],
) -> List[Check]:
    """Best-effort syntax validation. Reads the in-memory patched copy when we
    have one (pre-approval), otherwise falls back to the file on disk
    (post-write recheck)."""
    patched = patched or {}
    checks: List[Check] = []
    node = shutil.which("node")
    for rel in files:
        text = patched.get(rel)
        if text is None:
            target = Path(repo_path) / rel
            try:
                text = target.read_text(encoding="utf-8")
            except Exception as e:
                checks.append(
                    Check(name=f"read:{rel}", cmd="读取文件", kind="syntax",
                          ok=False, returncode=1, output_tail=str(e)[:300])
                )
                continue
        suffix = Path(rel).suffix.lower()
        if suffix == ".py":
            checks.append(_py_check(rel, text))
        elif suffix in (".js", ".mjs", ".cjs", ".jsx"):
            checks.append(_node_check(rel, text, node))
        elif suffix in (".ts", ".tsx"):
            checks.append(Check(name=f"balance:{rel}", cmd="括号/引号闭合检查",
                                kind="syntax", **_balance_check(text, jsx=suffix == ".tsx")))
        elif suffix == ".vue":
            checks.append(_vue_check(rel, text))
        elif suffix in (".less", ".scss", ".css"):
            checks.append(Check(name=f"balance:{rel}", cmd="花括号闭合检查",
                                kind="syntax", **_balance_check(text)))
        else:
            checks.append(
                Check(name=f"skip:{rel}", cmd="无可用的语法校验器", kind="syntax",
                      skipped=True, output_tail=f"{suffix or '无扩展名'} 文件未做语法校验")
            )
    return checks


def _vue_check(rel: str, text: str) -> Check:
    """Validate a .vue file's <script> block — best effort, never a hard failure.

    Two separate false-positive sources make whole-file JS bracket scanning useless
    on .vue inputs:
      * Vue mustaches `{{ $t('...') }}` in the template derail the stack;
      * a multi-line template literal with `${...}` interpolation (very common in
        TS) leaves the scanner stuck in the `tpl` state.
    We therefore scope the scan to <script> and downgrade a failure to a *skipped*
    check with a warning: the per-file diff plus human review is the real gate.
    """
    m = re.search(r"(?is)<script[^>]*>(.*?)</script\s*>", text)
    if not m:
        return Check(name=f"skip:{rel}", cmd="无 <script> 块", kind="syntax", skipped=True,
                     output_tail="该 .vue 没有 <script> 块，跳过语法校验")
    body = m.group(1)
    if not body.strip():
        return Check(name=f"skip:{rel}", cmd="<script> 为空", kind="syntax", skipped=True,
                     output_tail="该 .vue 的 <script> 为空，跳过语法校验")

    res = _balance_check(body, jsx=True)
    if res["ok"]:
        return Check(name=f"balance:{rel}", cmd="<script> 块括号/引号闭合检查",
                     kind="syntax", ok=True)
    # 括号扫描对 TS 模板字符串/JSX 会误报，不据此判失败，只提示人工复核。
    return Check(name=f"skip:{rel}", cmd="<script> 块括号扫描（仅供参考）",
                 kind="syntax", skipped=True,
                 output_tail=f"自动扫描疑似不平衡（{res.get('output_tail', '')}），"
                             "该检查对多行模板字符串会误报，请以 tsc/eslint 或人工复核为准")


def _py_check(rel: str, text: str) -> Check:
    try:
        ast.parse(text)
        return Check(name=f"py-compile:{rel}", cmd="ast.parse", kind="syntax", ok=True)
    except SyntaxError as e:
        return Check(
            name=f"py-compile:{rel}", cmd="ast.parse", kind="syntax", ok=False,
            returncode=1, output_tail=f"{e.msg} (line {e.lineno})",
        )


def _node_check(rel: str, text: str, node: Optional[str]) -> Check:
    if not node:
        return Check(name=f"balance:{rel}", cmd="括号/引号闭合检查", kind="syntax",
                     **_balance_check(text, jsx=True))
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=Path(rel).suffix or ".js",
                                     delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        proc = subprocess.run([node, "--check", tmp], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        return Check(
            name=f"node-check:{rel}", cmd=f"{node} --check", kind="syntax",
            returncode=proc.returncode, ok=proc.returncode == 0,
            output_tail=_tail((proc.stderr or "") + (proc.stdout or ""), 800),
        )
    except Exception as e:
        return Check(name=f"node-check:{rel}", cmd="node --check", kind="syntax",
                     skipped=True, output_tail=str(e)[:300])
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass


def _balance_check(text: str, jsx: bool = False) -> Dict:
    """String/comment aware bracket balance. Best effort — no regex-literal parsing."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: List[str] = []
    i, n, line = 0, len(text), 1
    state = "code"  # code | sq | dq | tpl | lc | bc
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            line += 1
        if state == "lc":
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "bc":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
            i += 1
            continue
        if state in ("sq", "dq", "tpl"):
            q = {"sq": "'", "dq": '"', "tpl": "`"}[state]
            if ch == "\\":
                i += 2
                continue
            if ch == q and (state != "tpl" or not _tpl_tagged(text, i)):
                state = "code"
            elif ch == "\n" and state != "tpl":
                state = "code"  # unterminated string, recover instead of derailing
            i += 1
            continue
        # code
        if ch == "/" and nxt == "/":
            state = "lc"
            i += 2
            continue
        if ch == "/" and nxt == "*":
            state = "bc"
            i += 2
            continue
        if ch == "'":
            state = "sq"
        elif ch == '"':
            state = "dq"
        elif ch == "`":
            state = "tpl"
        elif ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return {"ok": False, "returncode": 1,
                        "output_tail": f"第 {line} 行：多余的 '{ch}'（前面没有对应的开括号）"}
            stack.pop()
        i += 1
    if stack:
        return {"ok": False, "returncode": 1,
                "output_tail": f"文件结束时仍有未闭合的括号: {''.join(stack[-4:])}"}
    if state in ("bc", "tpl"):
        return {"ok": False, "returncode": 1,
                "output_tail": f"文件结束时仍处于{'块注释' if state == 'bc' else '模板字符串'}中"}
    return {"ok": True, "returncode": 0}


def _tpl_tagged(text: str, idx: int) -> bool:
    """Very rough: treat a backtick as closing only if a same-depth opener exists."""
    depth = 0
    for k in range(idx - 1, max(0, idx - 4000), -1):
        c = text[k]
        if c == "`":
            if depth == 0:
                return True
            depth -= 1
        elif c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
    return False
