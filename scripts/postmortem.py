# -*- coding: utf-8 -*-
"""失败归类器：把「这次为什么没产出补丁」变成可统计、可复用的结论。

## 为什么需要它

跑新工单总会撞上"这种或那种问题"，但打捞 41 份提案之后的实情是：**问题不是无穷多种**，
而是集中在四类——

  * 16 份 invalid：其中 13 份是「模型给了分析但没给补丁」，根因又只有三种：
    定位窗口不含真凶 / 工具误导（glob 失效、路径不存在）/ 预算耗尽；
  * 10 份 gate_failed：15 次 typecheck + 3 次 `balance:`（补丁半截、括号不闭合）；
  * 6 次 budget_exhausted、4 次 not_converged；
  * 少量网关侧空返回。

真正让人累的不是问题多，而是**每来一条都要从头人肉复盘**。所以这里把复盘流程本身做成
代码：任何提案落盘后都能被判进某个已知类，并直接给出「证据 + 下一步动作 + 是否已有机制
兜住」。**只有 ledger 里没见过的新类才需要人看**——老的类一律指向已经存在的护栏与用例。

## 用法

    python -m scripts.postmortem                # 汇总现存提案
    python -m scripts.postmortem --id <提案ID>  # 单条详查
    python -m scripts.postmortem --json out.json
（run.py 里也挂了 `--postmortem`，与 --list-proposals 同一处调用。）

证据一律来自落盘记录里的**原始字段**，不做推测性改写；分类靠 `detect` 里的显式判据，
每条判据都能在记录里指到具体字段——不靠"感觉像"。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# 归类账本：第一次出现的类登记在这里（私有运行时数据，见 .gitignore 的 /usage/）
LEDGER_NAME = "failure_classes.json"

# 门禁里"基线本来就红"的判据：这些名字的检查在 agent.baseline 里也失败过
_PREEXISTING_NAMES = ("typecheck", "lint", "test", "build", "tsc", "eslint")

# 已修类：这些类在本仓库里已经有护栏 + 回归用例，重现时先重跑、再查机制
# 「健康」类：不需要动作，不算「新问题」噪声
_HEALTHY = ("shipped", "patch_ready", "human_rejected")

CLASSES: Dict[str, Dict] = {
    "patch_ready": {
        "label": "有可采纳补丁",
        "why": "补丁构建成功，等在产出物里点采纳。",
        "next": ["去产出物页 review diff，确认后采纳（采纳是唯一会写工作区的动作）。"],
        "mechanism": "",
        "case": "",
    },
    "shipped": {
        "label": "已采纳",
        "why": "人工已采纳，写进了工作区（未 commit）。",
        "next": [],
        "mechanism": "",
        "case": "",
    },
    "human_rejected": {
        "label": "人工否决",
        "why": "提案被人工判为不该采纳。",
        "next": ["看 decision 里的理由：若是「改法不对」，把正确答案补进知识库卡片，"
                 "下次同类工单会作为先例参与定位。",
                 "若否决原因是「工单描述本身有误」，回到工单补充复现信息。"],
        "mechanism": "",
        "case": "",
    },
    "ai_unavailable": {
        "label": "模型没返回内容（网关侧）",
        "why": "网关空响应 / 推理吃光 max_tokens / 超时。与代码无关，重跑往往就好。",
        "next": ["先看 usage/usage.jsonl 里这一单的 finish_reason 与 token 数；",
                 "`finish_reason=length` 且 content 为空 → 推理占满了 max_tokens，"
                 "把 ai.max_tokens 调大或换非推理模型；",
                 "整段空且无 token 记录 → 查 key / 代理 / 超时。"],
        "mechanism": "「只有 role 的响应」被识别为推理截断并触发重试",
        "case": "tests/check_fix_agent.py（推理截断签名）",
    },
    "patch_broken_syntax": {
        "label": "补丁改了但语法不闭合（半截改动）",
        "why": "replace 段吐到一半就断了（模型输出被截断），花括号/引号不闭合。",
        "next": ["看 combined_diff：如果是「改动只写了一半」→ 让模型重交，或把该文件"
                 "拆成更小的改动点；",
                 "反复出现同一文件 → 该文件偏大，先在提示里给出更窄的窗口（start/end）。"],
        "mechanism": "gate 的 balance 检查（.ts 直接拦；.vue 刻意降噪但仍点名）",
        "case": "tests/ticket_battery.py（半截改动的拦截边界）",
    },
    "gate_baseline_red": {
        "label": "门禁红，但基线本来就红",
        "why": "失败项在 agent.baseline 里未打补丁时也是红的（仓库自身环境问题）。",
        "next": ["用 agent.baseline 逐项对照，确认不是本次改动引入的；",
                 "确认无关后可在 UI 勾选强制采纳，并在提案说明里写明这条历史红项。"],
        "mechanism": "compare_with_baseline + gate.apply_baseline（2026-10-16）",
        "case": "tests/check_gate_baseline.py",
    },
    "gate_red": {
        "label": "门禁红（补丁引入的）",
        "why": "补丁构建成功，但验收命令比基线多红了项，或语法检查不过。",
        "next": ["看 gate.checks 里失败项的报错原文（提案详情页有完整输出）；",
                 "把报错原文贴回对话让 agent 定向修 —— 这类不需要重新定位。"],
        "mechanism": "基线摘除只对「未打补丁就红」的检查生效，新弄红的一律拦",
        "case": "tests/ticket_battery.py（补丁引入新红）",
    },
    "path_not_found": {
        "label": "补丁/读取打偏到不存在的路径",
        "why": "工单描述与先例卡片里的路径过时（常少一层 `runtime/` 之类），模型照抄之后"
               "既读不到文件，又把补丁交给沙箱被拒——轮次全烧在猜路径上。",
        "next": ["直接重跑这条工单：工具现在会把仓库里相近的真实路径回给模型"
                 "（repo_read / repo_list 报错即带候选，补丁打偏时也会回喂）。",
                 "若仍打偏：看 agent.trace 里那条 repo_read 的返回有没有给候选，"
                 "候选排序该更贴近工单语义（同目录段重合度）。",
                 "长期：把工单里的路径当线索、不当结论。"],
        "mechanism": "RepoReader.suggest_paths + 报错回喂（2026-10-16）",
        "case": "tests/check_path_suggest.py",
    },
    "tool_misled": {
        "label": "工具误导（答案其实在仓库里）",
        "why": "工具静默降级：glob 圈到 0 个文件却被当成「全仓无命中」，于是模型认定"
               "「代码里不存在」而认输。",
        "next": ["先确认 trace 里那次工具结果的原文：若出现「⚠ …没有匹配到任何文件」"
                 "说明护栏已生效，看模型是不是没读它；",
                 "若没有护栏提示而模型仍判「不存在」→ 又是一种新的误导，去 chat.py "
                 "对应工具加护栏 + 用例（花括号、路径、夹带 action 都是这么修的）。"],
        "mechanism": "_expand_braces 展开花括号 + glob 圈不到文件时自动放宽重搜（2026-10-15）",
        "case": "tests/check_grep_glob.py",
    },
    "locate_miss": {
        "label": "定位窗口不含真凶文件",
        "why": "analyzer 给的嫌疑窗口里没有真正要改的文件，模型没有可逐字照抄的原文，"
               "只能给分析、给不出 SEARCH 段。",
        "next": ["对比 analysis.read_files 与 root_cause/final_summary 里模型点名的文件，"
                 "差集就是缺的窗口；",
                 "用 python scripts/replay_locate.py 复跑定位，看是关键词权重、内容级命中"
                 "还是目录开窗没兜住；",
                 "工单描述里没有可用文本线索（只有标题+截图）时，这类会自然变多——"
                 "优先让模型多要一轮窗口（agent 已支持点名补窗口）。"],
        "mechanism": "内容级命中置顶 + 词条 key 二级跳转（2026-10-15）",
        "case": "tests/check_grep_glob.py（[5][6] 节）",
    },
    "stall_or_budget": {
        "label": "轮次/时间预算耗尽",
        "why": "模型在同一个失败上打转，或每轮验证太慢把预算吃掉。",
        "next": ["看 agent.stall_counts 的签名：同一个签名重复 = 验证反馈没给它新信息，"
                 "要补的是上下文（读哪一段、报错原文），不是加轮次；",
                 "若从头到尾都在查资料（trace 里全是 tool）→ 定位窗口质量太差，"
                 "按 locate_miss 处理；",
                 "确认是慢命令吃预算 → 调 agent.deadline_seconds / per_command_timeout。"],
        "mechanism": "同签名连续重复即认输（不再烧 token）",
        "case": "tests/check_fix_agent.py（不收敛）",
    },
    "search_miss": {
        "label": "SEARCH 段与原文不逐字一致",
        "why": "模型凭印象写了 SEARCH 段（缩进/空格/半行差异），沙箱拒绝整块。",
        "next": ["这类是自我修正就能过的：确认反馈里给了「先 repo_read 读到原文再照抄」"
                 "的指引与行号窗口；",
                 "若是「多处命中」→ 让模型多带一行上下文保证唯一。"],
        "mechanism": "补丁失败反馈带可执行修法（读原文再照抄）",
        "case": "tests/check_fix_agent.py（补丁未构建的反馈）",
    },
    "model_no_patch": {
        "label": "模型只给分析、不给补丁（原因待定）",
        "why": "回复里没有 SEARCH/REPLACE 块。三种常见前因（路径/定位/工具）都不成立，"
               "剩下的多是「窗口不足 + 模型选择保守」的组合。",
        "next": ["看 agent.final_summary 与 trace 尾轮：它想读哪个文件却没读到；",
                 "把那个文件直接加进定位窗口（工单里补上文件路径是最省事的做法）；",
                 "连续多单都是这一类 → 说明定位层的窗口预算整体偏小。"],
        "mechanism": "跑道断言：任何一单都不得零交付（补丁 or 归因结论）",
        "case": "tests/ticket_battery.py",
    },
}

# 分类判据里的文本关键词（都取自真实记录的原文措辞）
_KW_NO_WINDOW = ("不在提供的窗口", "窗口里没有", "不在窗口", "窗口不足", "嫌疑窗口不足",
                 "补充上下文", "未在窗口", "缺少", "没有读到")
_KW_GLOB_LIE = ("没有匹配到任何文件", "没匹配到任何文件", "全仓", "无命中", "glob")
_KW_NOT_FOUND = ("文件不存在",)
_KW_NO_CONTENT = ("模型未返回任何内容", "空响应")


def _dig(record: Dict, *path: str) -> object:
    cur: object = record
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _all_text(record: Dict) -> str:
    """把记录里所有"人写的/模型写的"文本汇总，供关键词判据扫描。"""
    parts: List[str] = []

    def walk(node: object, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk({k: record.get(k) for k in ("analysis", "patch", "gate", "agent", "verify")})
    return "\n".join(parts)


def _errors(record: Dict) -> List[str]:
    out: List[str] = list(_dig(record, "patch", "errors") or [])
    agent = record.get("agent") or {}
    for at in agent.get("attempts") or []:
        out.extend(at.get("errors") or [])
    return [str(e) for e in out]


def _has_patch(record: Dict) -> bool:
    blocks = _dig(record, "patch", "blocks") or []
    return bool(blocks) and any((b.get("search") or "").strip()
                                or (b.get("replace") or "").strip() for b in blocks)


def _gate_failed_baseline_red(record: Dict) -> bool:
    """门禁失败项在基线上也是红的 → 不是本次改动引入。"""
    failed = _dig(record, "gate", "failed_checks") or []
    if not failed:
        return False
    baseline = (record.get("agent") or {}).get("baseline") or {}
    if not baseline:
        return False
    for name in failed:
        base = str(name).split(":", 1)[0]
        item = baseline.get(base)
        if not isinstance(item, dict) or item.get("ok") is not False:
            return False
    return True


# ------------------------------------------------------------------ 单个判据
def _detect_path_not_found(record: Dict) -> Optional[List[str]]:
    hits = [e for e in _errors(record) if any(k in e for k in _KW_NOT_FOUND)]
    if not hits:
        return None
    ev = [f"补丁沙箱拒绝：{hits[0][:140]}"]
    agent = record.get("agent") or {}
    for row in agent.get("trace") or []:
        if row.get("kind") == "response" and "文件不存在" in str(row.get("preview", "")):
            ev.append(f"第 {row.get('round')} 轮模型拿到的就是「文件不存在」")
            break
    return ev


def _detect_tool_misled(record: Dict) -> Optional[List[str]]:
    errs = _errors(record)
    if any("没有匹配到任何文件" in e or "没匹配到任何文件" in e for e in errs):
        return [f"护栏当时已告警：{[e for e in errs if '匹配到任何文件' in e][0][:140]}"]
    text = _all_text(record)
    if any(k in text for k in ("没有匹配到任何文件", "没匹配到任何文件")):
        return ["记录里出现「glob 没匹配到任何文件」——工具侧静默降级已发生"]
    return None


def _detect_locate_miss(record: Dict) -> Optional[List[str]]:
    text = _all_text(record)
    hit = [k for k in _KW_NO_WINDOW if k in text]
    if not hit:
        return None
    read_files = _dig(record, "analysis", "read_files") or []
    return [f"窗口/上下文相关措辞：{'、'.join(hit[:3])}",
            f"已给窗口 {len(read_files)} 个文件，真凶不在其中"] if read_files else \
           [f"窗口/上下文相关措辞：{'、'.join(hit[:3])}"]


def _detect_stall_or_budget(record: Dict) -> Optional[List[str]]:
    agent = record.get("agent") or {}
    concl = str(agent.get("conclusion") or "")
    if concl not in ("budget_exhausted", "not_converged", "stalled"):
        return None
    ev = [f"agent.conclusion={concl}"
          f"（{agent.get('rounds', '?')}/{agent.get('max_rounds', '?')} 轮，"
          f"{agent.get('elapsed_seconds', '?')}s）"]
    stall = agent.get("stall_counts") or {}
    if stall:
        top = max(stall.items(), key=lambda kv: kv[1])
        ev.append(f"重复最多次的失败签名：{str(top[0])[:110]} ×{top[1]}")
    return ev


def _detect_search_miss(record: Dict) -> Optional[List[str]]:
    errs = _errors(record)
    for e in errs:
        if any(k in e for k in ("SEARCH", "未匹配", "多处命中", "命中 0", "search")):
            return [f"补丁块被拒：{e[:140]}"]
    return None


def _detect_ai_unavailable(record: Dict) -> Optional[List[str]]:
    ai_error = str(_dig(record, "analysis", "ai_error") or "")
    if ai_error.strip():
        return [f"analysis.ai_error={ai_error[:140]}"]
    text = _all_text(record)
    hit = [k for k in _KW_NO_CONTENT if k in text]
    if hit:
        return [f"记录里出现空返回措辞：{'、'.join(hit)}"]
    return None


# ------------------------------------------------------------------ 归口
_ORDER = [
    "path_not_found",
    "tool_misled",
    "patch_broken_syntax",
    "gate_baseline_red",
    "gate_red",
    "ai_unavailable",
    "locate_miss",
    "stall_or_budget",
    "search_miss",
    "model_no_patch",
]


def classify(record: Dict) -> Dict:
    """把一条提案记录判进某个失败类，附证据与下一步。

    优先级：先看**已发生的事实**（补丁有没有构建成功、门禁红不红），再看模型行为
    （路径打偏 / 窗口不足 / 预算耗尽）。同一单可能同时命中多个类，`also` 会保留次要项，
    但只有主类决定「下一步该做什么」——一单只该有一个行动项。
    """
    status = str(record.get("status") or "")
    gate = record.get("gate") or {}
    failed = list(gate.get("failed_checks") or [])
    built = _has_patch(record) and not (record.get("patch") or {}).get("errors")

    if status == "applied":
        key, ev = "shipped", ["decision/apply 已落盘"]
    elif status == "rejected":
        reason = str(_dig(record, "decision", "reason")
                     or _dig(record, "decision", "note") or "")[:140]
        key, ev = "human_rejected", ([f"否决理由：{reason}"] if reason else ["人工否决"])
    elif failed and any(str(n).startswith("balance:") for n in failed):
        key = "patch_broken_syntax"
        ev = [f"语法检查未过：{n}" for n in failed if str(n).startswith("balance:")][:2]
    elif failed and _gate_failed_baseline_red(record):
        key, ev = "gate_baseline_red", [f"基线同样是红的：{'、'.join(map(str, failed[:3]))}"]
    elif failed:
        key, ev = "gate_red", [f"失败检查：{'、'.join(map(str, failed[:3]))}"]
    elif status in ("pending", "gate_failed") and built:
        key = "patch_ready"
        ev = [f"补丁 {len(record.get('patch', {}).get('files') or [])} 个文件，"
              f"门禁 {gate.get('level') or '?'}"]
    else:
        detectors = [
            ("path_not_found", _detect_path_not_found),
            ("tool_misled", _detect_tool_misled),
            ("ai_unavailable", _detect_ai_unavailable),
            ("locate_miss", _detect_locate_miss),
            ("stall_or_budget", _detect_stall_or_budget),
            ("search_miss", _detect_search_miss),
        ]
        hits = []
        for name, fn in detectors:
            ev = fn(record)
            if ev:
                hits.append((name, ev))
        key, ev = hits[0] if hits else ("model_no_patch",
                                       ["没有命中任何已知前因，按「只分析不给补丁」兜底"])
        also = [h[0] for h in hits[1:]]
        info = CLASSES[key]
        return {"class": key, "label": info["label"], "why": info["why"],
                "evidence": ev, "next": list(info["next"]),
                "mechanism": info["mechanism"], "case": info["case"],
                "also": also}

    info = CLASSES[key]
    return {"class": key, "label": info["label"], "why": info["why"],
            "evidence": ev, "next": list(info["next"]),
            "mechanism": info["mechanism"], "case": info["case"], "also": []}


# ------------------------------------------------------------------ 汇总报告
def load_records(directory: Path) -> List[Dict]:
    rows: List[Dict] = []
    for p in sorted(Path(directory).glob("*.json")):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


def _load_ledger(path: Optional[Path]) -> Dict:
    if not path or not Path(path).exists():
        return {"first_seen": {}, "runs": 0}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data.setdefault("first_seen", {})
        data.setdefault("runs", 0)
        return data
    except (OSError, ValueError):
        return {"first_seen": {}, "runs": 0}


def summarize(records: Iterable[Dict], ledger_path: Optional[Path] = None,
              today: Optional[str] = None) -> Dict:
    """直方图 + **只有新类才需要人看**。ledger 里没见过的类会被登记下来。"""
    ledger = _load_ledger(ledger_path)
    seen: Dict[str, str] = dict(ledger.get("first_seen") or {})
    first_ever = not seen  # 账本第一次落盘：所有类都会"首次"，别让人误以为全是新问题
    today = today or datetime.now().strftime("%Y-%m-%d")
    counts: Dict[str, int] = {}
    fresh: List[str] = []
    items: List[Dict] = []
    for rec in records:
        got = classify(rec)
        key = got["class"]
        counts[key] = counts.get(key, 0) + 1
        if key not in seen:
            seen[key] = today
            # 只有「需要动作」的类才算新问题：已采纳/有补丁等人 review 的不在此列
            if CLASSES[key]["next"] and key not in _HEALTHY:
                fresh.append(key)
        items.append({"id": rec.get("id"), "number": _dig(rec, "defect", "number"),
                      "title": str(_dig(rec, "defect", "title") or "")[:40], **got})
    # 已有机制、却仍然高频 → 护栏不够，这才是下一步该投入的地方
    weak = [k for k, n in counts.items() if CLASSES[k]["mechanism"] and n >= 3]
    summary = {"total": len(items), "counts": counts, "fresh": fresh,
               "first_ever": first_ever, "weak_guard": sorted(weak, key=lambda k: -counts[k]),
               "unmechanized": [k for k in counts
                                if not CLASSES[k]["mechanism"] and CLASSES[k]["next"]],
               "items": items}
    if ledger_path:
        ledger["first_seen"] = seen
        ledger["runs"] = int(ledger.get("runs", 0)) + 1
        ledger["updated"] = today
        try:
            Path(ledger_path).parent.mkdir(parents=True, exist_ok=True)
            Path(ledger_path).write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        except OSError:
            pass  # 账本写不进去不影响本次结论
    return summary


def report_lines(summary: Dict) -> List[str]:
    counts = summary["counts"]
    out = [f"共 {summary['total']} 条记录，归入 {len(counts)} 个类：", ""]
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        info = CLASSES[key]
        mark = "🔧" if info["mechanism"] else "  "
        out.append(f"  {mark} {n:3}  {key:22} {info['label']}")
    if summary["fresh"]:
        out += ["", "⚠ 新类（只有这些需要人看）："]
        for key in summary["fresh"]:
            out.append(f"    - {key}：{CLASSES[key]['why']}")
            for step in CLASSES[key]["next"][:2]:
                out.append(f"        · {step}")
    else:
        out += ["", "✅ 全部是已知类：按上面的机制逐条处理即可，不需要新的排查。"]
    if summary.get("first_ever"):
        out += ["", "（注：这是第一次登记归类账本，所有类都会被记成首次出现；"
                    "从第二次起只报真正没见过的新类。）"]
    if summary.get("weak_guard"):
        out += ["", "机制已存在、却仍然高频的类（说明护栏不够，下一步该往这投）："]
        for key in summary["weak_guard"]:
            out.append(f"    - {key} × {summary['counts'][key]}："
                       f"现有机制「{CLASSES[key]['mechanism']}」；"
                       f"用例 {CLASSES[key]['case'] or '（缺）'}")
    fixed = [k for k in summary["counts"] if CLASSES[k]["mechanism"]]
    if fixed:
        out += ["", "已有机制兜住的类（重现时先重跑，再看机制）："]
        for key in fixed:
            info = CLASSES[key]
            out.append(f"    - {key}：{info['mechanism']}"
                       + (f"　用例 {info['case']}" if info["case"] else ""))
    return out


def detail_lines(item: Dict) -> List[str]:
    out = [f"{item.get('id')}  工单 {item.get('number')}  {item.get('title')}",
           f"  主类：{item['class']}（{item['label']}）",
           f"  为什么：{item['why']}"]
    if item["evidence"]:
        out.append("  证据：")
        out += [f"    - {e}" for e in item["evidence"]]
    if item["also"]:
        out.append(f"  同时命中：{'、'.join(item['also'])}（次要信号）")
    if item["mechanism"]:
        out.append(f"  已有机制：{item['mechanism']}"
                   + (f"　用例 {item['case']}" if item["case"] else ""))
    if item["next"]:
        out.append("  下一步：")
        out += [f"    {i + 1}) {s}" for i, s in enumerate(item["next"])]
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="把「为什么没产出补丁」归类")
    ap.add_argument("--dir", default="proposals", help="提案目录（默认 proposals）")
    ap.add_argument("--id", default=None, help="只看某一条提案")
    ap.add_argument("--json", dest="json_out", default=None, help="把汇总写成 JSON")
    ap.add_argument("--ledger", default=str(Path("usage") / LEDGER_NAME),
                    help="首次出现登记文件（默认 usage/failure_classes.json）")
    ap.add_argument("--no-ledger", action="store_true", help="不读不写账本（只看看）")
    args = ap.parse_args(argv)

    records = load_records(Path(args.dir))
    if not records:
        print(f"{args.dir} 下没有提案记录")
        return 1
    ledger = None if args.no_ledger else Path(args.ledger)
    summary = summarize(records, ledger_path=ledger)

    if args.id:
        target = [it for it in summary["items"] if it["id"] == args.id]
        if not target:
            print(f"未找到提案 {args.id}")
            return 1
        for line in detail_lines(target[0]):
            print(line)
        return 0

    for line in report_lines(summary):
        print(line)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n（汇总已写入 {args.json_out}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
