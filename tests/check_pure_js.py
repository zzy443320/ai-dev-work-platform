# -*- coding: utf-8 -*-
"""跑前端纯函数的直调断言（node frontend/tests/pure.test.mjs）。

为什么要有这一层：`frontend/tests/pure.test.mjs` 是 JS 文件，既不在 pytest 的收集范围里，
也不在 `check_vue_all.py` 那份 Playwright 清单里——不包一层，它就只是个"记得的人才跑"的脚本。

为什么需要它：**需要 node，但绝不允许"静默不跑"**。缺 node 时这个用例会直接判失败并说清原因，
而不是退出码 2 让 pytest 当成跳过——`check_stages_ui.py` 那批用例就是这样在没人注意时
变成永久空壳的（Vue 收口时 window 全局层被删，整组断言从此只报 ReferenceError，
躺在仓库里两个多月没人管）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "frontend" / "tests" / "pure.test.mjs"

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("  FAIL 找不到 node：本用例要求前端工具链可用")
        print("       （装法见 README「快速开始」：npm install && npm run build）")
        return 1
    if not TEST.is_file():
        print(f"  FAIL 断言文件不存在：{TEST}")
        return 1
    print("== 前端纯函数直调断言（步骤条推导 + Markdown 渲染）==")
    proc = subprocess.run([node, str(TEST)], cwd=str(ROOT / "frontend"),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [l for l in out.splitlines() if l.strip()]
    for l in lines:
        if l.startswith(("  PASS", "  FAIL", "==", "ALL")):
            print("  " + l)
    check("node 断言全部通过（退出码 0）", proc.returncode == 0,
          f"rc={proc.returncode} / {lines[-1] if lines else ''}"[:160])
    n = sum(1 for l in lines if l.startswith("  PASS "))
    check("跑到的断言条数不少于 20（防止文件被改空后假通过）", n >= 20, f"{n} 条")
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
