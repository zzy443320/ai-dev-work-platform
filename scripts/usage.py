# -*- coding: utf-8 -*-
"""Token 用量账本与统计聚合。

四条设计红线：

1. **账本 append-only**（`usage/usage.jsonl`，一行一次真实模型调用）。不做原地更新，
   坏行/坏文件一律跳过——统计是旁路能力，绝不能因为它把一次运行搞挂。
2. **上下文用 contextvars，且在「工作线程内部」设置**。`pipeline.run` / `run_task` /
   `TeamOrchestrator.run` 本身就跑在线程池的工作线程里，在那里 `task()` 进栈，
   不依赖线程池是否传播上下文，归属语义 100% 确定。
3. **如实标注估算**。网关不回 usage 时（流式最常见）按字符估算并置 `estimated=True`，
   界面据此打「估算」角标——不把估算值伪装成账单。
4. **mock 调用不入账**。mock 不消耗 token，记进去只会污染调用次数与均值。
"""
import json
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

KIND_LABELS = {
    "defect": "缺陷修复",
    "chat": "问答",
    "team": "长任务作业",
    "reqdev": "需求开发",
    "apidebug": "接口联调",
    "codetest": "代码测试",
    "probe": "试运行 / 探针",
    "ai_test": "连通性测试",
    "other": "其他",
}
GRANULARITIES = ("day", "week", "month")

# 每行一次调用的账本；目录可由环境变量 USAGE_DIR 覆盖（测试用临时目录）
DIR = Path(os.environ.get("USAGE_DIR") or (PROJECT_ROOT / "usage"))
# 与其它数据目录一致：导入时就把目录建好。空账本不影响任何统计口径，
# 但「服务一启动，产物目录就是齐的」——用户不用猜哪个目录还没生成。
DIR.mkdir(parents=True, exist_ok=True)
LEDGER_NAME = "usage.jsonl"

_CTX: ContextVar[Dict] = ContextVar("usage_ctx", default={})
_WRITE_LOCK = threading.Lock()

# 估算用的字符族：CJK 及全角标点基本 1 字 ≈ 1 token，其余（英文/代码/JSON 结构）
# 约 3.5 字符 ≈ 1 token。仅用于网关不返回 usage 时的兜底，会打 estimated 标记。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\uff00-\uffef]")


def set_dir(path) -> Path:
    """Override the ledger directory (tests use a temp dir)."""
    global DIR
    DIR = Path(path)
    return DIR


def ledger_path() -> Path:
    return DIR / LEDGER_NAME


# ----------------------------------------------------------------- 估算
def estimate_tokens(text: str) -> int:
    """字符级 token 估算——只用于网关不回 usage 的兜底路径。"""
    text = text or ""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    return int(round(cjk + rest / 3.5))


def estimate_call(prompt: str, system: str = "", reply: str = "",
                  reasoning: str = "") -> Dict:
    """把一次调用的 prompt/回复折算成统一形状的用量（与网关回包同形）。"""
    return {
        "input": estimate_tokens(prompt) + estimate_tokens(system),
        "output": estimate_tokens(reply) + estimate_tokens(reasoning),
        "reasoning": estimate_tokens(reasoning),
        "estimated": True,
    }


# ------------------------------------------------------- 上下文（归属标注）
def current() -> Dict:
    return dict(_CTX.get() or {})


@contextmanager
def task(kind: str, run_id: str = "", title: str = "", step: str = ""):
    """标注「这次调用属于哪个任务的哪一步」。

    必须在线程内部调用（见模块 docstring 第 2 条）。
    """
    tok = _CTX.set({
        "kind": kind or "other",
        "run_id": str(run_id or ""),
        "title": str(title or "")[:160],
        "step": step or "",
    })
    try:
        yield
    finally:
        _CTX.reset(tok)


@contextmanager
def step(name: str, agent: str = "", kind: str = "", run_id: str = "", title: str = ""):
    """在当前 task 上下文里改标一个子步骤（如 team 的 coder/tester/reviewer）。"""
    ctx = dict(_CTX.get() or {})
    if name:
        ctx["step"] = name
    if agent:
        ctx["agent"] = agent
    if kind:
        ctx["kind"] = kind
    if run_id:
        ctx["run_id"] = run_id
    if title:
        ctx["title"] = title
    tok = _CTX.set(ctx)
    try:
        yield
    finally:
        _CTX.reset(tok)


# ----------------------------------------------------------------- 写入
def _append(rec: Dict) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False)
        with _WRITE_LOCK:
            with ledger_path().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        # 记账是旁路能力：磁盘满/无权限都不该让一次 AI 调用白跑
        pass


def record(*, model: str = "", mode: str = "", input_tokens: int = 0,
           output_tokens: int = 0, reasoning_tokens: int = 0,
           estimated: bool = False, seconds: float = 0.0, ok: bool = True,
           error: str = "", kind: Optional[str] = None,
           run_id: Optional[str] = None, title: Optional[str] = None,
           step: Optional[str] = None, agent: Optional[str] = None,
           attempt: int = 1) -> Dict:
    """追加一条调用记录。显式参数覆盖上下文，未给的从上下文取。"""
    ctx = _CTX.get() or {}
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": str(kind if kind is not None else ctx.get("kind") or "other"),
        "run_id": str(run_id if run_id is not None else ctx.get("run_id") or ""),
        "title": str(title if title is not None else ctx.get("title") or "")[:160],
        "step": str(step if step is not None else ctx.get("step") or ""),
        "agent": str(agent if agent is not None else ctx.get("agent") or ""),
        "model": str(model or ""),
        "mode": str(mode or ""),
        "attempt": max(1, int(attempt or 1)),
        "input_tokens": inp,
        "output_tokens": out,
        "reasoning_tokens": int(reasoning_tokens or 0),
        "total_tokens": inp + out,
        "estimated": bool(estimated),
        "seconds": round(float(seconds or 0.0), 2),
        "ok": bool(ok),
        "error": str(error or "")[:300],
    }
    _append(rec)
    return rec


# ----------------------------------------------------------------- 读取
def _rec_date(rec: Dict) -> Optional[date]:
    ts = str(rec.get("ts") or "")
    if len(ts) < 10:
        return None
    try:
        return date(int(ts[0:4]), int(ts[5:7]), int(ts[8:10]))
    except (ValueError, TypeError):
        return None


def iter_records(days: Optional[int] = None) -> List[Dict]:
    """读全部记录（坏行跳过、ts 不可解析的跳过）。`days` 给定时只回区间内的。"""
    p = ledger_path()
    if not p.exists():
        return []
    out: List[Dict] = []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or _rec_date(obj) is None:
                    continue
                out.append(obj)
    except OSError:
        return []
    if days:
        cut = date.today() - timedelta(days=max(1, int(days)) - 1)
        out = [r for r in out if (_rec_date(r) or date.min) >= cut]
    return out


def _empty_tot() -> Dict:
    return {"calls": 0, "input": 0, "output": 0, "total": 0,
            "seconds": 0.0, "estimated": 0, "failed": 0, "reasoning": 0}


def _add(tot: Dict, rec: Dict) -> None:
    tot["calls"] += 1
    tot["input"] += int(rec.get("input_tokens") or 0)
    tot["output"] += int(rec.get("output_tokens") or 0)
    tot["reasoning"] += int(rec.get("reasoning_tokens") or 0)
    tot["total"] += int(rec.get("total_tokens") or 0)
    tot["seconds"] += float(rec.get("seconds") or 0.0)
    if rec.get("estimated"):
        tot["estimated"] += 1
    if not rec.get("ok", True):
        tot["failed"] += 1


def _finalize(tot: Dict) -> Dict:
    tot["seconds"] = round(tot["seconds"], 1)
    # 均值只按成功调用算：失败调用通常是 0 token 的配置/网络错误，
    # 算进去会把"单次平均消耗"稀释成一个没意义的数
    ok_calls = tot["calls"] - int(tot.get("failed") or 0)
    tot["avg"] = int(round(tot["total"] / ok_calls)) if ok_calls > 0 else 0
    return tot


# ------------------------------------------------------------- 分桶（时间轴）
def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def bucket_key(d: date, gran: str) -> str:
    if gran == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if gran == "week":
        iso = d.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}"
    return d.isoformat()


def _bucket_label(d: date, gran: str) -> str:
    if gran == "month":
        return f"{d.year % 100:02d}/{d.month:02d}"
    if gran == "week":
        return f"{d.month:02d}/{d.day:02d} 周"
    return f"{d.month:02d}/{d.day:02d}"


def _series(from_d: date, to_d: date, gran: str) -> List[date]:
    """区间内的分桶起点（含空桶），空桶必须画出来否则趋势图会骗人。"""
    out: List[date] = []
    if gran == "month":
        cur = from_d.replace(day=1)
        while cur <= to_d:
            out.append(cur)
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        return out
    if gran == "week":
        cur = week_start(from_d)
        while cur <= to_d:
            out.append(cur)
            cur += timedelta(days=7)
        return out
    cur = from_d
    while cur <= to_d:
        out.append(cur)
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------- 聚合报表
def report(days: int = 30, granularity: str = "day",
           task_limit: int = 50, recent: int = 40) -> Dict:
    days = max(1, min(365, int(days or 30)))
    gran = granularity if granularity in GRANULARITIES else "day"
    today = date.today()
    from_d = today - timedelta(days=days - 1)

    recs = iter_records()
    win = [r for r in recs if (_rec_date(r) or date.min) >= from_d]

    # ---- 合计（区间 / 全量 / 今日 / 本周 / 本月）----
    tot = _empty_tot()
    for r in win:
        _add(tot, r)
    all_time = _empty_tot()
    for r in recs:
        _add(all_time, r)
    today_tot = _empty_tot()
    for r in recs:
        if _rec_date(r) == today:
            _add(today_tot, r)
    ws = week_start(today)
    week_tot = _empty_tot()
    for r in recs:
        d = _rec_date(r)
        if d and d >= ws:
            _add(week_tot, r)
    month_tot = _empty_tot()
    for r in recs:
        d = _rec_date(r)
        if d and d.year == today.year and d.month == today.month:
            _add(month_tot, r)

    # ---- 趋势分桶 ----
    by_key: Dict[str, Dict] = {}
    for r in win:
        d = _rec_date(r)
        if d is None:
            continue
        k = bucket_key(d, gran)
        by_key.setdefault(k, _empty_tot())
        _add(by_key[k], r)
    buckets = []
    for d in _series(from_d, today, gran):
        k = bucket_key(d, gran)
        t = _finalize(by_key.pop(k, _empty_tot()))
        t["key"] = k
        t["label"] = _bucket_label(d, gran)
        t["date"] = d.isoformat()
        buckets.append(t)

    def _group(recs_in, keyfn, meta=None):
        acc: Dict[str, Dict] = {}
        for r in recs_in:
            k = keyfn(r)
            if k is None:
                continue
            item = acc.setdefault(k, {"key": k, **_empty_tot()})
            _add(item, r)
            if meta:
                meta(item, r)
        rows = [_finalize(v) for v in acc.values()]
        rows.sort(key=lambda x: (-x["total"], -x["calls"]))
        return rows

    kinds = _group(win, lambda r: str(r.get("kind") or "other"))
    for k in kinds:
        k["label"] = KIND_LABELS.get(k["key"], k["key"])
    models = _group(win, lambda r: str(r.get("model") or "(未标注)"))
    # 模型行顺带记一下它实际观察到过哪些 mode（真实/估算）
    mode_of: Dict[str, set] = {}
    for r in win:
        mode_of.setdefault(str(r.get("model") or "(未标注)"), set()).add(
            str(r.get("mode") or ""))
    for m in models:
        m["modes"] = sorted(x for x in mode_of.get(m["key"], set()) if x)

    def _task_meta(item, r):
        item["kind"] = item.get("kind") or str(r.get("kind") or "other")
        if not item.get("title"):
            item["title"] = str(r.get("title") or "")
        item["last"] = max(item.get("last") or "", str(r.get("ts") or ""))

    tasks = _group(
        win,
        lambda r: (str(r.get("run_id") or "") or
                   f"{r.get('kind') or 'other'}::{r.get('title') or ''}"),
        _task_meta,
    )
    for t in tasks:
        t["label"] = KIND_LABELS.get(t.get("kind") or "", t.get("kind") or "")

    recent_rows = [
        {k: r.get(k) for k in ("ts", "kind", "run_id", "title", "step", "agent",
                               "model", "mode", "input_tokens", "output_tokens",
                               "total_tokens", "estimated", "seconds", "ok", "error")}
        for r in win[-int(recent or 0):][::-1]
    ] if recent else []

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "range": {"days": days, "granularity": gran,
                  "from": from_d.isoformat(), "to": today.isoformat()},
        "totals": _finalize(tot),
        "all_time": _finalize(all_time),
        "today": _finalize(today_tot),
        "this_week": _finalize(week_tot),
        "this_month": _finalize(month_tot),
        "buckets": buckets,
        "kinds": kinds,
        "models": models,
        "tasks": tasks[:max(1, int(task_limit or 50))],
        "recent": recent_rows,
        "note": {
            "ledger": str(ledger_path()),
            "records": len(recs),
            # mock 调用不入账（不消耗 token），这里如实说明而不是让用户以为漏记了
            "mock_excluded": True,
        },
    }
