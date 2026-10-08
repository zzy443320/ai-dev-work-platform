# -*- coding: utf-8 -*-
"""试跑沙箱：在**仓库之外**的一份临时副本里真跑验证命令。

为什么要它：agentic 修复循环要拿到「改完之后跑起来是什么结果」这个信号，就只能把
候选补丁落到某个地方跑 lint / tsc / 测试。落到用户真实工作区等于把「生成阶段就在
写仓库」这件事塞进一个以「人工审批是唯一写入口」为安全叙事的项目里；不落盘又只能
做静态括号检查，所谓「修对了」还是模型自说自话。沙箱是唯一的第三条路：补丁写进
临时目录，命令在临时目录里跑，用户仓库全程一个字节不动。

两个后端（`mode`）：

  worktree  `git worktree add --detach <tmp> HEAD`，再把工作区里**已改动/未跟踪**的
            文件覆盖上去。干净仓库近乎零成本；脏工作区也对得上「用户现在看到的代码」。
  copy      按 `git ls-files` 逐文件拷（没有 git 就带黑名单遍历）。给非 git 目录或
            worktree 建不出来时的兜底。

依赖问题：只拷源码的话 `npm run type-check` 会因为缺 node_modules 直接失败，那个失败
与补丁无关，回喂给模型只会把它带偏。所以沙箱会把源仓库里的 node_modules **链接**进来
（Windows 用 junction，不需要管理员权限；其它平台用 symlink）。链接不了就如实记在
notes 里，让上层知道「依赖型命令的失败可能不可信」。

命令执行的两条硬规矩（都是踩过的坑）：
  1. **白名单**：模型可以要求跑命令，但不能借沙箱的 cwd 去跑 `curl | sh`、`git push`
     或者删仓库外的东西。不在白名单里的命令直接拒绝，并把原因回给模型。
  2. **重定向到文件而不是管道**：`subprocess.run(capture_output=True, timeout=)` 超时
     只杀直接子进程，孙进程（npm → node → tsc 那种）仍握着 stdout 管道，`communicate`
     就永远等不到 EOF。整套流水线曾因此在某个晚上挂住 80 分钟。这里改成写临时文件 +
     杀整个进程树。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .repo_paths import UnsafePath, resolve_in_repo

IS_WINDOWS = os.name == "nt"

# 命令超时与输出截断
DEFAULT_CMD_TIMEOUT = 240
OUTPUT_TAIL_CHARS = 4000

# 只拷这些额外的根级文件（默认被 .gitignore 排除，但缺了它们构建就是跑不起来）
DEFAULT_COPY_EXTRA = (".env", ".env.local", ".npmrc", ".yarnrc", ".yarnrc.yml",
                      ".babelrc", ".editorconfig", "tsconfig.json", "jsconfig.json")

# 可跑命令的白名单（正则，匹配归一化后的命令行）。默认放行「读 + 构建 + 测试」，
# 不放行任何写用户仓库、发网络、发布包的动作。
DEFAULT_ALLOW_PATTERNS = (
    r"^(npm|pnpm|yarn|cnpm|tnpm|bun|bunx)\b",
    r"^npx\b",
    r"^(node|deno|tsx|ts-node)\b",
    r"^(tsc|vue-tsc|eslint|prettier|stylelint|biome|vitest|jest|mocha|karma|playwright|cypress|vite|rsbuild|webpack|rollup|turbo|lerna|nx)\b",
    r"^(python|python3|py|pytest|ruff|black|mypy)\b",
    r"^(git)\s+(diff|status|show|log|ls-files|grep|blame|rev-parse)\b",
    r"^(ls|dir|cat|type|find|findstr|rg|grep|wc|head|tail|sort|uniq|file|stat|which|where)\b",
    r"^(echo|printf|pwd|cd)\b",
)

# 命中即拒（先于白名单判定）：删除类、外发类、破坏 git 状态类。
DENY_PATTERNS = (
    r"\brm\s+-[rf]", r"\brmdir\b", r"\bdel\s+/s", r"\bRemove-Item\b",
    r"\bunlink\b", r"\bshred\b", r"\bmkfs\b", r"\bdiskpart\b",
    r">\s*[A-Za-z0-9./\\]",               # 输出重定向到文件
    r"\bgit\s+(push|reset|clean|checkout|restore|rebase|worktree)\b",
    r"\b(npm|pnpm|yarn)\s+(publish|link|add|install|i)\b",
    r"\b(curl|wget|ssh|scp)\b",
    r"\btaskkill\b", r"\bkill(all)?\b",
    r"\brd\s+/s", r"\bers\b",
    r"\|.*\b(sh|bash|python)\b",
)

# 解释器的「内联代码」开关：一旦放行，白名单就形同虚设（python -c "import os; os.remove(...)"
# 想干什么不行）。跑检查用的都是脚本文件 / 包命令，不需要内联代码。
_INLINE_CODE_FLAGS = ("-c", "--command", "-e", "--eval", "--exec", "-p", "--print",
                      "--import-time", "--exec-func")
_INTERPRETERS = {"python", "python3", "py", "node", "nodejs", "deno", "bun", "ruby",
                 "perl", "php", "sh", "bash", "zsh", "powershell", "pwsh", "cmd"}

# 依赖目录：不拷，改成链接
DEP_DIR = "node_modules"
# 扫依赖目录的最大深度（monorepo 里 packages/*/node_modules 也要链上）
DEP_SCAN_DEPTH = 3

_IGNORE_DIRS = {
    ".git", ".svn", ".hg", "dist", "build", "out", ".next", ".nuxt", ".output",
    "coverage", ".turbo", ".cache", ".parcel-cache", "__pycache__", ".venv",
    "venv", ".idea", ".vscode", ".pytest_cache", ".mw", ".temp", "node_modules",
}


class SandboxUnavailable(Exception):
    """沙箱建不出来（原因会写进 notes，上层据此降级为静态闸门，而不是当没这回事）。"""


@dataclass
class CmdResult:
    """一次命令执行的结果。`digest()` 是给收敛判定用的归一化错误指纹。"""

    name: str
    cmd: str
    returncode: int = -1
    ok: bool = False
    skipped: bool = False
    denied: bool = False
    seconds: float = 0.0
    output: str = ""
    note: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "cmd": self.cmd, "returncode": self.returncode,
            "ok": self.ok, "skipped": self.skipped, "denied": self.denied,
            "seconds": self.seconds, "note": self.note,
            "output": self.output[-OUTPUT_TAIL_CHARS:],
        }

    def digest(self) -> str:
        """错误指纹：命令名 + 归一化后的失败输出。

        归一化掉了行号、耗时、ANSI 计数这些「同一问题每次打印都不同」的数字，否则
        两次真正相同的失败会被判成不同指纹，3 次不收敛的止损就永远不会触发。
        """
        if self.ok or self.skipped:
            return f"{self.name}:ok"
        body = re.sub(r"\x1b\[[0-9;]*m", "", self.output or "")
        body = re.sub(r"\d+", "N", body)
        body = re.sub(r"[A-Za-z]:[/\\]", "/", body)      # Windows 盘符
        body = re.sub(r"/+", "/", body)
        lines = [x.strip() for x in body.splitlines() if x.strip()]
        return f"{self.name}:fail:" + "|".join(lines[:25])[:1500]


@dataclass
class ServerHandle:
    """沙箱里起起来的常驻进程（dev server）。`ready=False` 时调用方**不能**拿它当证据。"""

    command: str = ""
    url: str = ""
    port: int = 0
    ready: bool = False
    seconds: float = 0.0
    note: str = ""
    log_tail: str = ""
    # 句柄不参与相等比较，也绝不进 to_dict()（进程对象序列化不了）
    _proc: Optional[Any] = field(default=None, compare=False, repr=False)
    _log_file: Optional[Any] = field(default=None, compare=False, repr=False)

    def to_dict(self) -> Dict:
        return {"command": self.command, "url": self.url, "port": self.port,
                "ready": self.ready, "seconds": self.seconds, "note": self.note,
                "log_tail": self.log_tail[-1500:]}

    def stop(self, wait_seconds: float = 5.0) -> None:
        """杀掉服务进程树，并**等端口真的释放**再返回。

        不等就会出现两种坏事：同一批缺陷里下一次起服务抢到半死的端口，或者用户仓库外
        留着一个还在监听 127.0.0.1 的 dev server 而没人知道。
        """
        if self._proc is not None:
            _kill_tree(self._proc)
            try:
                self._proc.wait(timeout=30)
            except Exception:
                pass
            self._proc = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
        if self.port:
            end = time.time() + max(0.0, wait_seconds)
            while time.time() < end and port_open("127.0.0.1", self.port, 0.4):
                time.sleep(0.2)
            if port_open("127.0.0.1", self.port, 0.4):
                self.note = (self.note + "；" if self.note else "") + \
                    f"端口 {self.port} 停服后仍被占用，请手工确认残留进程"


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_open(host: str, port: int, seconds: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=seconds):
            return True
    except OSError:
        return False


@dataclass
class Sandbox:
    """仓库外的临时可跑副本。用 `with` 或显式 `open()/close()`。

    `available=False` 时不要静默当成成功：调用方必须把 `notes` 里的原因说出来，
    并退回静态闸门——否则用户会以为「验证通过」，而实际上什么都没跑。
    """

    repo_root: Path
    cfg: Dict = field(default_factory=dict)
    prefix: str = "ones-fix-sandbox"

    root: Optional[Path] = None
    mode: str = ""
    available: bool = False
    notes: List[str] = field(default_factory=list)
    linked: List[str] = field(default_factory=list)
    opened_seconds: float = 0.0
    _worktree: bool = False
    _tmp_parent: Optional[Path] = None

    # ------------------------------------------------------------------ 生命周期
    def open(self) -> "Sandbox":
        start = time.time()
        self.repo_root = Path(self.repo_root).resolve()
        mode = str(self.cfg.get("mode") or "auto").strip().lower()
        base = str(self.cfg.get("dir") or "").strip()
        parent = Path(base) if base else Path(tempfile.gettempdir())
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.notes.append(f"沙箱根目录不可用（{e}），已退回静态闸门")
            return self
        # 沙箱绝不能建在目标仓库里面：那等于把副本写进用户的项目（还会污染 git status），
        # 与本模块「仓库外试跑」的立身之本相反。配置错了就退回系统临时目录并说清楚。
        try:
            parent.resolve().relative_to(self.repo_root)
            self.notes.append(
                f"配置的沙箱目录 {parent} 落在目标仓库 {self.repo_root} 内，"
                "已改用系统临时目录（沙箱必须在仓库外）")
            parent = Path(tempfile.gettempdir())
        except (ValueError, OSError):
            pass
        self._tmp_parent = parent

        built = False
        if mode in ("auto", "worktree"):
            built = self._try_worktree()
        if not built and mode in ("auto", "copy"):
            if mode == "worktree":
                self.notes.append("worktree 后端失败，已改用逐文件拷贝")
            built = self._try_copy()

        if not built:
            self.mode = "unavailable"
            self.available = False
            self._purge_dir()
            return self

        self.opened_seconds = round(time.time() - start, 1)
        self.available = True
        if mode in ("auto", "worktree") and self._worktree:
            self.mode = "worktree"
        else:
            self.mode = "copy"
        if str(self.cfg.get("link_node_modules", True)).lower() not in ("false", "0"):
            self._link_dep_dirs()
        else:
            self.notes.append("已关闭依赖目录链接：type-check/lint/build 可能因缺 "
                              "node_modules 失败，这类失败与补丁无关，不要当成修复结论。")
        self.notes.append(f"沙箱后端 {self.mode}，建立耗时 {self.opened_seconds}s；"
                          f"补丁只写进 {self.root}，你的工作区未被写入。")
        return self

    def close(self) -> None:
        """删除沙箱。失败要说清楚留在哪，绝不假装删干净了。"""
        if self.root is None:
            return
        if self._worktree:
            try:
                subprocess.run(["git", "-C", str(self.repo_root), "worktree",
                                "remove", "--force", str(self.root)],
                               capture_output=True, timeout=180)
            except Exception as e:
                self.notes.append(f"git worktree remove 失败（{e}），改为直接删除临时目录")
        self._purge_dir()
        if self.root and self.root.exists():
            self.notes.append(f"沙箱目录未能删除干净，请手工清理：{self.root}")
        else:
            self.root = None

    def __enter__(self) -> "Sandbox":
        if not self.available:
            self.open()
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    def status(self) -> Dict:
        return {
            "available": self.available,
            "mode": self.mode,
            "root": str(self.root) if self.root else "",
            "notes": list(self.notes),
            "linked_dep_dirs": list(self.linked),
            "opened_seconds": self.opened_seconds,
        }

    # -------------------------------------------------------------------- 后端
    def _new_tmp(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=f"{self.prefix}-", dir=str(self._tmp_parent)))

    def _try_worktree(self) -> bool:
        """`git worktree add --detach` + 覆盖工作区改动。非 git 仓库直接放弃。"""
        if not (self.repo_root / ".git").exists():
            self.notes.append("目标不是 git 仓库，worktree 后端不适用")
            return False
        tmp = self._new_tmp()
        # git 要求 worktree 路径不存在或为空目录；mkdtemp 给的是空目录，但某些
        # git 版本仍会拒绝，所以先删掉自己建的空壳再让 git 建。
        try:
            tmp.rmdir()
        except OSError:
            pass
        # 先回收上次被强杀留下的失效登记，否则 .git/worktrees 会越积越多，
        # 严重时 `git worktree add` 会报 "already registered"。
        self._git("worktree", "prune")
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.repo_root), "worktree", "add", "--detach",
                 str(tmp), "HEAD"],
                capture_output=True, timeout=900,
            )
        except Exception as e:
            self.notes.append(f"git worktree 启动失败：{e}")
            self._purge_dir()
            return False
        if proc.returncode != 0:
            self.notes.append("git worktree add 失败："
                              + (proc.stderr or proc.stdout).decode("utf-8", "replace")[:300])
            self._purge_dir()
            return False
        self.root, self._worktree = tmp, True
        self._overlay_working_changes()
        self._copy_extra_files()
        return True

    def _try_copy(self) -> bool:
        """逐文件拷贝跟踪 + 未跟踪文件。worktree 失败或非 git 时用。"""
        tmp = self._new_tmp()
        files = self._listed_files() or self._walk_files()
        if not files:
            self.notes.append("仓库里一个可拷贝的文件都没有（路径错误或目录为空）")
            shutil.rmtree(tmp, ignore_errors=True)
            return False
        copied = 0
        for rel in files:
            src = self.repo_root / rel
            if not src.is_file():
                continue
            dst = tmp / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
            except OSError:
                continue
        self._copy_extra_files(dest=tmp)
        if not copied:
            shutil.rmtree(tmp, ignore_errors=True)
            self.notes.append("逐文件拷贝一个文件都没成功，沙箱不可用")
            return False
        self.root, self._worktree = tmp, False
        self.notes.append(f"已拷贝 {copied} 个文件到沙箱")
        return True

    def _purge_dir(self) -> None:
        if self.root and self._tmp_parent and self._tmp_parent in self.root.parents:
            shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------- 内容搬运
    def _git(self, *args: str, timeout: int = 180) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(["git", "-C", str(self.repo_root), *args],
                                  capture_output=True, timeout=timeout)
        except Exception as e:
            return -1, "", str(e)
        return (proc.returncode,
                (proc.stdout or b"").decode("utf-8", "replace"),
                (proc.stderr or b"").decode("utf-8", "replace"))

    def _listed_files(self) -> List[str]:
        """跟踪 + 未跟踪（不含 ignored）文件清单，正斜杠相对路径。"""
        rc, out, _ = self._git("ls-files", "-c", "-o", "--exclude-standard", "-z")
        if rc != 0:
            return []
        return [p.replace("\\", "/") for p in out.split("\0") if p.strip()][:20000]

    def _walk_files(self) -> List[str]:
        out: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = [d for d in dirnames
                           if d not in _IGNORE_DIRS and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    out.append(str(p.relative_to(self.repo_root)).replace("\\", "/"))
                except ValueError:
                    continue
                if len(out) >= 20000:
                    return out
        return out

    def _overlay_working_changes(self) -> None:
        """worktree 检出的是 HEAD，而用户的真实状态是工作区：把改动/未跟踪文件盖上去，
        并把工作区里已删除的文件在沙箱里也删掉。否则补丁的 SEARCH 基准就不是用户看到的代码。"""
        rc, out, _ = self._git("status", "--porcelain", "-z")
        if rc != 0 or self.root is None:
            self.notes.append("无法读取工作区改动状态，沙箱内容停留在 HEAD")
            return
        items = [s for s in out.split("\0") if s.strip()]
        changed: List[str] = []
        deleted: List[str] = []
        i = 0
        while i < len(items):
            rec = items[i]
            code, path = rec[:2], rec[3:]
            i += 1
            if code.startswith("R") or (code == "C " and " -> " in path):
                # rename 记录带两个 NUL 段：旧路径 + 新路径，取新路径
                path = items[i] if i < len(items) else path.split(" -> ")[-1]
                i += 1
            path = path.replace("\\", "/")
            if path.endswith("/"):
                # 未跟踪**目录** git 只报一条（`src/deep/`），要自己摊成文件，
                # 否则新建目录里的代码在沙箱里根本不存在，补丁必然匹配不上。
                base = self.repo_root / path.rstrip("/")
                if base.is_dir():
                    for f in sorted(base.rglob("*")):
                        if f.is_file():
                            changed.append(
                                str(f.relative_to(self.repo_root)).replace("\\", "/"))
                continue
            if "D" in code and not (self.repo_root / path).exists():
                deleted.append(path)
            else:
                changed.append(path)
        for rel in changed:
            src = self.repo_root / rel
            if not src.is_file():
                continue
            dst = self.root / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            except OSError:
                continue
        for rel in deleted:
            try:
                (self.root / rel).unlink(missing_ok=True)
            except OSError:
                continue
        if changed or deleted:
            self.notes.append(f"已把工作区现有改动同步进沙箱（{len(changed)} 改动 / "
                              f"{len(deleted)} 删除）")

    def _copy_extra_files(self, dest: Optional[Path] = None) -> None:
        tmp = dest or self.root
        if tmp is None:
            return
        patterns = list(self.cfg.get("copy_extra") or DEFAULT_COPY_EXTRA)
        for pat in patterns:
            rel = str(pat or "").strip().replace("\\", "/")
            if not rel:
                continue
            src = self.repo_root / rel
            if src.is_file():
                try:
                    (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, tmp / rel)
                except OSError:
                    pass

    def _link_dep_dirs(self) -> None:
        """把源仓库的 node_modules 之类依赖目录链接进沙箱（同相对路径）。"""
        if self.root is None:
            return
        for rel in self._find_dep_dirs():
            src = self.repo_root / rel
            dst = self.root / rel
            if not src.is_dir() or dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if self._make_dir_link(src, dst):
                self.linked.append(rel)
            else:
                self.notes.append(
                    f"依赖目录 {rel} 无法链接进沙箱（Windows 需要开发者模式或管理员权限时"
                    "才允许 symlink）；若 type-check/lint 报「找不到模块」，那是沙箱缺依赖，"
                    "不是补丁写错了，不要据此改代码。")

    def _find_dep_dirs(self) -> List[str]:
        out: List[str] = []
        root_dep = self.repo_root / DEP_DIR
        if root_dep.is_dir():
            out.append(DEP_DIR)
        for depth in range(1, DEP_SCAN_DEPTH + 1):
            pattern = "/".join(["*"] * depth) + f"/{DEP_DIR}"
            try:
                hits = list(self.repo_root.glob(pattern))
            except OSError:
                hits = []
            for p in hits[:200]:
                if p.is_dir():
                    rel = str(p.relative_to(self.repo_root)).replace("\\", "/")
                    if rel not in out:
                        out.append(rel)
            if len(out) > 300:
                break
        return out

    def _make_dir_link(self, src: Path, dst: Path) -> bool:
        if IS_WINDOWS:
            # junction：普通用户即可创建，目录级，足以让 node 的模块解析走过去
            try:
                proc = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
                    capture_output=True, timeout=120,
                )
                return proc.returncode == 0 and dst.exists()
            except Exception:
                return False
        try:
            os.symlink(str(src), str(dst), target_is_directory=True)
            return dst.exists()
        except OSError:
            return False

    def spawn_server(self, cmd: str, port: Optional[int] = None,
                     ready_timeout: int = 90, cwd_rel: str = "",
                     env_extra: Optional[Dict[str, str]] = None) -> ServerHandle:
        """在沙箱里起一个常驻 dev server，等端口通。

        与 `run()` 的区别就一件事：它**不等命令结束**。一次性检查用 run()，
        要看修复后的页面就必须有个还活着的 server。日志同样写文件不写管道（同样的
        孙进程握管道问题），环境变量里给出 PORT 并把 vite/next 常用端口指到它，
        免得跟用户自己开着的 dev server 抢 3000/5173。
        """
        if self.root is None:
            return ServerHandle(command=cmd, note="沙箱不可用，未启动服务")
        allowed, why = self.check_command(cmd)
        if not allowed:
            return ServerHandle(command=cmd, note=f"命令被沙箱白名单拒绝：{why}")
        port = int(port or free_port())
        # 命令行里的 {port} 由我们填：vite 用 --port、next 用 -p、脚本各有各的写法，
        # 与其猜，不如让配置里写 `npm run dev -- --port {port}` 这种明确形式。
        cmd = str(cmd or "").replace("{port}", str(port))
        cwd = self.root
        if str(cwd_rel or "").strip():
            try:
                sub = resolve_in_repo(self.root, cwd_rel)
                if sub.is_dir():
                    cwd = sub
            except UnsafePath:
                pass
        env = dict(os.environ)
        env.update({"CI": "true", "NO_COLOR": "1", "FORCE_COLOR": "0",
                    "BROWSER": "none", "PORT": str(port)})
        for k, v in (env_extra or {}).items():
            if v:
                env[str(k)] = str(v)
        url = f"http://127.0.0.1:{port}"
        log_file = tempfile.TemporaryFile(mode="w+b")
        start = time.time()
        try:
            popen_kw = dict(cwd=str(cwd), stdout=log_file, stderr=subprocess.STDOUT,
                            env=env, stdin=subprocess.DEVNULL)
            if IS_WINDOWS:
                proc = subprocess.Popen(cmd, shell=True,
                                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                                        **popen_kw)
            else:
                proc = subprocess.Popen(cmd, shell=True, start_new_session=True,
                                        **popen_kw)
        except Exception as e:
            log_file.close()
            return ServerHandle(command=cmd, url=url, port=port,
                                note=f"启动失败：{type(e).__name__}: {e}")

        ready = False
        deadline = start + max(10, int(ready_timeout))
        while time.time() < deadline:
            if proc.poll() is not None:
                break                       # 进程已经退了，再等也没有意义
            if port_open("127.0.0.1", port):
                ready = True
                break
            time.sleep(0.7)
        # 端口通了不等于首屏渲染好了：多等 2s，拿不到内容也别把「没等到」说成「没问题」
        if ready:
            time.sleep(2.0)
        log_file.flush()
        log_file.seek(0)
        tail = _tail(_decode_output(log_file.read()), 2500)
        note = ""
        if not ready:
            code = proc.poll()
            note = (f"服务进程已退出（退出码 {code}），没有监听 {port}" if code is not None
                    else f"服务在 {ready_timeout}s 内没有监听 {port}")
        handle = ServerHandle(command=cmd, url=url, port=port, ready=ready,
                              seconds=round(time.time() - start, 1), note=note,
                              log_tail=tail, _proc=proc, _log_file=log_file)
        if not ready:
            handle.stop()
        return handle

    # ---------------------------------------------------------------- 读写接口
    def _resolve(self, rel: str) -> Path:
        if self.root is None:
            raise SandboxUnavailable("沙箱未建立")
        # 与真实写盘同一套路径闸门：模型给的路径越界，在沙箱里也一样不许
        return resolve_in_repo(self.root, rel)

    def write(self, rel: str, content: str) -> Path:
        target = self._resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read(self, rel: str) -> str:
        target = self._resolve(rel)
        if not target.is_file():
            return ""
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return target.read_text(encoding="gbk", errors="replace")

    # ------------------------------------------------------------------ 跑命令
    def check_command(self, cmd: str) -> Tuple[bool, str]:
        """命令能不能跑。先过黑名单再过白名单，返回 (允许, 原因)。"""
        raw = str(cmd or "").strip()
        if not raw:
            return False, "命令为空"
        if len(raw) > 500:
            return False, "命令过长（>500 字符），拒绝执行"
        norm = re.sub(r"[<>]{2,}", "", raw.lower())
        for pat in DENY_PATTERNS:
            if re.search(pat, norm, re.I):
                return False, (f"命令命中沙箱禁用规则 {pat!r}：沙箱只允许跑构建/测试/检查类"
                               "命令，删除、外发、发布、改 git 状态的命令一律不可用")
        head, rest = _split_head(norm)
        if head in _INTERPRETERS and _has_inline_code(rest):
            return False, ("沙箱不放行解释器的内联代码（-c / -e / --eval 之类）："
                           "那等于把白名单整个绕过去。请把要跑的东西写成脚本文件，"
                           "或用 npx <pkg> run 的形式调用现成检查器")
        # 带路径的解释器（"C:\Program Files\...\python.exe" check.py）剥掉路径后再匹配白名单
        allow = tuple(self.cfg.get("allow") or DEFAULT_ALLOW_PATTERNS)
        haystacks = [norm] if not head else [norm, head + rest]
        for pat in allow:
            try:
                if any(re.search(pat, h, re.I) for h in haystacks):
                    return True, ""
            except re.error:
                continue
        return False, ("命令不在沙箱白名单里。可用：npm/pnpm/yarn/npx、node、"
                       "tsc/vue-tsc/eslint/vitest/jest/playwright、python/pytest、"
                       "git diff/status/show、ls/cat/grep 等只读与检查类命令")

    def run(self, cmd: str, name: str = "", timeout: Optional[int] = None,
            cwd_rel: str = "") -> CmdResult:
        """在沙箱里跑一条命令。工作目录只在沙箱内，永不指向用户仓库。"""
        if self.root is None:
            return CmdResult(name=name or cmd, cmd=cmd, skipped=True,
                             note="沙箱不可用，命令未执行")
        allowed, why = self.check_command(cmd)
        if not allowed:
            return CmdResult(name=name or cmd.split()[0] if cmd else "cmd", cmd=cmd,
                             denied=True, note=why,
                             output=f"[denied] {why}")
        cwd = self.root
        if str(cwd_rel or "").strip():
            try:
                sub = resolve_in_repo(self.root, cwd_rel)
                if sub.is_dir():
                    cwd = sub
            except UnsafePath:
                pass
        timeout = int(timeout or self.cfg.get("per_command_timeout") or DEFAULT_CMD_TIMEOUT)
        label = name or (cmd.strip().split()[0] if cmd.strip() else "cmd")
        return _execute(label, cmd, cwd, timeout)


def _split_head(norm: str) -> Tuple[str, str]:
    """拆出命令的可执行文件名与参数。带引号或带路径的解释器都要剥成裸名字。"""
    m = re.match(r'^["\'](.+?)["\']\s*(.*)$', norm, re.S)
    if not m:
        parts = norm.split(None, 1)
        head = parts[0] if parts else ""
        rest = (" " + parts[1]) if len(parts) > 1 else ""
    else:
        head, rest = m.group(1), (" " + m.group(2))
    name = Path(str(head).replace("\\", "/")).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name, rest


def _has_inline_code(rest: str) -> bool:
    """参数里是否出现解释器的内联代码开关（含 `--eval=...` 这种带值形态）。"""
    for tok in re.split(r"\s+", str(rest or "").strip()):
        if not tok:
            continue
        base = tok.split("=", 1)[0].lower()
        if base in _INLINE_CODE_FLAGS:
            return True
        # 短选项簇：-ec / -uc 里嵌着 -c
        if len(base) > 2 and base.startswith("-") and not base.startswith("--"):
            if base[1] in ("c", "e", "p"):
                return True
    return False


def _execute(name: str, cmd: str, cwd: Path, timeout: int) -> CmdResult:
    """跑命令 → CmdResult。超时杀整棵进程树，输出走临时文件不走管道。"""
    start = time.time()
    env = dict(os.environ)
    # CI=true 让 vitest/jest 不进 watch 模式；不关色码的话指纹里全是 ANSI 噪声
    env.update({"CI": "true", "NO_COLOR": "1", "FORCE_COLOR": "0",
                "GIT_TERMINAL_PROMPT": "0", "BROWSER": "none"})
    out_file = tempfile.TemporaryFile(mode="w+b")
    creation = 0
    if IS_WINDOWS:
        creation = subprocess.CREATE_NEW_PROCESS_GROUP  # 便于 tree-kill
    else:
        creation = 0
    try:
        popen_kw = dict(cwd=str(cwd), stdout=out_file, stderr=subprocess.STDOUT, env=env)
        if IS_WINDOWS:
            proc = subprocess.Popen(cmd, shell=True, creationflags=creation, **popen_kw)
        else:
            proc = subprocess.Popen(cmd, shell=True, start_new_session=True, **popen_kw)
    except Exception as e:
        out_file.close()
        return CmdResult(name=name, cmd=cmd, returncode=-1, output=f"[spawn failed] {e}")

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            proc.wait(timeout=30)
        except Exception:
            pass
    out_file.flush()
    out_file.seek(0)
    try:
        text = _decode_output(out_file.read())
    finally:
        out_file.close()
    tail = _tail(text)
    if timed_out:
        return CmdResult(name=name, cmd=cmd, returncode=-1,
                         seconds=round(time.time() - start, 1),
                         output=tail + f"\n[timeout] 命令超过 {timeout}s 未结束，已强制终止"
                                       "（它可能是个常驻 dev server，不是一次性检查）")
    return CmdResult(name=name, cmd=cmd, returncode=proc.returncode,
                     ok=proc.returncode == 0, seconds=round(time.time() - start, 1),
                     output=tail)


def _kill_tree(proc: subprocess.Popen) -> None:
    """杀掉整棵进程树。只杀直接子进程会留下握着资源（或继续写文件）的孙进程。"""
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=60)
            return
        except Exception:
            pass
    else:
        try:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def _decode_output(raw) -> str:
    """子进程输出解码。Windows 上的命令行工具（tsc/eslint/自建脚本）经常吐 GBK，
    统一按 UTF-8 读会把中文报错变成乱码——而那段乱码恰恰是要回喂给模型的关键证据。
    子进程是直接往 OS 句柄写字节的，所以先按字节读回来再试编码。"""
    if isinstance(raw, str):
        return raw
    data = bytes(raw or b"")
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _tail(s: str, n: int = OUTPUT_TAIL_CHARS) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else "…(前文省略)…\n" + s[-n:]


def default_server_command(repo_root: Path) -> Tuple[str, str]:
    """从 package.json 里猜一条能起 dev server 的命令，返回 (命令, 工作目录相对路径)。

    猜不出来就返回空串——**起不来就说起不来**，不要拿一张空白页当「修复后截图」。
    命令里统一带 `{port}`，由 spawn_server 填成实际空闲端口，避免抢用户自己开着的
    3000/5173。monorepo 请在配置里显式写 sandbox_server_command / _cwd。
    """
    pkg = Path(repo_root) / "package.json"
    if not pkg.is_file():
        return "", ""
    try:
        scripts = (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {})
    except Exception:
        return "", ""
    manager = "npm"
    if (Path(repo_root) / "pnpm-lock.yaml").exists():
        manager = "pnpm"
    elif (Path(repo_root) / "yarn.lock").exists():
        manager = "yarn"
    for name in ("dev", "start", "serve"):
        if name not in scripts:
            continue
        body = str(scripts.get(name) or "").lower()
        if "next" in body:
            return f"{manager} run {name} -p {{port}}", ""
        if any(k in body for k in ("vite", "rsbuild", "webpack", "nuxt", "astro",
                                   "react-scripts", "craco", "vue-cli-service")):
            return f"{manager} run {name} -- --port {{port}}", ""
        # 不认识的技术栈就不猜参数：起歪了拿一张空白页当「修复后截图」比不看更糟
        return "", ""
    return "", ""


def resolve_sandbox_cfg(raw: Optional[Dict]) -> Dict:
    """把 config/Settings 里的 `agent` 段洗成沙箱能吃的字典（全部有安全默认值）。"""
    raw = dict(raw or {})

    def _bool(key: str, default: bool) -> bool:
        v = raw.get(key)
        if v is None:
            return default
        return str(v).strip().lower() not in ("false", "0", "no", "off", "")

    mode = str(raw.get("sandbox") or raw.get("sandbox_mode") or "auto").strip().lower()
    if mode not in ("auto", "copy", "worktree"):
        mode = "auto"
    out: Dict = {
        "mode": mode,
        "dir": str(raw.get("sandbox_dir") or "").strip(),
        "link_node_modules": _bool("link_node_modules", True),
        "copy_extra": list(raw.get("copy_extra") or DEFAULT_COPY_EXTRA),
        "per_command_timeout": _int(raw.get("per_command_timeout"), DEFAULT_CMD_TIMEOUT),
        "enabled": _bool("enabled", True),
        # 修复后页面验证用的常驻 dev server（默认 auto：能探到 vite/next 等才起）
        "server": str(raw.get("sandbox_server") or "auto").strip().lower(),
        "server_command": str(raw.get("sandbox_server_command") or "").strip(),
        "server_cwd": str(raw.get("sandbox_server_cwd") or "").strip(),
        "server_ready_timeout": _int(raw.get("sandbox_server_ready_timeout"), 90),
    }
    if out["server"] not in ("auto", "on", "off"):
        out["server"] = "auto"
    allow = raw.get("command_allow")
    if isinstance(allow, str):
        allow = [x.strip() for x in allow.splitlines() if x.strip()]
    if allow:
        # 用户追加的规则排在前面（他可能就想放行某个自建 runner）
        out["allow"] = [str(p) for p in allow] + list(DEFAULT_ALLOW_PATTERNS)
    return out


def _int(v, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if 10 <= n <= 3600 else default
