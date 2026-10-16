# -*- coding: utf-8 -*-
"""单次模型调用的**总时长**上限：推理模型不许把一次调用拖成十几分钟。

背景（2026-10-16 实测，真实网关 + deepseek-flash）：`iter_lines` 的超时只管「两个
chunk 之间的间隔」，推理模型完全可以每 30s 吐一个思考 token，把总时长拖到十几分钟；
而 `fix_agent` 的 deadline 只在**轮次边界**检查，根本拦不住。现场表现是
「流水线不报错、但十几分钟毫无进展」，用户只会觉得它卡死了。同一天实测到：一次把
额度全烧在思考上的调用 182s，加倍额度的重试更久，最长的一次调用十几分钟。

所以：`complete_stream` 里加了总时长上限（`max(ai.timeout*2, 240)`，可用环境变量
`AI_CALL_CAP_SECONDS` 覆盖），超限主动断掉并如实回报 kind=timeout。

这个上限**故意不触发**「推理截断加倍重试」：那种失败的本质是模型停不下来，翻倍只会
把等待也翻倍（实测 32k 额度照样被思考吃光）。所以还要断言
`_is_reasoning_truncation(上限错误) is False`。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 用例里把上限压到 1s（必须在 import 后、调用前设置；读的是运行时的环境变量）
os.environ["AI_CALL_CAP_SECONDS"] = "1"

from scripts.ai_model import _is_reasoning_truncation  # noqa: E402
from scripts.ai_providers import AIConfig, AIError, complete_stream  # noqa: E402

FAIL = []
PASS = 0


def ok(cond, label, detail=""):
    global PASS
    print(("  [ok]   " if cond else "  [FAIL] ") + label
          + (f" — {str(detail)[:170]}" if detail else ""))
    if cond:
        PASS += 1
    else:
        FAIL.append(label)


class _Resp:
    """假 SSE 响应：yield 若干行，每行之间 sleep，模拟「一直在思考」。"""

    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, lines, gap=0.25, repeat=1):
        self._lines = lines
        self._gap = gap
        self._repeat = repeat
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for _ in range(self._repeat):
            for ln in self._lines:
                time.sleep(self._gap)
                yield ln

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, resp):
        self._resp = resp

    def post(self, *a, **k):
        return self._resp


def _cfg(resp, timeout=120):
    cfg = AIConfig.from_dict({
        "provider": "openai", "base_url": "https://example.invalid/v1",
        "model": "fake-reasoning", "api_key": "sk-test", "mock": False,
        "timeout": timeout,
    })
    cfg.session = lambda: _Session(resp)   # 换掉 requests.Session，别真发请求
    return cfg


THINK_ONLY = ['data: {"choices":[{"delta":{"reasoning_content":"再想想"}}]}']
ANSWER = ['data: {"choices":[{"delta":{"content":"{\\"ok\\":true}"}}]}',
          "data: [DONE]"]


def main():
    print("== 单次模型调用总时长上限 ==")

    print("\n[1] 只在推理里打转的流：必须在总时长上限处主动中断")
    resp = _Resp(THINK_ONLY, gap=0.25, repeat=200)   # 理论上要 50s 才吐完
    t0 = time.time()
    err = None
    try:
        complete_stream(_cfg(resp), "写个补丁", "sys")
    except AIError as e:
        err = e
    dt = time.time() - t0
    ok(err is not None, "抛了 AIError 而不是一直挂着", repr(err)[:120])
    ok(getattr(err, "kind", "") == "timeout", "kind=timeout（上层据此不再加倍重试）",
       getattr(err, "kind", ""))
    ok("单次调用超过" in str(getattr(err, "message", "")), "错误文本说清是总时长超限",
       str(getattr(err, "message", ""))[:150])
    ok(dt < 8, "1s 上限在秒级生效（不是等流自己吐完）", f"{dt:.2f}s")
    ok(resp.closed, "主动 close 掉连接，不留半开流")

    print("\n[2] 上限故意不触发「推理截断加倍重试」")
    ok(_is_reasoning_truncation(str(getattr(err, "message", ""))) is False,
       "上限错误不归入推理截断（翻倍只会把等待也翻倍）")

    print("\n[3] 正常流不受影响（不能把好调用也砍掉）")
    good = _Resp(ANSWER, gap=0.01)
    text = complete_stream(_cfg(good), "写个补丁", "sys")
    ok(text == '{"ok":true}', "正常返回正文", text)
    ok(not good.closed, "正常结束时不强制关流")

    print("\n[4] 上限可由环境变量覆盖（特殊网关的自救口）")
    os.environ["AI_CALL_CAP_SECONDS"] = "0.3"
    try:
        complete_stream(_cfg(_Resp(THINK_ONLY, gap=0.2, repeat=100)), "p", "s")
        ok(False, "0.3s 上限下应当中断")
    except AIError as e:
        ok(getattr(e, "kind", "") == "timeout", "环境变量生效", getattr(e, "kind", ""))
    finally:
        os.environ["AI_CALL_CAP_SECONDS"] = "1"

    print(f"\n通过 {PASS} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  FAIL {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
