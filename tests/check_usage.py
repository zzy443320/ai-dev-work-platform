# -*- coding: utf-8 -*-
"""Token 用量账本与统计聚合的离线自检（不联网、不依赖 web 服务）。

覆盖：
1. 估算器：空串 0、CJK 近似 1 字 1 token、ASCII 按字符折算、单调；
2. 账本写入：字段归一（total = input+output）、坏目录不抛（统计是旁路能力）；
3. 上下文归属：task()/step() 决定 kind/run_id/title/step/agent，退出后复原；
4. 读取容错：坏 JSON 行、坏时间戳被跳过；days 过滤；
5. 分桶：日/周/月的 key 与标签、跨月跨年、空桶必须补齐；
6. 报表：合计/区间/今日/本周/本月、按类型与模型聚合、单任务排行、
   均值排除失败调用、空账本不炸；
7. 与真实调用链打通：假中转站返回 usage → 账本里是真值(estimated=False)；
   网关不回 usage → 落估算值并标 estimated=True；mock 调用不入账。

用法：
    python tests/check_usage.py
"""
import json
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TESTS_DIR))

import fake_relay  # noqa: E402
from scripts import usage  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.ai_providers import AIConfig, _stash_usage, _usage_from  # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}"
          + (f"   → {extra}" if (extra and not cond) else ""))


def line(kind="defect", ts=None, run_id="", title="", inp=100, out=50,
         estimated=False, ok=True, model="m1", mode="openai", step=""):
    """直接按账本格式拼一行 JSONL（绕开 record() 以便精确控制时间）。"""
    ts = ts or date.today().isoformat() + "T10:00:00"
    return json.dumps({
        "ts": ts, "kind": kind, "run_id": run_id, "title": title, "step": step,
        "agent": "", "model": model, "mode": mode, "attempt": 1,
        "input_tokens": inp, "output_tokens": out, "reasoning_tokens": 0,
        "total_tokens": inp + out, "estimated": estimated, "seconds": 1.5,
        "ok": ok, "error": "" if ok else "boom",
    }, ensure_ascii=False)


def write_lines(lines):
    p = usage.ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="check-usage-"))
    usage.set_dir(tmp)
    try:
        # ── 1. 估算器 ──
        print("\n[1] token 估算器")
        check("空串 → 0", usage.estimate_tokens("") == 0)
        check("中文近似 1 字 1 token",
              usage.estimate_tokens("中" * 100) == 100,
              f"got {usage.estimate_tokens('中' * 100)}")
        check("纯英文按字符折算（少于字符数）",
              0 < usage.estimate_tokens("a" * 350) < 350,
              f"got {usage.estimate_tokens('a' * 350)}")
        check("越长越大",
              usage.estimate_tokens("代码" * 50) > usage.estimate_tokens("代码" * 5))
        est = usage.estimate_call("中" * 10, "system", "回" * 5, "想" * 3)
        check("estimate_call 形状正确且标记 estimated",
              set(est) == {"input", "output", "reasoning", "estimated"}
              and est["estimated"] is True
              and est["input"] == 10 + usage.estimate_tokens("system")
              and est["output"] == 5 + 3,
              f"{est}")

        # ── 2. 账本写入 ──
        print("\n[2] 账本写入")
        rec = usage.record(model="m1", mode="openai", input_tokens=11,
                           output_tokens=22, estimated=False)
        check("total = input + output", rec["total_tokens"] == 33, f"{rec}")
        check("落盘一行", len(usage.iter_records()) == 1)
        check("ts 是可解析的本地时间",
              usage._rec_date(rec) == date.today(), rec["ts"])
        usage.set_dir(tmp / "nope" / "deeper")   # 目录不存在也要能自己建
        r2 = usage.record(model="m1", input_tokens=1, output_tokens=1)
        check("目录不存在时自动创建", usage.ledger_path().exists() and r2["total_tokens"] == 2)
        usage.set_dir(tmp)
        # 记账失败绝不抛：把 ledger 路径指向一个文件，写入必然失败
        usage.set_dir(tmp / "broken.jsonl")
        usage.record(model="m1")
        check("写入失败不抛异常（旁路能力）", True)
        usage.set_dir(tmp)

        # ── 3. 上下文归属 ──
        print("\n[3] 上下文归属")
        with usage.task("team", run_id="team-1", title="组件迁移"):
            base = usage.current()
            with usage.step("coder", agent="coder"):
                inner = usage.current()
            after = usage.current()
        check("task() 写入 kind/run_id/title",
              base["kind"] == "team" and base["run_id"] == "team-1"
              and base["title"] == "组件迁移", f"{base}")
        check("step() 叠加 step/agent 而不丢外层",
              inner["step"] == "coder" and inner["agent"] == "coder"
              and inner["run_id"] == "team-1", f"{inner}")
        check("退出 step 后复原", after.get("step", "") == "" and after["kind"] == "team")
        check("退出 task 后清空", usage.current() == {})
        with usage.task("defect", run_id="D-1", title="T"):
            r = usage.record(model="m1")
        check("record() 自动带上上下文",
              r["kind"] == "defect" and r["run_id"] == "D-1" and r["step"] == "",
              f"{r}")
        with usage.task("defect"):
            r2 = usage.record(model="m1", kind="probe", step="probe")
        check("显式参数覆盖上下文", r2["kind"] == "probe" and r2["step"] == "probe")

        # ── 4. 读取容错 ──
        print("\n[4] 读取容错")
        today = date.today()
        old = (today - timedelta(days=100)).isoformat()
        write_lines([
            line(ts=f"{today.isoformat()}T09:00:00", inp=10, out=5),
            "{ 这不是合法 JSON",
            '{"ts": "bad", "input_tokens": 1, "output_tokens": 1}',
            "",
            line(ts=f"{old}T09:00:00", inp=999, out=999),
            json.dumps({"no_ts": True}),
        ])
        allrec = usage.iter_records()
        check("坏行/坏时间戳被跳过，只剩 2 条有效", len(allrec) == 2, f"got {len(allrec)}")
        check("days 过滤生效", len(usage.iter_records(days=30)) == 1,
              f"got {len(usage.iter_records(days=30))}")

        # ── 5. 分桶 ──
        print("\n[5] 分桶（日/周/月）")
        d = date(2026, 3, 1)          # 周日
        check("日 key", usage.bucket_key(d, "day") == "2026-03-01")
        check("周 key 用 ISO 周", usage.bucket_key(d, "week") == "2026-W09",
              usage.bucket_key(d, "week"))
        check("月 key", usage.bucket_key(d, "month") == "2026-03")
        check("周一起算：周日属于上一周",
              usage.week_start(d) == date(2026, 2, 23), str(usage.week_start(d)))
        s = usage._series(date(2026, 2, 25), date(2026, 3, 4), "day")
        check("日序列补齐 8 天", len(s) == 8 and s[0] == date(2026, 2, 25), f"{len(s)}")
        sw = usage._series(date(2026, 3, 1), date(2026, 3, 20), "week")
        check("周序列按周一起步", len(sw) == 4 and sw[0] == date(2026, 2, 23)
              and sw[1] == date(2026, 3, 2), f"{sw}")
        sm = usage._series(date(2025, 11, 5), date(2026, 2, 3), "month")
        check("月序列跨年正确", [x.isoformat() for x in sm] ==
              ["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"], f"{sm}")

        # ── 6. 报表 ──
        print("\n[6] 报表聚合")
        t = today.isoformat()
        write_lines([
            line(ts=f"{t}T09:00:00", kind="defect", run_id="D-1", title="缺陷甲",
                 inp=100, out=50, model="m1", estimated=False),
            line(ts=f"{t}T09:05:00", kind="defect", run_id="D-1", title="缺陷甲",
                 inp=200, out=100, model="m1", estimated=True),
            line(ts=f"{t}T10:00:00", kind="team", run_id="team-9", title="迁移",
                 inp=1000, out=500, model="m2"),
            line(ts=f"{t}T10:01:00", kind="team", run_id="team-9", title="迁移",
                 inp=0, out=0, model="m2", ok=False),
            line(ts=f"{old}T10:00:00", kind="codetest", run_id="C-1", title="旧",
                 inp=7777, out=7777, model="m1"),
        ])
        rep = usage.report(days=30, granularity="day")
        # 150 + 300 + 1500 + 0（最后一条是失败调用，token 为 0）
        check("区间内合计正确",
              rep["totals"]["total"] == 1950 and rep["totals"]["calls"] == 4,
              f"{rep['totals']}")
        check("今日累计 = 区间内（都在今天）", rep["today"]["total"] == 1950,
              f"{rep['today']}")
        check("本月/本周包含区间", rep["this_month"]["total"] >= 1950
              and rep["this_week"]["total"] >= 1950)
        check("全量包含区间外记录",
              rep["all_time"]["total"] == 1950 + 7777 * 2,
              f"{rep['all_time']}")
        check("均值排除失败调用（1950 / 3 次成功）",
              rep["totals"]["avg"] == round(1950 / 3), f"{rep['totals']['avg']}")
        check("失败调用单独计数", rep["totals"]["failed"] == 1)
        check("估算调用单独计数", rep["totals"]["estimated"] == 1)
        check("分桶数 = 区间天数", len(rep["buckets"]) == 30, f"{len(rep['buckets'])}")
        check("分桶按时间升序且末桶是今天",
              rep["buckets"][-1]["key"] == t
              and rep["buckets"][0]["date"] < rep["buckets"][-1]["date"])
        check("今日桶数值正确",
              rep["buckets"][-1]["total"] == 1950, f"{rep['buckets'][-1]}")
        check("空桶被补出来（更早的桶 total=0）",
              rep["buckets"][0]["total"] == 0 and rep["buckets"][0]["calls"] == 0)
        check("按类型聚合降序 + 中文标签",
              [k["key"] for k in rep["kinds"]] == ["team", "defect"]
              and rep["kinds"][0]["label"] == "长任务作业", f"{rep['kinds']}")
        check("按模型聚合降序",
              [m["key"] for m in rep["models"]] == ["m2", "m1"], f"{rep['models']}")
        check("单任务排行（迁移 1500 > 缺陷甲 450）",
              [x["key"] for x in rep["tasks"]] == ["team-9", "D-1"], f"{rep['tasks']}")
        check("任务带标题与类型标签",
              rep["tasks"][0]["title"] == "迁移"
              and rep["tasks"][0]["label"] == "长任务作业", f"{rep['tasks'][0]}")
        check("任务带最后发生时间", rep["tasks"][0]["last"].startswith(t))
        check("最近调用倒序（最新在前）",
              rep["recent"][0]["ts"] >= rep["recent"][-1]["ts"])
        check("账本路径与条数如实回传",
              rep["note"]["ledger"].endswith("usage.jsonl")
              and rep["note"]["records"] == 5, f"{rep['note']}")

        # 空账本 / 缺失账本
        usage.ledger_path().unlink()
        empty = usage.report(days=7, granularity="week")
        check("空账本返回结构完整的空报表",
              empty["totals"]["total"] == 0 and len(empty["buckets"]) >= 1
              and empty["kinds"] == [] and empty["tasks"] == [],
              f"{empty['totals']}")
        check("缺失账本不抛异常", empty["all_time"]["calls"] == 0)

        # 粒度参数不合法 → 回退 day（接口层会先 400）
        usage.set_dir(tmp)
        write_lines([line(ts=f"{t}T09:00:00")])
        check("非法粒度回退为 day",
              usage.report(days=7, granularity="hour")["range"]["granularity"] == "day")

        # ── 7. usage 字段归一 ──
        print("\n[7] 网关用量归一")
        check("OpenAI 形状",
              _usage_from({"usage": {"prompt_tokens": 5, "completion_tokens": 6}})["input"] == 5)
        check("Anthropic 形状",
              _usage_from({"usage": {"input_tokens": 7, "output_tokens": 8}})["output"] == 8)
        check("reasoning_tokens 被提取",
              _usage_from({"usage": {"prompt_tokens": 1, "completion_tokens": 2,
                                     "completion_tokens_details": {"reasoning_tokens": 9}}}
                          )["reasoning"] == 9)
        check("认不出 → None（交给估算）",
              _usage_from({}) is None and _usage_from({"usage": {}}) is None
              and _usage_from(None) is None)
        cfg = AIConfig.from_dict({"provider": "custom", "base_url": "http://x",
                                  "model": "m", "api_key": "k"})
        got = _stash_usage(cfg, None, "中" * 20, "sys", "reply")
        check("网关不给 usage 时落估算值并标记 estimated",
              got["estimated"] is True and got["input"] >= 20, f"{got}")
        got2 = _stash_usage(cfg, {"usage": {"prompt_tokens": 3, "completion_tokens": 4}},
                            "p", "s", "r")
        check("网关给 usage 时用真值", got2["estimated"] is False and got2["input"] == 3)

        # ── 8. 真实调用链打通（假中转站） ──
        print("\n[8] 与真实调用链打通")
        srv, base = fake_relay.start()
        ai_cfg = {
            "provider": "custom", "base_url": base, "model": "gw-model",
            "endpoint": "/gw/chat", "content_path": "data.reply",
            "auth_style": "bearer", "api_key": "k",
        }
        usage.ledger_path().unlink(missing_ok=True)
        with usage.task("defect", run_id="D-E2E", title="假中转站工单", step="locate"):
            out = AIModel(ai_cfg).complete("hello")
        recs = usage.iter_records()
        check("真实调用写入账本", len(recs) == 1, f"got {len(recs)}")
        if recs:
            r = recs[0]
            check("用网关回的真值（假中转站给 7/21）",
                  r["input_tokens"] == 7 and r["output_tokens"] == 21
                  and r["estimated"] is False, f"{r}")
            check("归属到调用方标注的任务",
                  r["kind"] == "defect" and r["run_id"] == "D-E2E"
                  and r["step"] == "locate" and r["title"] == "假中转站工单", f"{r}")
            check("记下模型名与 mode",
                  r["model"] == "gw-model" and r["mode"] == "custom", f"{r}")

        # 流式路径（假中转站不支持流式 → 走 JSON 兜底，同样要记上用量）
        usage.ledger_path().unlink(missing_ok=True)
        seen = []
        with usage.task("team", run_id="T-E2E", title="流式"):
            AIModel(ai_cfg).complete("hello", on_delta=lambda k, t: seen.append(k))
        recs = usage.iter_records()
        check("流式兜底路径也入账", len(recs) == 1, f"got {len(recs)}")
        check("流式兜底用网关真值",
              recs and recs[0]["input_tokens"] == 7 and recs[0]["estimated"] is False,
              f"{recs}")

        # mock 不入账
        usage.ledger_path().unlink(missing_ok=True)
        with usage.task("defect", run_id="D-MOCK"):
            AIModel({"mock": True}).complete("anything")
        check("mock 调用不入账（不消耗 token）", usage.iter_records() == [])

        srv.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(OK)} 项，失败 {len(BAD)} 项")
    if BAD:
        print("失败项：" + "；".join(BAD))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
