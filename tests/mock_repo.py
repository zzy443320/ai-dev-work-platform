# -*- coding: utf-8 -*-
"""测试用 mock 目标仓库：路径解析 + 一键生成（「新克隆即可跑」的关键一环）。

为什么必须有一个 mock 仓库：`test_browser_e2e.py` / `test_browser_guards.py` 会真的
点「采纳 / 强制采纳」，也就是**真的写目标仓库**。它们只能打在一个一次性的 mock 仓库上。

为什么要有这个模块：mock 仓库的路径以前是写死的一个本机绝对路径，别人克隆下来这四个
用例直接找不到仓库。现在路径规则只有一处实现：

  1. 环境变量 `MOCK_REPO` 优先；
  2. 否则用项目根的**同级目录** `../test-mock-repo`（与 `web/server.py` 的
     `DEFAULT_REPO` 一致），所以克隆到哪个盘、哪个目录都能用。

生成好之后打印该仓库的绝对路径，把它填进服务配置（或直接
`python tests/make_mock_repo.py --set-server`）即可。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mock_repo"
BRANCH = "master"
SENTINEL = Path("src") / "components" / "ProcessList.tsx"

# 固定提交身份：不依赖本机全局 git 配置（新机器上可能压根没配 user.name，
# 那样 commit 会失败；用 `git -c` 逐次传入即可）。
_IDENT = ["-c", "user.name=mock-repo", "-c", "user.email=mock-repo@example.invalid",
          "-c", "commit.gpgsign=false"]


def path() -> Path:
    """mock 仓库路径（不保证已存在；要生成用 ensure()）。"""
    return Path(os.environ.get("MOCK_REPO") or (PROJECT_ROOT.parent / "test-mock-repo"))


def git(*args, repo=None) -> str:
    r = subprocess.run(["git", "-C", str(repo or path()), *args],
                       capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()


def is_ready(p: Path | None = None) -> bool:
    """已是一个「一次提交、工作区干净」的 mock 仓库。"""
    p = p or path()
    if not (p / ".git").exists():
        return False
    if SENTINEL.as_posix() not in git("ls-files", repo=p).replace("\\", "/"):
        return False
    return (not git("status", "--porcelain", repo=p)
            and len(git("rev-list", "HEAD", repo=p).splitlines()) == 1)


def _looks_like_mock(p: Path) -> bool:
    """只认本工具生成的仓库，避免手滑把 MOCK_REPO 指到真项目上被清空。"""
    return (p / SENTINEL).exists()


def ensure(force: bool = False) -> Path:
    """在 path() 处生成 mock 仓库；已就绪则原样返回（幂等）。"""
    p = path()
    if not force and is_ready(p):
        return p
    if p.exists():
        if not _looks_like_mock(p):
            raise RuntimeError(
                f"{p} 已存在但不是本工具生成的 mock 仓库，拒绝覆盖。\n"
                f"       换个位置：设环境变量 MOCK_REPO=<空目录> 再跑。")
        shutil.rmtree(p, ignore_errors=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, p)
    subprocess.run(["git", *_IDENT, "init", "-q", str(p)], check=True)
    # 分支名固定 master（config.test.yaml / 用例都按它写），不随本机 init.defaultBranch 变
    subprocess.run(["git", "-C", str(p), "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"],
                   check=True)
    subprocess.run(["git", "-C", str(p), "add", "-A"], check=True)
    subprocess.run(["git", *_IDENT, "-C", str(p), "commit", "-q", "-m", "init"], check=True)
    return p


def reset() -> Path:
    """把工作区恢复到干净状态（用例之间互不影响），提交历史保持一次。"""
    p = ensure()
    git("checkout", "--", ".", repo=p)
    git("clean", "-fd", repo=p)
    return p


if __name__ == "__main__":  # 直接跑：python tests/mock_repo.py
    print(ensure())
