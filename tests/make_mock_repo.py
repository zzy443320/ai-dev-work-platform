# -*- coding: utf-8 -*-
"""一键准备 mock 目标仓库——「别人下载后能直接跑」的入口。

    python tests/make_mock_repo.py                 # 生成（幂等）并打印路径
    python tests/make_mock_repo.py --set-server    # 顺带把 8765 上的服务指向它

为什么需要它：`test_browser_e2e.py` / `test_browser_guards.py` 会真的点「采纳 /
强制采纳」，也就是真的写目标仓库。它们自带一道闸门（`tests/server_guard.py`），
服务没指向 mock 仓库就直接跳过（退出码 2）。这个脚本负责把 mock 仓库建出来，
并（可选）把服务指过去，让那两个用例真的能跑起来。

`--set-server` 会改服务当前的仓库配置，所以**先把原值打印出来**，方便跑完切回去。
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_repo  # noqa: E402

BASE = "http://127.0.0.1:8765"


def _api(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="生成测试用 mock 目标仓库（浏览器采纳类用例的落点）")
    ap.add_argument("--force", action="store_true", help="已存在也重建")
    ap.add_argument("--set-server", action="store_true",
                    help="把 " + BASE + " 上的服务指向这个仓库（会改当前 repo 配置）")
    args = ap.parse_args()

    p = mock_repo.ensure(force=args.force)
    print(f"[ok] mock 仓库：{p}")
    print(f"     {mock_repo.git('log', '--oneline', repo=p)}  "
          f"| 工作区：{'干净' if not mock_repo.git('status', '--porcelain', repo=p) else '有改动'}")

    if args.set_server:
        try:
            before = (_api("/api/settings").get("repo") or {})
        except Exception as e:
            print(f"[skip] {BASE} 上没有可用的服务（{e}）——先起服务再带 --set-server 跑：")
            print("       .venv/Scripts/python.exe -m web.server")
            return 0
        print(f"[改配置] 当前服务指向：{before.get('path') or '(未配置)'} "
              f"（分支 {before.get('branch') or '(未配置)'}）")
        _api("/api/settings", "POST", {"repo": {"path": str(p), "branch": mock_repo.BRANCH}})
        health = _api("/api/health")
        same = str(health.get("repo") or "").replace("\\", "/").rstrip("/").lower() \
            == str(p).replace("\\", "/").rstrip("/").lower()
        print(f"[ok] 服务现指向：{health.get('repo')}（分支 {health.get('branch')}）"
              f" {'✓ 一致' if same else '✗ 与期望不一致，请检查'}")
        print(f"     跑完想切回去：POST {BASE}/api/settings "
              f'{{"repo": {{"path": "{before.get("path")}", '
              f'"branch": "{before.get("branch")}"}}}}')
    else:
        print(f"[提示] 让服务指向它：python tests/make_mock_repo.py --set-server")

    print("\n接下来可以跑这两个「会真的写仓库」的用例（它们只认这个 mock 仓库）：")
    print("  .venv/Scripts/python.exe tests/test_browser_e2e.py")
    print("  .venv/Scripts/python.exe tests/test_browser_guards.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
