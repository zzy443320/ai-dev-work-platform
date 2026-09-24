# -*- coding: utf-8 -*-
"""界面自检的安全预检：确认 8765 上的服务确实指向测试用的 mock 仓库。

为什么必须有这道闸：`test_browser_e2e.py` / `test_browser_guards.py` 会真的走
`/api/run` + 点「采纳」，**采纳就是写目标仓库**；而它们的断言又只用 mock 仓库的
git 状态验算。一旦服务其实指向真实项目仓，测试补丁就会写进真实工作区——断言还会
全红（因为去看的是另一个仓库），看起来像"测试挂了"，实际是"动了不该动的文件"。

2026-09-24 踩过一次：跑 e2e 时服务指向的是真实仓库，采纳确实落盘了（这次内容恰好
与原文件一致才没留下改动）。所以这里改成硬闸门：仓库不符就直接中止，不写任何东西。
"""
import json
import urllib.request


def _norm(p) -> str:
    return str(p or "").replace("\\", "/").rstrip("/").lower()


def server_repo(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/api/health", timeout=10) as r:
        return (json.loads(r.read().decode("utf-8")) or {}).get("repo") or ""


def require_repo(base: str, expected: str, *, what: str = "本用例") -> bool:
    """服务指向 `expected` 才返回 True；否则打印原因并返回 False（调用方应中止）。"""
    got = server_repo(base)
    if got and _norm(got) == _norm(expected):
        return True
    print(f"[skip] {what} 会真的写目标仓库，但 {base} 上的服务指向的是：\n"
          f"         {got or '(未配置)'}\n"
          f"       期望（mock 仓库）：{expected}\n"
          f"       为避免把测试补丁写进真实工作区，已中止，未执行任何写操作。\n"
          f"       请另起一个指向 mock 仓库的实例再跑本用例（见 README「测试」一节）。")
    return False
