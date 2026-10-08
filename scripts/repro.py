# -*- coding: utf-8 -*-
"""缺陷复现用例：跑器探测 + 「必须先红」判据。

为什么单独一个模块：agentic 修复循环原先的「验证通过」只等于**你配的那几条
lint/tsc 命令从红变绿**。它能证明「没改坏构建」，证明不了「这条缺陷被修好了」——
缺陷的真正判据是「工单描述的现象不再出现」，而那需要一个**针对现象的可执行断言**。
这个模块就是为了让那件东西存在：

  1. `detect_harness()`  看仓库里到底有什么可跑的（vitest / jest / mocha / 自定义命令 /
     什么都没有时退回可执行断言脚本），并如实标注证据强度；
  2. `classify_run()`    把一次运行分成「红（有效复现）/ 绿（用例没测到缺陷）/
     跑不起来（runner 或脚本本身坏了）」三态。**没跑起来不能算红**——一个 import
     都失败的文件永远都是红的，拿它当判据会得到「永远修不好」的假结论；
  3. `spec_ok()`         用例落点的硬约束：必须是仓库内新建文件、必须像测试文件
     （否则 vitest 根本不收集它，跑了个寂寞）。

「必须先红」是硬闸门：用例在未修复代码上就通过了，说明它测的是别的东西，
必须重写；重写次数用尽仍不能复现，就放弃复现环节并如实记录（而不是偷偷把一个
什么都没测的用例算成证据）。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

STRENGTH_TEST = "test"        # 真单测跑器：证据最强
STRENGTH_ASSERT = "assert"    # 可执行断言脚本：真红真绿，但只是机械判据
STRENGTH_NONE = "none"        # 没有任何可跑的复现手段

STRENGTH_LABEL = {
    STRENGTH_TEST: "单测跑器",
    STRENGTH_ASSERT: "可执行断言",
    STRENGTH_NONE: "无",
}

# package.json 里像「跑单测」的脚本名（按可信度排序）
_TEST_SCRIPTS = ("test:unit", "test", "vitest", "jest", "unit", "ut")

# 每种跑器：命令模板 + 用例落点提示
_RUNNERS = (
    # vitest 必须显式 run，否则 CI=true 之外还可能进 watch
    ("vitest", "npx vitest run {file}",
     "放在被测文件同目录或 __tests__/ 下，命名 *.spec.ts / *.test.js"),
    ("jest", "npx jest --ci --runTestsByPath {file}",
     "命名 *.test.js / *.spec.js，遵循仓库 jest 的 testMatch 配置"),
    ("mocha", "npx mocha {file}", "放在 test/ 或 spec/ 目录下"),
    ("playwright", "npx playwright test {file}",
     "命名 *.spec.ts 放进 e2e 目录（较慢，单条缺陷慎用）"),
)

# 「跑不起来」而非「测出缺陷」的输出特征。这些一律不算合格的红。
_BROKEN_RES = (
    re.compile(r"Cannot find module", re.I),
    re.compile(r"ERR_MODULE_NOT_FOUND|ENOENT", re.I),
    re.compile(r"command not found|不是内部或外部命令", re.I),
    re.compile(r"npm ERR!", re.I),
    re.compile(r"No test files? found|No test suites? were found", re.I),
    re.compile(r"no tests found", re.I),
    re.compile(r"^SyntaxError:", re.M),
    re.compile(r"Cannot use import statement outside a module", re.I),
    re.compile(r"unexpected token", re.I),
)

_SPEC_NAME_RE = re.compile(r"(\.spec|\.test|_test|test_)", re.I)
_SOURCE_EXT = {".ts", ".tsx", ".js", ".jsx", ".vue", ".mjs", ".cjs"}


@dataclass
class Harness:
    kind: str
    name: str
    cmd_tpl: str = ""
    spec_hint: str = ""
    strength: str = STRENGTH_NONE
    notes: List[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.cmd_tpl) and self.strength != STRENGTH_NONE

    def to_dict(self) -> Dict:
        return {"kind": self.kind, "name": self.name, "cmd_tpl": self.cmd_tpl,
                "spec_hint": self.spec_hint, "strength": self.strength,
                "strength_label": STRENGTH_LABEL.get(self.strength, self.strength),
                "notes": list(self.notes)}


def render_cmd(h: Harness, spec_rel: str) -> str:
    """把用例路径填进命令模板。模板没写 {file} 就追加到末尾。"""
    tpl = h.cmd_tpl or ""
    if "{file}" in tpl:
        return tpl.replace("{file}", spec_rel)
    return f"{tpl} {spec_rel}".strip()


def detect_harness(repo_root: Path, cfg: Optional[Dict] = None) -> Harness:
    """探测这个仓库能用什么方式复现缺陷。

    顺序：用户显式配的复现命令 → package.json 里的测试脚本 → node_modules/.bin 里
    现成的跑器 → 断言脚本（node 或 python）。**宁可退回断言脚本也不要退回「没有」**，
    但要标清强度：断言脚本绿了不等于页面好了，这一点对用户必须可见。
    """
    cfg = dict(cfg or {})
    root = Path(repo_root)

    custom = str(cfg.get("command") or "").strip()
    if custom:
        strength = str(cfg.get("strength") or "").strip().lower()
        if strength not in (STRENGTH_TEST, STRENGTH_ASSERT):
            strength = STRENGTH_TEST if _looks_like_runner(custom) else STRENGTH_ASSERT
        return Harness(kind="command", name="自定义复现命令", cmd_tpl=custom,
                       spec_hint=str(cfg.get("spec_hint") or "路径由你定，命令里用 {file} 引用"),
                       strength=strength,
                       notes=["复现命令来自配置 agent.repro.command，跑器识别由你负责"])

    scripts = _pkg_scripts(root)
    bins = _bin_names(root)
    for kind, tpl, hint in _RUNNERS:
        if kind not in bins and not any(kind in (scripts.get(s) or "") for s in scripts):
            continue
        # 有 .bin 才敢直接 npx；只有 scripts 里没有对应跑器时，交给 npm run
        if kind in bins:
            return Harness(kind=kind, name=kind, cmd_tpl=tpl, spec_hint=hint,
                           strength=STRENGTH_TEST)
        for script in _TEST_SCRIPTS:
            if script in scripts:
                return Harness(kind=kind, name=f"npm run {script}",
                               cmd_tpl=f"npm run {script} -- {{file}}",
                               spec_hint=hint, strength=STRENGTH_TEST,
                               notes=[f"用 npm run {script} 跑单文件，参数经 -- 透传；"
                                      "若该脚本不接受文件参数，请把命令改成显式的 "
                                      "agent.repro.command"])
    # 没有跑器：退回「一个能跑的可执行断言脚本」
    if "node" in bins or _which("node"):
        return Harness(kind="assert-node", name="node 断言脚本",
                       cmd_tpl="node {file}",
                       spec_hint="放在仓库内新建的 .cjs 文件里（如 "
                                 "__repro__/defect.cjs），用 fs/正则读被测源码做断言",
                       strength=STRENGTH_ASSERT,
                       notes=["仓库没有单测跑器，只能用机械断言：它能证明「某段代码改对了」，"
                              "不能证明「页面上的现象消失了」"])
    return Harness(kind="assert-python", name="python 断言脚本",
                   cmd_tpl=f'"{Path(sys.executable).as_posix()}" {{file}}',
                   spec_hint="放在仓库内新建的 .py 文件里（如 __repro__/defect.py），"
                             "读被测源码做断言",
                   strength=STRENGTH_ASSERT,
                   notes=["仓库既没有单测跑器也没有 node，只能用 Python 机械断言"
                          "（证据强度最低，务必人工复核页面）"])


def spec_ok(h: Harness, repo_root: Path, rel: str) -> Tuple[bool, str]:
    """用例落点校验：仓库内、新建、看起来像个能被收集到的测试文件。"""
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw:
        return False, "path 不能为空"
    if raw.startswith("/") or ".." in [p for p in raw.split("/") if p]:
        return False, "用例必须写在仓库内的相对路径，且不能跳出仓库根"
    if ":" in raw.split("/")[0]:
        return False, "用例路径不能带盘符"
    target = Path(repo_root) / raw
    if target.exists():
        return False, (f"{raw} 已经存在——复现用例必须是**新建文件**，不要覆盖仓库里已有的"
                       "测试；换一个路径，或者用补丁流程去改已有用例")
    suffix = Path(raw).suffix.lower()
    if suffix not in (_SOURCE_EXT | {".py", ".cjs", ".mjs", ".json"}):
        return False, f"用例文件扩展名不支持：{suffix or '(无)'}"
    if h.strength == STRENGTH_TEST and not _SPEC_NAME_RE.search(Path(raw).name):
        return False, (f"跑器是 {h.name}：文件名必须含 .spec / .test，"
                       "否则它根本不会收集这个用例（跑了个寂寞）。"
                       f"建议：{h.spec_hint}")
    return True, ""


def classify_run(h: Harness, returncode: int, output: str,
                 denied: bool = False) -> str:
    """一次复现运行的三态判定。"""
    if denied:
        return "broken"
    if returncode != 0:
        text = output or ""
        for rx in _BROKEN_RES:
            if rx.search(text):
                return "broken"
        return "red"
    return "green"


def broken_hint(h: Harness, output: str) -> str:
    """跑不起来时给模型的可执行下一步（不同原因修法完全不同）。"""
    text = (output or "")[:600]
    if "Cannot find module" in text or "ERR_MODULE_NOT_FOUND" in text:
        return ("用例 import 了拿不到的模块。别去装依赖：改成只断言**这个文件本身的文本**"
                "（读源码做正则/结构判断），或者用相对路径 import 真实被测文件。")
    if "No test files" in text or "no tests found" in text.lower():
        return (f"跑器没有收集到这个文件。按 {h.name} 的约定改名/改位置：{h.spec_hint}")
    if "SyntaxError" in text or "unexpected token" in text.lower():
        return "用例自身语法错误，先把它写对（它跑不起来时不能当判据）。"
    if "npm ERR!" in text:
        return "npm 脚本本身没跑起来。换 `npx <跑器> run <文件>` 的直调形式，或在配置里显式给 agent.repro.command。"
    return "复现命令没有真正执行成功，换一种更可执行的写法。"


# ------------------------------------------------------------------ 内部工具
def _pkg_scripts(root: Path) -> Dict[str, str]:
    pkg = root / "package.json"
    if not pkg.is_file():
        return {}
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        return {}
    scripts = data.get("scripts") or {}
    return {str(k): str(v) for k, v in scripts.items() if isinstance(v, str)}


def _bin_names(root: Path) -> set:
    bin_dir = root / "node_modules" / ".bin"
    if not bin_dir.is_dir():
        return set()
    out = set()
    try:
        for p in bin_dir.iterdir():
            name = re.sub(r"\.(cmd|ps1|bat|exe)$", "", p.name, flags=re.I).lower()
            out.add(name)
    except OSError:
        return set()
    return out


def _which(exe: str) -> bool:
    import shutil

    return shutil.which(exe) is not None


def _looks_like_runner(cmd: str) -> bool:
    low = cmd.lower()
    return any(k in low for k in ("vitest", "jest", "mocha", "playwright", "cypress",
                                  "pytest", "test", "spec"))
