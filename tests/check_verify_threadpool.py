"""验证「页面验收 error / 截图采集失败」的根因与修复。

场景 A（复现）：在 asyncio 事件循环内直接调用 ScreenshotVerifier.capture
    —— 等价于修复前的 /api/run（async handler 内同步调用），
    应复现 "Playwright Sync API inside the asyncio loop" 错误。

场景 B（修复后）：用 starlette.concurrency.run_in_threadpool 把 capture 丢进
    工作线程再调 —— 等价于修复后的 server.py，应能真实截图（status=ok）。

前提：本项目 web 服务已在 127.0.0.1:8765 运行（截图对象用服务自己的首页）。
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verifier import ScreenshotVerifier  # noqa: E402

BASE = "http://127.0.0.1:8765"
DEFECT = {
    "id": "VERIFY-THREADPOOL",
    "title": "验证脚本自拍照",
    "description": "对本项目 web 服务首页截图，验证 Playwright 在线程池内可用",
    "route": "/",
}


def _verifier() -> ScreenshotVerifier:
    return ScreenshotVerifier(
        base_url=BASE,
        screenshot_dir=str(PROJECT_ROOT / "screenshots"),
    )


async def in_loop() -> dict:
    return _verifier().capture("verify_loop_repro", "before", DEFECT)


async def via_pool() -> dict:
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(
        _verifier().capture, "verify_pool_ok", "before", DEFECT
    )


def main() -> int:
    if not _verifier().reachable():
        print(f"[skip] {BASE} 不可达：请先启动 web 服务再跑本脚本")
        return 2

    repro = asyncio.run(in_loop())
    print(f"[场景A 事件循环内直接调用] status={repro['status']}")
    print(f"  error={(repro.get('error') or '')[:140]}")

    ok = asyncio.run(via_pool())
    shot = Path(ok.get("screenshot") or "")
    print(f"[场景B 线程池内调用] status={ok['status']} screenshot={shot.name}")
    if ok.get("error"):
        print(f"  error={ok['error'][:140]}")

    loop_broke = repro["status"] == "error" and "asyncio" in str(repro.get("error", ""))
    pool_works = ok["status"] == "ok" and shot.exists()
    print()
    if pool_works and loop_broke:
        print("PASS: 复现了「事件循环内报错」，且线程池内截图成功 —— 修复有效")
        return 0
    if pool_works:
        print("PASS(部分): 线程池内截图成功；场景A 未复现预期文案，请看上方输出")
        return 0
    print("FAIL: 线程池内仍未成功，请看上方输出")
    return 1


if __name__ == "__main__":
    sys.exit(main())
