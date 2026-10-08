# -*- coding: utf-8 -*-
"""把脚本式用例接进 pytest：只按退出码收编，不改这些脚本一行。

现状与为什么不「顺手改成 pytest 用例」：tests/ 下 40 多个文件是 `check()` 打印 +
`sys.exit(1 if FAIL else 0)` 的自包含脚本，各自负责起临时服务、建临时仓库、清自己的
现场（见 tests/temp_server.py）。把它们逐个改写成 pytest 函数收益很小、风险却大
（共享状态、隔离方式、Windows 下的端口与只读文件都是坑）。失败信号本来就已经统一到
退出码上了，所以这一层就是个薄胶水：pytest 负责汇总与挑选，脚本负责怎么跑。

清单是**按实测行为**筛的，不是按文件名：
  - 入选：不依赖浏览器、不依赖外部已启动的服务、不依赖私有仓库，单跑 < ~90s；
  - 排除：check_vue_*.py / check_*_ui.py（Playwright，整套 5–10 分钟，另走
    `python tests/check_vue_all.py`）；test_browser_*.py（要先把服务起在 8765 并
    准备 mock 仓库）；check_locate_regression*.py（要读你本机的私有前端仓）。

跑法：
    .venv/Scripts/python.exe -m pytest                 # 安全子集
    .venv/Scripts/python.exe -m pytest tests/suite_pytest.py -k origin
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 安全 + 数据完整性：这组必须常跑，挂了等于把用户仓库/密钥暴露面打开。
SECURITY = [
    "check_origin_guard.py",      # 本机同源闸门（CORS 通配回归）
    "check_write_guard.py",       # 写盘护栏：undo preflight / 路径越界 / 回滚如实报告
    "check_render_escape.py",     # v-html 转义（含 Playwright，但用临时仓库，无外部依赖）
    "check_kb_export.py",         # 知识库脱敏与泄密自检
]
# 流水线核心行为
PIPELINE = [
    "test_safety.py",             # 采纳/撤销/闸门/漂移 六个场景
    "check_fix_agent.py",         # agentic 修复循环：沙箱隔离、红→绿、不收敛、预算、白名单
    "check_artifact_diff.py",
    "check_patch_fallback.py",
    "check_kb_patterns.py",
    "check_changelog.py",
    "check_usage.py",
]

ALL = SECURITY + PIPELINE


def _run_script(name: str) -> None:
    """跑一个脚本式用例，断言退出码为 0。

    输出**重定向到临时文件而不是管道**。这是踩过的坑：这些脚本内部还会派生
    uvicorn 孙进程（temp_server.serve()），一旦超时只杀直接子进程，孙进程仍握着
    stdout 管道，communicate() 就永远等不到 EOF —— 整套会在某个晚上挂住 80 分钟，
    而单独跑同一个脚本只要 8 秒。改成写文件，杀完就是杀完。
    """
    env = dict(os.environ)
    # Windows 控制台默认 GBK：不加这两个，子进程打印中文会炸 UnicodeEncodeError，
    # 或者把断言里的中文比对搞成乱码。
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryFile(mode="w+b") as out:
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tests" / name)],
                cwd=str(ROOT), env=env,
                stdout=out, stderr=subprocess.STDOUT,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            out.seek(0)
            tail = out.read().decode("utf-8", "replace")[-3000:]
            pytest.fail(f"{name} 超过 600s 未结束（已杀死）。最后输出：\n{tail}")
        out.seek(0)
        captured = out.read().decode("utf-8", "replace")
    if proc.returncode != 0:
        pytest.fail(f"{name} 退出码 {proc.returncode}\n{captured[-4000:]}")
    # 兜底：脚本自己打了失败行却给了 0 退出码的情况。三种历史写法都覆盖：
    # [FAIL] xxx（界面用例）、"  FAIL xxx"（check_* 的 check()）、"N FAILED: [...]"。
    if re.search(r"^\s*(\[FAIL\]|FAIL |FAILED |!!)", captured, re.M):
        pytest.fail(f"{name} 退出码 0 但输出里有失败标记：\n{captured[-2500:]}")


@pytest.mark.parametrize("name", SECURITY, ids=SECURITY)
def test_security_scripts(name):
    _run_script(name)


@pytest.mark.parametrize("name", PIPELINE, ids=PIPELINE)
def test_pipeline_scripts(name):
    _run_script(name)


def test_pytest_does_not_import_script_style_files():
    """守收集规则本身：默认收集会把脚本式 test_*.py 在导入期跑完并 SystemExit。

    这条断言的价值是让「python_files 收窄」这个配置不被顺手删掉——删掉之后
    `pytest` 不是变慢，而是在收集阶段就炸，报错信息还很难看懂。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"收集阶段就失败了：\n{out[-2500:]}"
    assert "SystemExit" not in out, out[-2500:]
