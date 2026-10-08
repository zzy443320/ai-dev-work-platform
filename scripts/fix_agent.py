# -*- coding: utf-8 -*-
"""Agentic 缺陷修复循环：读代码 → 出补丁 → 沙箱真跑 → 读报错 → 自我修复。

与旧的「一次成型」流水线的区别，就一句话：**模型终于能看到自己改完之后的真实结果了**。

旧链路（scripts/analyzer.py）是启发式捞 6 个文件窗口 → 单次 JSON 出 SEARCH/REPLACE →
静态括号闸门。它有三个结构性缺陷，不是调参能解决的：

  1. 看什么文件由打分排序决定，模型没有回头补查的能力——真改动点排进候选池第 7 名就是看不见；
  2. 补丁写完从没跑过，`type-check` 报错、单测红、变量名写错这些它一概不知道；
  3. 失败原因不回喂，所以同一个错误它会重复犯，用户只能重跑整条流水线碰运气。

这里把修复阶段换成 Trae/Claude Code 那种闭环：给模型仓库只读工具 + **提交候选补丁**
+ **在沙箱里跑命令** 三类能力，每轮把真实输出（含与基线对比出来的「修好了哪些 / 新弄坏了
哪些」）回灌给它，直到验证全绿或预算用尽。3 次出现同一个错误指纹就主动认输转人工，
不再烧 token 硬撑。

安全契约与全项目一致：**本模块对目标仓库只读**。候选补丁只写进 scripts/sandbox.py 建的
临时副本；产出的仍然是一份等人工审批的提案（patch_text 会由 CodeFixer 在采纳时对真实
工作区重新匹配、复检、可撤销）。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import usage
from .chat import RepoReader, _clip, _strip_dsml, _tool_label
from .gate import resolve_commands
from .patch_engine import build_patch, parse_blocks, render_block_prompt
from .repro import (STRENGTH_ASSERT, STRENGTH_NONE, STRENGTH_TEST, classify_run,
                    broken_hint, detect_harness, render_cmd, spec_ok)
from .sandbox import CmdResult, Sandbox, resolve_sandbox_cfg

# 预算默认值（config `agent:` 段可覆盖；界面参数面板同源）
DEFAULT_MAX_ROUNDS = 8
DEFAULT_DEADLINE_SECONDS = 360
DEFAULT_MAX_STALL = 3
DEFAULT_NOTE_BUDGET = 60000
# 复现环节要先占一轮（写用例 + 跑红），不额外给预算就会把修代码的轮次挤光
DEFAULT_REPRO_EXTRA_ROUNDS = 2
DEFAULT_REPRO_MAX_REWRITE = 2
REPRO_CHECK_NAME = "repro"
# 单轮送进模型的验证输出上限：再长就该用更精确的命令，而不是把日志整份吞进上下文
MAX_FEEDBACK_CHARS = 6000
MAX_DIFF_CHARS = 4000

CONCLUSION_VERIFIED = "verified"            # 基线红的跑绿了、且没弄坏别的
CONCLUSION_GREEN = "checks_pass"            # 基线本来就全绿，改完仍全绿（弱一点的正确性）
CONCLUSION_STALLED = "not_converged"        # 同一错误指纹重复出现 → 认输
CONCLUSION_BUDGET = "budget_exhausted"      # 轮次/时间用尽仍未全绿
CONCLUSION_NO_PATCH = "no_patch"            # 模型始终没给出可用补丁
CONCLUSION_NO_SANDBOX = "sandbox_unavailable"
CONCLUSION_DISABLED = "agent_disabled"      # Mock 模式 / 未开启 agentic 循环
CONCLUSION_UNVERIFIED = "unverified"        # 补丁能构建，但这个仓库没有任何可跑的验收命令

# 结论 → 是否需要人工介入（写进提案，UI 直接读这个字段决定徽标颜色）
NEEDS_HUMAN = {CONCLUSION_STALLED, CONCLUSION_BUDGET, CONCLUSION_NO_PATCH,
               CONCLUSION_NO_SANDBOX, CONCLUSION_DISABLED}

# verified 是**靠什么**验出来的，证据强度差一个量级，必须分开标：
#   repro-test   针对工单现象写的单测，从不绿到绿 —— 最接近「缺陷被修好了」
#   repro-assert 机械断言脚本（仓库没有单测跑器时的退路）—— 只证明代码改对了
#   gate-command 只有 lint/tsc/build 从红变绿 —— 只证明没改坏构建
VIA_REPRO_TEST = "repro-test"
VIA_REPRO_ASSERT = "repro-assert"
VIA_GATE = "gate-command"

# 一次尝试的分级：预算耗尽时取历史最好的一次作为最终提案，而不是「最后一次」
RANK_VERIFIED = 5
RANK_GREEN = 4
RANK_UNVERIFIED = 3     # 补丁能构建，但仓库没有可跑的验收命令
RANK_FAIL = 2           # 跑过了，仍然红（或弄坏了别的）
RANK_UNBUILT = 1        # SEARCH 没匹配上，什么都没落盘


# --------------------------------------------------------------------- 提示词
AGENT_SYSTEM = """\
你是这个前端仓库的**缺陷修复工程师**，正在一条自动化流水线里独立处理一条 ONES 缺陷工单。
你的工作方式与人在 IDE 里修 bug 一样：先看代码找证据，改，然后**真的把它跑一遍**确认修好了；
跑不过就接着改。你不是在写分析报告。

铁律：
1. 用中文思考与书写。**任何结论都必须来自工具真实返回的内容**——没读过的文件不许断言，
   不许编造文件路径、函数名或行号。
2. 你**不能直接改仓库**。改动只能通过「提交候选补丁」交付：你在回复里给出 SEARCH/REPLACE
   块，系统会把它落到一个**仓库外的临时沙箱**里，跑验收命令，并把真实输出原样回给你。
   所以「我觉得这样改应该能好」不是进展，**沙箱里跑绿了才是**。
3. 每次提交补丁后必须等系统的验证结果。系统回给你的内容里若有报错，那就是下一步的输入：
   读它、定位、改掉、再提交。禁止无视报错重复提交同一份补丁。
4. ⭐ **先立判据，再动手修**：仓库里有可用的复现手段时（见下面「复现环节」），你要先用
   `repro_add` 写一个**针对工单现象**的复现用例，它必须在未修复的代码上**跑红**——
   现在就能通过的用例会被打回重写。用例转绿 + 验收命令不新增失败，才算这次真的修好了。
   修代码的过程中**绝不许改用例凑绿**（系统会直接拒绝包含用例路径的补丁）。
5. 补丁格式（严格，标记独占一行）：
<<<<<<< SEARCH 相对仓库根的文件路径
原文里的一整段（必须与文件内容逐字一致，含缩进）
=======
改完后的一段
>>>>>>> REPLACE
   - SEARCH 只写需要改的那一小段，前后各带 2~3 行上下文即可，**不要整文件覆写**；
   - 同一文件可以有多块；每个块在文件里必须**唯一匹配**，否则会被拒绝；
   - 流水线**不支持新建文件**（需要新增文件时，把要加的内容和路径写进最后的「交给人工的
     待办」里，别硬造一个不存在的 SEARCH）。
6. 输出协议（三选一，别混）：
   - 要查东西 → **整条回复只有一个 JSON**：{"action":"call","tool":"repo_grep","arguments":{...}}
   - 要交补丁 → 一句「这次改什么、预期验证会怎么变」+ 紧随其后的 SEARCH/REPLACE 块，**不要**套 JSON
   - 要认输 → {"action":"give_up","reason":"...","evidence":"还需要人工看什么"}
7. **不要反问**，也不要让用户把文件内容贴给你——那是把活推回给用户的最后手段。缺信息就自己查。
   有多种合理改法时，选最贴合仓库现状的直接做，把选择和理由写进最终说明，让人工在审批时改主意。
8. 修**这个**缺陷，不要顺手重构。最小改动、可审、可回滚的补丁远胜大而漂亮的补丁。
"""

READ_RULES = """\
## 查仓库的规矩
- 先 `repo_grep` 拿到「路径:行号」，再用 `repo_read` 的 start/end 读 ±30 行窗口；不要一上来就读大文件。
- 报错堆栈里点名的文件（工单里有的话）几乎就是案发现场，优先读它。
- 改公共组件/hook/工具函数前，用 `repo_grep` 把**所有调用方**过一遍，不然你就是在一半的调用点上埋雷。
- 需要单独验证某个用例时可以用 `check_run`（例如 `npx vitest run src/foo.spec.ts`），它跑在同一个沙箱里。
"""

PATCH_RULES = """\
## 提交补丁的正确姿势
1. 动手前先想清楚「这条缺陷的判据是什么」——什么命令跑绿 / 什么页面不该再报错，才算修好。
2. 前几轮的顺序是：读够文件 → （有复现手段时）`repro_add` 把用例跑红 → 才交修代码的补丁。
   跳过复现直接下补丁，最后只能拿到「命令级证据」，用户看不出缺陷现象有没有消失。
3. 拿到验证结果后：
   - 全绿 → 停，给最终说明（根因 / 改动 / 验证结论 / 遗留风险）。
   - 还有报错 → 只针对报错改，别推翻重来。
   - 系统提示「相比基线新弄坏了 X」→ 先把 X 修回去，再谈缺陷本身。
"""

GIVE_UP_RULES = """\
## 什么时候认输
- 同一个验证结果第 3 次出现（系统会明确提醒你卡在这里几次了）；
- 根因不在前端仓库（在后端 / 接口 / 数据 / 权限），前端无补丁可出；
- 必须新建文件或改构建配置才能修，超出本流水线的补丁能力。
认输不丢人，**假装修好才丢人**。
"""


def _tools_text() -> str:
    rows = [
        ("repo_list", "列目录：{\"path\": \"packages/app/src\"}"),
        ("repo_read", "读文件：{\"path\": \"…\", \"start\": 120, \"end\": 160}"),
        ("repo_grep", "搜代码：{\"pattern\": \"getFinalContent\", \"glob\": \"*.vue\"}"),
        ("repro_add", "写复现用例（沙箱内新建文件，立刻在未修复代码上跑一次）："
                      "{\"path\": \"…\", \"content\": \"…\"}"),
        ("check_run", "在沙箱里跑一条检查命令：{\"cmd\": \"npx vitest run src/x.spec.ts\"}"),
        ("patch_try", "提交候选补丁：直接输出 SEARCH/REPLACE 块，系统自动识别并真跑验证"),
        ("give_up", "认输转人工：{\"action\":\"give_up\",\"reason\":\"…\"}"),
    ]
    return "\n".join(f"- `{n}` {d}" for n, d in rows)


# ------------------------------------------------------------------ 动作解析
def parse_action(text: str) -> Optional[Dict]:
    """识别「整条回复就是一个 action JSON」。比 chat 里的同类解析放宽长度上限。"""
    t = _strip_dsml((text or "").strip())
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    if not t.startswith("{") or len(t) > 20000:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(t)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    action = str(obj.get("action") or "").strip().lower()
    if action in ("give_up", "giveup"):
        return {"give_up": True,
                "reason": str(obj.get("reason") or obj.get("evidence") or "")[:1500]}
    if action not in ("call", "tool"):
        return None
    args = obj.get("arguments")
    return {"tool": str(obj.get("tool") or "").strip(),
            "arguments": args if isinstance(args, dict) else {}}


def compare_with_baseline(baseline: Dict[str, bool], after: Dict[str, bool]) -> Dict:
    """把「补丁前后的命令通过情况」折算成模型能直接行动的结论。

    只看 after 全绿是不够的：基线本来就红着三条、改完还红同样三条，既可能是「缺陷与此
    无关」，也可能是「你根本没修对」。这两种要靠基线做参照才分得开。
    """
    fixed = sorted([k for k, v in after.items() if v and not baseline.get(k, True)])
    regressions = sorted([k for k, v in after.items() if not v and baseline.get(k, True)])
    still_failing = sorted([k for k, v in after.items() if not v])
    return {"fixed": fixed, "regressions": regressions, "still_failing": still_failing,
            "all_green": all(after.values()) if after else False}


def conclusion_of(verdict: Dict, has_baseline_fail: bool) -> str:
    """一次验证的结论。返回 CONCLUSION_* 之一。"""
    if verdict["regressions"]:
        return "regression"
    if not verdict["all_green"]:
        return "failing"
    return CONCLUSION_VERIFIED if has_baseline_fail else CONCLUSION_GREEN


def checks_signature(checks: Dict[str, Dict]) -> str:
    """把一轮验证结果压成可比较的指纹——判断「卡在同一个错误上」的唯一依据。"""
    parts = []
    for name in sorted(checks):
        item = checks[name]
        parts.append(CmdResult(name=name, cmd=item.get("cmd", ""),
                               returncode=item.get("returncode", -1),
                               ok=bool(item.get("ok")),
                               output=item.get("output", "")).digest())
    return "|".join(parts)


# ====================================================================== 主体
class FixAgent:
    """一条缺陷 = 一次 agent 循环。实例不跨工单复用（沙箱与预算都是每单一份）。"""

    def __init__(self, repo_path: str, ai, *, agent_cfg: Optional[Dict] = None,
                 gate_cfg: Optional[Dict] = None,
                 emit: Optional[Callable[[Dict], None]] = None):
        self.repo_root = Path(repo_path).resolve()
        self.ai = ai
        self.cfg = dict(agent_cfg or {})
        self.gate_cfg = dict(gate_cfg or {})
        self.emit = emit
        self.max_rounds = _int(self.cfg.get("max_rounds"), DEFAULT_MAX_ROUNDS, 1, 24)
        self.deadline_seconds = _int(self.cfg.get("deadline_seconds"),
                                     DEFAULT_DEADLINE_SECONDS, 30, 1800)
        self.max_stall = _int(self.cfg.get("max_stall"), DEFAULT_MAX_STALL, 1, 8)
        self.note_budget = _int(self.cfg.get("note_budget"), DEFAULT_NOTE_BUDGET, 8000, 400000)
        self.command_timeout = _int(self.cfg.get("per_command_timeout"), 240, 10, 1800)
        self.sandbox_cfg = resolve_sandbox_cfg(self.cfg)
        # 复现环节：`agent.repro` 支持三种写法 —— off/auto/on（开关）、
        # 或 {enabled: auto, command: "...", strength: test}（自定义复现命令）
        raw_repro = self.cfg.get("repro")
        if isinstance(raw_repro, dict):
            mode = str(raw_repro.get("enabled", "auto")).strip().lower()
            self.repro_cfg = dict(raw_repro)
        else:
            mode = str(raw_repro or "auto").strip().lower()
            self.repro_cfg = {"command": str(self.cfg.get("repro_command") or "").strip(),
                              "strength": str(self.cfg.get("repro_strength") or "").strip()}
        self.repro_mode = mode if mode in ("off", "auto", "on") else "auto"
        self.repro_extra_rounds = _int(self.cfg.get("repro_extra_rounds"),
                                       DEFAULT_REPRO_EXTRA_ROUNDS, 0, 8)
        self.repro_max_rewrite = _int(self.cfg.get("repro_max_rewrite"),
                                      DEFAULT_REPRO_MAX_REWRITE, 0, 6)
        self.harness = (detect_harness(self.repo_root, self.repro_cfg)
                        if self.repro_mode != "off" else None)
        if (self.harness is not None and self.harness.usable
                and self.repro_extra_rounds):
            # 复现要占轮次：不补预算，等于把修代码的机会全让给写用例
            self.max_rounds += self.repro_extra_rounds

    # ------------------------------------------------------------------ events
    def _stage(self, detail: str, status: str = "start",
               stage: str = "agentic 修复") -> None:
        if self.emit:
            try:
                self.emit({"type": "stage", "stage": stage, "status": status,
                           "detail": detail})
            except Exception:
                pass

    def _tool_event(self, name: str, args: Dict, result: str) -> None:
        if self.emit:
            try:
                self.emit({"type": "tool", "tool": name, "arguments": args,
                           "result": _clip(result, 1200), "chars": len(result)})
            except Exception:
                pass

    # --------------------------------------------------------------------- run
    def run(self, defect: Dict, seed: Optional[Dict] = None) -> Dict:
        """跑一轮完整闭环。永不抛异常：任何失败都变成 conclusion + notes 回给上层。"""
        seed = dict(seed or {})
        result: Dict = {
            "enabled": True,
            "conclusion": CONCLUSION_NO_PATCH,
            "needs_human": True,
            "rounds": 0,
            "max_rounds": self.max_rounds,
            "patch_text": "",
            "patch_canonical": "",
            "blocks": [],
            "combined_diff": "",
            "files": [],
            "attempts": [],
            "trace": [],
            "baseline": {},
            "verification": {},
            "notes": [],
            "final_summary": "",
            "sandbox": {},
            "repro": {},
            "verified_via": "",
            "suggested_commands": [],
            "elapsed_seconds": 0.0,
            "started_at": _now(),
            "stall_counts": {},
        }
        # 复现用例的状态机：none → red（合格判据，开始修）→ green（修好了）
        #                    或 → blocked（写了几个用例都复现不出来，放弃复现环节）
        self.repro: Dict = {"attempted": False, "status": "none", "path": "",
                            "content": "", "harness": (self.harness.to_dict()
                                                       if self.harness else {}),
                            "rewrites": 0, "runs": []}
        if str(getattr(self.ai, "mode", "")) == "mock":
            result["conclusion"] = CONCLUSION_DISABLED
            result["notes"].append("Mock 模型不会真的读写代码，agentic 循环无意义，"
                                   "已退回一次成型链路")
            return result

        reader = RepoReader(str(self.repo_root), tools_enabled=True)
        sandbox = Sandbox(self.repo_root, cfg=self.sandbox_cfg).open()
        result["sandbox"] = sandbox.status()
        for note in sandbox.notes:
            self._stage(note, status="warn" if not sandbox.available else "done",
                        stage="沙箱")
        if not sandbox.available:
            result["conclusion"] = CONCLUSION_NO_SANDBOX
            result["notes"].append("沙箱建不出来，拿不到真实运行结果："
                                   + "；".join(sandbox.notes[-3:]))
            sandbox.close()
            return result

        deadline = _Deadline(self.deadline_seconds)
        try:
            commands = resolve_commands(str(self.repo_root), self.gate_cfg) or []
        except Exception as e:
            commands = []
            result["notes"].append(f"验收命令解析失败：{e}")

        baseline, baseline_notes = self._baseline(sandbox, commands)
        result["baseline"] = baseline
        result["notes"].extend(baseline_notes)
        if self.harness is None:
            result["repro"]["status"] = "disabled"
        elif not self.harness.usable:
            result["notes"].append("没有可用的复现手段（既没探测到单测跑器，也没找到可执行的"
                                   "断言脚本环境），本次只验证验收命令，不验证缺陷现象。")
        elif self.repro_mode == "auto":
            self._stage(f"复现环节就绪：{self.harness.name}（证据强度 "
                        f"{self.harness.strength}）——先写用例跑红，再动手修",
                        stage="复现用例", status="start")
        if not commands:
            suggested = suggest_commands(self.repo_root)
            result["suggested_commands"] = suggested
            if suggested:
                result["notes"].append(
                    "这个仓库没有可跑的验收命令，agentic 循环拿不到真实运行结果。"
                    "但 node_modules/.bin 里其实躺着可用的检查器，建议把它们填进配置的 "
                    "gate.commands（改完这条缺陷的判定就从「模型自说自话」变成「真跑过」）："
                    + "；".join(suggested))
            else:
                result["notes"].append(
                    "这个仓库既没有验收脚本，也没在 node_modules/.bin 里找到可用的"
                    " tsc/eslint/vitest/jest——agentic 循环退化为纯读代码 + 静态闸门。")

        context: List[str] = [self._seed_text(defect, seed, baseline)]
        attempts: List[Dict] = []
        stall: Dict[str, int] = {}
        conclusion = CONCLUSION_NO_PATCH
        gave_up = ""
        broke_early = False

        try:
            for rnd in range(1, self.max_rounds + 1):
                result["rounds"] = rnd
                if deadline.expired():
                    conclusion = CONCLUSION_BUDGET
                    result["notes"].append(
                        f"已达时间预算 {self.deadline_seconds}s，停止循环")
                    broke_early = True
                    break
                text = self._ask(reader, context, rnd, deadline, result)
                if text is None:
                    conclusion = CONCLUSION_NO_PATCH
                    broke_early = True
                    break
                action = parse_action(text)
                if action and action.get("give_up"):
                    conclusion = CONCLUSION_STALLED
                    gave_up = action.get("reason", "")
                    result["notes"].append("模型主动认输转人工：" + gave_up[:300])
                    broke_early = True
                    break
                if action and action.get("tool"):
                    context.extend(self._exec_tool(action["tool"], action["arguments"],
                                                   reader, sandbox, commands, rnd))
                    result["trace"].append({"round": rnd, "kind": "tool",
                                            "tool": action["tool"]})
                    continue
                blocks, _errs = parse_blocks(text)
                if blocks:
                    attempt = self._try_patch(text, blocks, sandbox, commands,
                                              baseline, rnd)
                    attempts.append(attempt)
                    context.extend(self._attempt_feedback(attempt, rnd))
                    self._stage(
                        f"第 {rnd} 轮补丁验证：{attempt['conclusion']}"
                        f"（{len(attempt['files'])} 个文件）",
                        status="done" if attempt["rank"] >= RANK_GREEN else "warn",
                        stage="沙箱验证")
                    sig = attempt.get("signature", "")
                    if sig and attempt["rank"] < RANK_GREEN:
                        stall[sig] = stall.get(sig, 0) + 1
                        if stall[sig] >= self.max_stall:
                            conclusion = CONCLUSION_STALLED
                            result["notes"].append(
                                f"同一验证结果已连续出现 {self.max_stall} 次，"
                                "判定不收敛，停止烧 token 转人工")
                            broke_early = True
                            break
                        if stall[sig] == self.max_stall - 1 and self.max_stall > 2:
                            context.append(
                                "### 系统\n⚠ 这已经是你第 %d 次交出**验证结果完全相同**的补丁。"
                                "再犯一次就判定不收敛并停止。要么换个思路（用 repo_read 看清"
                                "报错点、或换更小的改动），要么直接 give_up。"
                                % stall[sig])
                    if attempt["rank"] >= RANK_VERIFIED:
                        conclusion = attempt["conclusion"]
                        broke_early = True
                        break
                    continue
                result["trace"].append({"round": rnd, "kind": "noop"})
                context.append(
                    "### 系统（第 %d 轮）\n你这条回复既没有调用工具，也没有给出 "
                    "SEARCH/REPLACE 块，因此什么都没发生。请二选一：查代码（只输出一个 "
                    "action JSON）或交补丁（输出 SEARCH/REPLACE 块）。" % rnd)
            if not broke_early:
                conclusion = CONCLUSION_BUDGET if attempts else CONCLUSION_NO_PATCH
        finally:
            sandbox.close()

        result["attempts"] = attempts
        result["stall_counts"] = {k[:60]: v for k, v in stall.items()}
        best: Optional[Dict] = None
        for a in attempts:
            best = a if best is None or a["rank"] > best["rank"] else best
        result["repro"] = self.repro
        if best:
            result["patch_text"] = best.get("patch_text", "")
            result["verified_via"] = best.get("verified_via", "")
            result["patch_canonical"] = best.get("patch_canonical", "")
            result["blocks"] = best.get("blocks", [])
            result["combined_diff"] = best.get("combined_diff", "")
            result["files"] = best.get("files", [])
            result["verification"] = best.get("verification", {})
            result["best_round"] = best.get("round")
            # 最终结论以「历史最好的一次」为准：模型最后一轮崩了/超预算，都不能把
            # 之前已经跑绿的补丁说成失败，反过来也不能把没跑绿的补成 verified。
            if best.get("conclusion") in (CONCLUSION_VERIFIED, CONCLUSION_GREEN,
                                          CONCLUSION_UNVERIFIED):
                conclusion = best["conclusion"]
                if best["conclusion"] == CONCLUSION_UNVERIFIED:
                    result["notes"].append(
                        "本次补丁**未经任何真实验证**：仓库里没有可运行的验收命令，"
                        "沙箱无从判断对错，请人工跑一遍再决定是否采纳。")
        if gave_up and not (best and int(best.get("rank", 0)) >= RANK_GREEN):
            result["final_summary"] = gave_up
        elif best and best.get("summary"):
            result["final_summary"] = best["summary"]
        elif gave_up:
            # 后面又认输过一次，但历史里有跑绿的那次——结论以证据为准，认输理由附在说明后
            result["final_summary"] = (best.get("summary", "") + "\n（后续一轮模型选择认输："
                                       + gave_up[:200] + "）")
        else:
            result["final_summary"] = _auto_summary(conclusion, len(attempts))
        result["conclusion"] = conclusion
        result["needs_human"] = conclusion in NEEDS_HUMAN
        result["elapsed_seconds"] = round(self.deadline_seconds
                                          - max(0.0, deadline.left()), 1)
        return result

    # ------------------------------------------------------------------ 模型调用
    def _ask(self, reader: RepoReader, context: List[str], rnd: int,
             deadline: _Deadline, result: Dict) -> Optional[str]:
        prompt = self._prompt(reader, context, rnd, deadline)
        self._stage(f"模型分析并动手（第 {rnd}/{self.max_rounds} 轮，"
                    f"剩余 {int(max(0, deadline.left()))}s）")
        buf: List[str] = []

        def on_delta(kind: str, piece: str) -> None:
            piece = str(piece or "")
            buf.append(piece)
            if len(buf) >= 40 or "\n" in piece:
                if self.emit:
                    try:
                        self.emit({"type": "ai_delta", "kind": kind,
                                   "text": "".join(buf)})
                    except Exception:
                        pass
                buf.clear()

        with usage.step(f"agent_round_{rnd}"):
            res = self.ai.complete_text(prompt, AGENT_SYSTEM, max_tokens=16000,
                                        on_delta=on_delta)
        if res.get("error"):
            result["notes"].append(f"模型调用失败：{str(res['error'])[:300]}")
            self._stage(f"模型调用失败：{str(res['error'])[:160]}", status="fail")
            return None
        text = _strip_dsml(str(res.get("text") or ""))
        result["trace"].append({"round": rnd, "kind": "reply", "chars": len(text),
                                "preview": _clip(text, 240)})
        return text

    def _prompt(self, reader: RepoReader, context: List[str], rnd: int,
                deadline: _Deadline) -> str:
        left = int(max(0, deadline.left()))
        parts = [
            "## 任务\n处理下面这条缺陷工单。你可以用工具查仓库、提交候选补丁、"
            "在沙箱里跑命令。每提交一次补丁，系统都会给你**真实的**验证结果。\n\n"
            "### 可用工具\n" + _tools_text()
            + f"\n\n轮次预算：{self.max_rounds} 轮（一次「提交补丁 + 验证」算一轮）。",
            READ_RULES,
            PATCH_RULES,
            GIVE_UP_RULES,
            "## 目标仓库\n- 仓库根：" + str(reader.root)
            + "\n- 工具里的 path 一律用相对仓库根的形式。",
            "\n".join(_fit_notes(context, self.note_budget)),
        ]
        if rnd >= self.max_rounds:
            parts.append("## 最后一轮\n这是本轮缺陷能用的最后一轮。**不要再调用工具**："
                         "要么给出你认为最佳的一份 SEARCH/REPLACE 补丁，要么 give_up 说明卡在哪。")
        else:
            parts.append(f"## 现在（第 {rnd} 轮，剩余 {self.max_rounds - rnd} 轮 / {left}s）\n"
                         "按协议继续。")
        return "\n\n".join(parts)

    # ---------------------------------------------------------------- 种子上下文
    def _seed_text(self, defect: Dict, seed: Dict, baseline: Dict) -> str:
        desc = str(seed.get("description") or defect.get("description")
                   or defect.get("desc") or "")
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc)).strip()
        lines = ["## 缺陷工单",
                 f"- 编号：{defect.get('id') or '-'}",
                 f"- 标题：{defect.get('title') or '-'}",
                 f"- 优先级：{defect.get('priority') or '-'}"]
        if desc:
            lines.append("- 描述：\n" + _clip(desc, 4000))
        analysis = seed.get("analysis") or {}
        if analysis:
            lines.append("## 定位阶段的初步结论（**只是线索**，以你自己查到的为准）")
            for k, label in (("root_cause", "疑似根因"), ("category", "分类"),
                             ("explanation", "说明"), ("non_frontend", "非前端判断")):
                v = analysis.get(k)
                if v:
                    lines.append(f"- {label}：{_clip(str(v), 800)}")
            if analysis.get("suspect_files"):
                lines.append("- 嫌疑文件：" + "、".join(analysis["suspect_files"][:12]))
            if analysis.get("keywords"):
                lines.append("- 关键词：" + "、".join(analysis["keywords"][:12]))
        cand = str(analysis.get("patch_text") or "").strip()
        if cand:
            lines.append("## 一次成型阶段留下的候选补丁（未经验证，可能带错）\n"
                         + _clip(cand, 4000))
        page = seed.get("page") or {}
        if page:
            lines.append("## 页面现场（Playwright 复刻工单操作后观察到的）")
            lines.append(f"- 路由：{page.get('route') or '-'}  状态：{page.get('status') or '-'}")
            if page.get("console_errors"):
                lines.append("- 控制台报错：\n" + _clip(
                    "\n".join(f"  · {e}" for e in page["console_errors"][:12]), 2500))
            if page.get("ai_verdict"):
                lines.append("- 视觉判读：" + _clip(str(page["ai_verdict"]), 1200))
        lines.append(self._repro_seed())
        if baseline:
            fail = [k for k, v in baseline.items() if not v.get("ok")]
            lines.append("## 基线：沙箱里**未打补丁**时跑验收命令的结果")
            for name, item in baseline.items():
                mark = "✓ 通过" if item.get("ok") else "✗ 失败"
                lines.append(f"- {name} `{item.get('cmd', '')}` → {mark}")
                if not item.get("ok") and item.get("output"):
                    lines.append("```\n" + _clip(item["output"], 2500) + "\n```")
            if fail:
                lines.append("⚠ 基线就有失败项，这些**不是**你引入的。修好这条缺陷的判据是"
                             "「与缺陷相关的项转绿 + 不新增失败项」，不是把无关的历史红项全修绿。")
        else:
            lines.append("## 基线\n目标仓库没有可跑的验收命令（既没配 `gate.commands`，"
                         "package.json 里也没有 type-check/lint/test 脚本）。**你拿不到任何"
                         "真实运行结果**，只能靠读代码保证正确性——这种情况下把改动做到最小，"
                         "并在最终说明里写清「未经验证，需人工跑一遍」。")
        return "\n".join(lines)

    def _repro_seed(self) -> str:
        """复现环节的规矩。没跑器时也要说清楚为什么只能退到断言脚本。"""
        h = self.harness
        if h is None:
            return ("## 复现环节\n配置已关闭复现（agent.repro: off）。只按验收命令判断结果。")
        if not h.usable:
            return ("## 复现环节\n这个仓库没有可用的复现手段（没有单测跑器，也没有 node/"
                    "python 断言环境）。跳过 repro_add，直接读代码 + 交补丁。")
        head = ["## 复现环节（先红，再修）",
                f"- 可用跑器：**{h.name}**（证据强度 {h.strength}）",
                f"- 命令模板：`{h.cmd_tpl}`（{{file}} 换成你的用例路径）",
                f"- 落点建议：{h.spec_hint}"]
        if h.notes:
            head += [f"- 注意：{n}" for n in h.notes]
        if h.strength == STRENGTH_TEST:
            head.append("- 用 `repro_add` 提交用例（path + content）。它会先写在**未修复**的"
                        "代码上跑一次：**必须红**才算你复现了这条缺陷；现在就绿会被打回重写。")
        else:
            head.append("- 这个仓库没有单测跑器，只能写**机械断言脚本**（读被测源码，断言"
                        "「缺陷导致的特征不该再出现」）。它比单测弱：绿了只说明改动落实了，"
                        "不代表页面现象消失。仍然必须先红。")
        head.append("- 复现用例只存在于沙箱里，**不要**把它写进 SEARCH/REPLACE 补丁"
                    "（补丁只放修代码的改动）；系统会自动把它收成一份产出物交给人工。")
        head.append("- 顺序要求：先把用例跑红，再交修代码的补丁。跳过复现直接改代码，"
                    "最后只能拿到「命令级证据」，用户看不到缺陷是否真的消失。")
        return "\n".join(head)

    # ------------------------------------------------------------------ 跑命令
    def _run_commands(self, sandbox: Sandbox, commands: List[Dict],
                      label: str = "") -> Tuple[Dict[str, Dict], List[str]]:
        """跑一遍验收命令（有合格复现用例时连它一起跑）。被白名单拒绝的命令**从结果里
        剔掉**，而不是算通过——否则「denied → ok=True」会让模型以为这条已经验过了。"""
        specs = list(commands)
        if self.repro.get("status") == "red" and self.repro.get("path"):
            # 复现用例是这条缺陷的判据，每一次验证都必须带上它；它不在 commands 里，
            # 因为基线阶段它还不存在（那时跑它没有意义）
            specs = specs + [{"name": REPRO_CHECK_NAME,
                              "cmd": self.repro.get("cmd")
                              or render_cmd(self.harness, self.repro["path"])}]
        out: Dict[str, Dict] = {}
        notes: List[str] = []
        for idx, spec in enumerate(specs):
            r = sandbox.run(spec["cmd"], name=spec["name"], timeout=self.command_timeout)
            if r.denied:
                notes.append(f"{label}命令 {spec['name']} 被沙箱白名单拒绝（{r.note}），"
                             "该项不计入验证结论")
                continue
            name = spec["name"]
            if name in out:
                # 同名命令（配置里写重了）不能互相覆盖：后一条会把前一条的真实结果顶掉，
                # 于是「全绿」里其实少跑了一项
                name = f"{name}#{idx + 1}"
            out[name] = {"cmd": spec["cmd"], "ok": bool(r.ok or r.skipped),
                         "returncode": r.returncode, "output": r.output,
                         "seconds": r.seconds, "note": r.note}
        return out, notes

    def _baseline(self, sandbox: Sandbox, commands: List[Dict]) -> Tuple[Dict, List[str]]:
        if not commands:
            return {}, []
        self._stage("沙箱基线：未打补丁先跑一遍验收命令（取真实报错现场，也作红→绿的参照）",
                    stage="沙箱")
        out, notes = self._run_commands(sandbox, commands, label="基线")
        fails = [k for k, v in out.items() if not v["ok"]]
        self._stage(f"基线完成：{len(out) - len(fails)} 通过 / {len(fails)} 失败" if fails
                    else f"基线完成：{len(out)} 条命令全部通过",
                    status="warn" if fails else "done", stage="沙箱")
        return out, notes

    # ------------------------------------------------------------------ 工具
    def _exec_tool(self, name: str, args: Dict, reader: RepoReader, sandbox: Sandbox,
                   commands: List[Dict], rnd: int) -> List[str]:
        if name.startswith("repo_"):
            self._stage(f"查仓库：{_tool_label(name, args)}")
            result = reader.call(name, args)
        elif name in ("check_run", "run_check", "sandbox_run"):
            cmd = str(args.get("cmd") or args.get("command") or "")
            want = str(args.get("name") or "").strip()
            if not cmd and want:
                cmd = next((c["cmd"] for c in commands if c["name"] == want), "")
            if not cmd:
                result = "[错误] check_run 需要 cmd，例如 {\"cmd\": \"npx vitest run src/foo.spec.ts\"}"
            else:
                self._stage(f"沙箱执行：{cmd[:80]}")
                r = sandbox.run(cmd, name=want or "check", cwd_rel=str(args.get("cwd") or ""),
                                timeout=self.command_timeout)
                if r.denied:
                    result = f"[拒绝] {r.note}"
                else:
                    result = (f"$ {cmd}\n返回码 {r.returncode}"
                              f"（{r.seconds}s）\n" + _clip(r.output, 4000))
        elif name in ("repro_add", "repro_write", "add_repro"):
            result = self._add_repro(args, sandbox, rnd)
        else:
            result = ("[错误] 未知工具 " + repr(name) + "。可用：repo_list / repo_read / "
                      "repo_grep / repro_add / check_run / patch_try（直接输出 "
                      "SEARCH/REPLACE 块）/ give_up")
        self._tool_event(name, args, result)
        return [f"### 工具 {name} {_json_short(args)}（第 {rnd} 轮）\n"
                + _clip(result, MAX_FEEDBACK_CHARS)]

    # ------------------------------------------------------- 复现用例（红优先）
    def _add_repro(self, args: Dict, sandbox: Sandbox, rnd: int) -> str:
        """收下模型写的复现用例，立刻在未修复代码上跑一次：**必须先红**。

        这是整个复现环节的立身之本。一个在缺陷代码上就通过的文件，测的不是这条缺陷；
        放任它进后续的「红→绿」，得到的 `verified` 是假的——而且比没有更糟，因为它
        看起来像证据。
        """
        h = self.harness
        if h is None or not h.usable:
            return ("[错误] 这个仓库没有可用的复现手段（没探测到单测跑器，也没有可执行的"
                    "断言脚本环境）。跳过 repro_add，直接读代码 + 交补丁，并在最终说明里"
                    "写明「无法构造可执行复现」。")
        rel = str(args.get("path") or "").strip().replace("\\", "/")
        content = str(args.get("content") or "")
        ok, why = spec_ok(h, self.repo_root, rel)
        if not ok:
            return f"[错误] 复现用例落点不合法：{why}"
        if not content.strip():
            return "[错误] repro_add 需要 content（用例正文）。"
        self.repro["attempted"] = True
        self._stage(f"落复现用例到沙箱并跑一次（要求先红）：{rel[:60]}",
                    stage="复现用例", status="start")
        try:
            sandbox.write(rel, content)
        except Exception as e:
            return f"[错误] 复现用例写进沙箱失败：{e}"
        cmd = render_cmd(h, rel)
        r = sandbox.run(cmd, name=REPRO_CHECK_NAME, timeout=self.command_timeout)
        state = classify_run(h, r.returncode, r.output, denied=r.denied)
        self.repro["runs"].append({"round": rnd, "path": rel, "cmd": cmd,
                                   "state": state, "returncode": r.returncode,
                                   "output": _clip(r.output, 2500)})
        if r.denied:
            return (f"[复现命令被沙箱拒绝] {r.note}\n请换成仓库里能跑的测试命令写法"
                    "（例如直接用 npx <runner> run <文件>）。")
        if state == "red":
            self.repro.update({"status": "red", "path": rel, "content": content,
                               "cmd": cmd})
            return (f"复现成功：用例在未修复代码上就是**红的**（返回码 {r.returncode}），"
                    "它就是这条缺陷的判据。现在可以动手修了——之后每次交补丁我都会带上"
                    "这个用例一起跑，它转绿 + 验收命令不新增失败，才算 verified。\n"
                    f"报错原文：\n{_clip(r.output, 3000)}")
        if state == "broken":
            self.repro["rewrites"] = int(self.repro.get("rewrites", 0)) + 1
            left = max(0, self.repro_max_rewrite + 1 - self.repro["rewrites"])
            tail = ("" if left else
                    "复现环节的尝试次数已用尽：这一条不再要求可执行复现，"
                    "请直接进入修复，并在说明里写清「未能构造复现用例」。")
            return (f"[复现命令没跑起来，这不算红] 返回码 {r.returncode}\n"
                    f"{_clip(r.output, 2500)}\n{broken_hint(h, r.output)}"
                    f"（剩余尝试次数 {left}）{tail}")
        # green：用例在缺陷代码上就通过了 —— 硬闸门打回
        self.repro["rewrites"] = int(self.repro.get("rewrites", 0)) + 1
        if self.repro["rewrites"] > self.repro_max_rewrite:
            self.repro["status"] = "blocked"
            return ("[复现用例未能复现缺陷] 它在**未修复**的代码上就通过了，说明它测的不是"
                    "这条缺陷；已重写 " + str(self.repro_max_rewrite) + " 次仍如此。"
                    "复现环节到此为止：请照常读代码交补丁，但要在最终说明里明确"
                    "「未能构造出能复现该缺陷的用例，结论只有验收命令级证据」。")
        return ("[复现用例未复现缺陷，打回重写] 它在**未修复**的代码上就通过了（返回码 0）。"
                "一个现在就绿的用例不可能证明你把缺陷修好了。重写请**沿用同一个 path**"
                "（直接覆盖，别在沙箱里堆第二个用例，否则全量跑测试时会把废弃的那份一起收走）。"
                "断言必须落在「缺陷会让它失败」的那一点上（比如 undefined 访问、错误的字段、"
                "少了一层的可选链、错误的分支条件），而不是随便挑一段能跑通的代码。"
                f"（剩余重写次数 {max(0, self.repro_max_rewrite - self.repro['rewrites']) + 1}）")

    # ---------------------------------------------------------------- 补丁尝试
    def _try_patch(self, text: str, blocks: List, sandbox: Sandbox,
                   commands: List[Dict], baseline: Dict, rnd: int) -> Dict:
        attempt: Dict = {
            "round": rnd,
            "patch_text": text,
            "patch_canonical": render_block_prompt(blocks),
            "blocks": [{"file_path": b.file_path, "search": b.search,
                        "replace": b.replace, "match_mode": b.match_mode,
                        "match_start": b.match_start} for b in blocks],
            "files": [], "combined_diff": "", "checks": {}, "verification": {},
            "conclusion": "unbuilt", "rank": RANK_UNBUILT, "signature": "",
            "built": False, "errors": [], "seconds": 0.0,
            "summary": _clip(_plain(text), 600),
        }
        built = build_patch(str(sandbox.root), blocks)
        # 硬拦「改温度计说退烧」：一旦复现用例被立为判据，补丁里就不允许再动它。
        # 只在提示词里禁止是不够的——模型确实会为了拿到绿而改用例。
        repro_path = str(self.repro.get("path") or "")
        if self.repro.get("status") == "red" and repro_path:
            touched = [b.file_path for b in blocks
                       if (b.file_path or "").replace("\\", "/") == repro_path]
            if touched:
                attempt["errors"] = [
                    f"补丁试图修改复现用例 {repro_path}：它是这条缺陷的判据，改它等于改考卷"
                    "凑分数。请只修改业务代码；若确信用例本身写错了，先 give_up 说明理由。"]
                attempt["signature"] = "repro-touched"
                attempt["conclusion"] = "unbuilt"
                return attempt
        if not built.ok:
            attempt["errors"] = list(dict.fromkeys(built.errors))[:8]
            attempt["signature"] = "unbuilt:" + "|".join(sorted(attempt["errors"]))[:800]
            attempt["conclusion"] = "unbuilt"
            return attempt

        # 落进沙箱 → 跑验收 → 还原。还原是必须的：下一轮的 SEARCH 基准仍然是
        # 「用户当前工作区的内容」，不还原的话第二份补丁会打在第一份的残骸上。
        originals: Dict[str, str] = {c.file_path: c.original for c in built.changes if c.ok}
        start = time.time()
        for c in built.changes:
            if c.ok:
                sandbox.write(c.file_path, c.patched)
        attempt["files"] = built.files()
        attempt["combined_diff"] = _clip(built.combined_diff(), MAX_DIFF_CHARS)
        attempt["built"] = True

        checks, notes = self._run_commands(sandbox, commands, label=f"第 {rnd} 轮")
        attempt["notes"] = notes
        for rel, content in originals.items():
            try:
                sandbox.write(rel, content)
            except OSError:
                pass
        attempt["seconds"] = round(time.time() - start, 1)

        base_pass = {k: bool(v.get("ok")) for k, v in baseline.items()}
        if REPRO_CHECK_NAME in checks:
            # 复现用例不在基线里（写它的时候基线已经跑完了），但它的「红」是**确证过的**
            # ——不补这一笔，红→绿的折算会把它当成「基线就是绿的」，fixed 里永远不含它
            base_pass[REPRO_CHECK_NAME] = False
        has_baseline_fail = any(not v for v in base_pass.values())
        # 注意必须真的把 checks 塞回 attempt：漏了这一行时，反馈区会照着「没有可跑命令」
        # 的分支给模型发一句假提示，闸门也拿不到真实结果（集成用例 [11] 抓到的就是这个）。
        attempt["checks"] = checks
        if checks:
            passed = {k: bool(v["ok"]) for k, v in checks.items()}
            verdict = compare_with_baseline(base_pass, passed)
            concl = conclusion_of(verdict, has_baseline_fail)
            attempt["verification"] = verdict
            attempt["conclusion"] = concl
            attempt["rank"] = {CONCLUSION_VERIFIED: RANK_VERIFIED,
                               CONCLUSION_GREEN: RANK_GREEN,
                               "regression": RANK_FAIL,
                               "failing": RANK_FAIL}.get(concl, RANK_FAIL)
            attempt["signature"] = checks_signature(checks)
            repro_green = bool(passed.get(REPRO_CHECK_NAME))
            attempt["verified_via"] = self._via_of(repro_green, bool(checks))
            if concl in (CONCLUSION_VERIFIED, CONCLUSION_GREEN) and \
                    REPRO_CHECK_NAME in checks:
                self.repro["status"] = "green" if repro_green else "red"
                self.repro["green_round"] = rnd if repro_green else None
        else:
            attempt["conclusion"] = CONCLUSION_UNVERIFIED
            attempt["rank"] = RANK_UNVERIFIED
            attempt["signature"] = f"unverified:r{rnd}"
        return attempt

    def _via_of(self, repro_green: bool, has_checks: bool) -> str:
        """verified 到底靠什么验出来的：复现用例 >> 验收命令。"""
        strength = (self.harness.strength if self.harness else STRENGTH_NONE)
        if repro_green and strength == STRENGTH_TEST:
            return VIA_REPRO_TEST
        if repro_green and strength == STRENGTH_ASSERT:
            return VIA_REPRO_ASSERT
        return VIA_GATE if has_checks else ""

    # ------------------------------------------------------- 回喂给模型的反馈
    def _attempt_feedback(self, attempt: Dict, rnd: int) -> List[str]:
        lines = [f"## 系统（第 {rnd} 轮补丁的沙箱验证结果）",
                 "以下输出是补丁落到临时沙箱后**真实跑出来的**，不是模拟、不是我编的。"]
        if not attempt.get("built"):
            lines.append("**补丁未能构建，一个字都没落盘。**原因：")
            for e in attempt.get("errors", []):
                lines.append(f"- {e}")
            lines.append("修法：SEARCH 段必须与文件当前内容逐字一致（含缩进与空行）。"
                         "先用 repo_read 按行号读到那一段的原文，再照抄进 SEARCH。"
                         "别把 SEARCH 扩大到整文件（会被判定为整文件覆写而拒绝）。")
            if any("文件不存在" in e for e in attempt.get("errors", [])):
                lines.append("另外：**这条流水线不支持新建文件**。仓库里不存在的路径不能当"
                             "SEARCH 目标。确实需要新增文件时，在最终说明里写清路径与内容，"
                             "交给人工创建，别硬造。")
            return ["\n".join(lines)]
        for note in attempt.get("notes", []):
            lines.append(f"（提醒）{note}")
        checks = attempt.get("checks") or {}
        if not checks:
            lines.append("⚠ 这个仓库没有可跑的验收命令，本次改动**未经任何真实验证**。"
                         "只能靠读代码保证正确性，请在最终说明里如实写明这一点。")
        for name, item in checks.items():
            mark = "✓ 通过" if item["ok"] else "✗ 失败"
            lines.append(f"### {name} `{item.get('cmd', '')}` → {mark}"
                         f"（{item.get('seconds', 0)}s）")
            if not item["ok"]:
                lines.append("```\n" + _clip(item.get("output", ""), 3500) + "\n```")
        verdict = attempt.get("verification") or {}
        if verdict:
            if verdict.get("fixed"):
                lines.append("相比基线转绿：" + ", ".join(verdict["fixed"]))
            if verdict.get("regressions"):
                lines.append("⚠ **相比基线新弄坏了**：" + ", ".join(verdict["regressions"])
                             + "。这比缺陷没修好更严重，先把它修回去。")
            if verdict.get("still_failing"):
                lines.append("仍然失败：" + ", ".join(verdict["still_failing"]))
        if REPRO_CHECK_NAME in checks:
            rp = self.repro
            if rp.get("status") == "green":
                lines.append(f"✅ 复现用例 {rp.get('path')} 已转绿 —— 这条缺陷的现象"
                             "在可执行判据上消失了。")
            else:
                lines.append(f"⚠ 复现用例 {rp.get('path')} 仍然是红的。它是这条缺陷的判据："
                             "别的命令全绿也不算修好。看上面的报错改你的补丁，"
                             "**不要改用例来凑绿**（那是把温度计砸了说退烧了）。")
        lines.append(f"本次改动的 diff：\n```diff\n{attempt.get('combined_diff', '')}\n```")
        if attempt.get("conclusion") in (CONCLUSION_VERIFIED, CONCLUSION_GREEN):
            lines.append("验证通过。请给出最终说明（根因 / 为什么这么改 / 验证结论 / "
                         "遗留风险与需人工确认的点），**不要再输出 SEARCH/REPLACE 块**。")
        elif attempt.get("conclusion") == CONCLUSION_UNVERIFIED:
            lines.append("请给出最终说明，并明确「未经验证」。不要再重复提交同一份补丁。")
        else:
            lines.append("还没通过。针对上面的报错改，改完再交一份补丁。")
        return ["\n".join(lines)]


# ------------------------------------------------------------------ 小工具
_BIN_CANDIDATES = (
    ("vue-tsc", "npx vue-tsc --noEmit"),
    ("tsc", "npx tsc --noEmit -p ."),
    ("eslint", "npx eslint . --ext .js,.jsx,.ts,.tsx,.vue"),
    ("vitest", "npx vitest run"),
    ("jest", "npx jest --ci"),
    ("mocha", "npx mocha"),
    ("playwright", "npx playwright test"),
)


def suggest_commands(repo_root: Path) -> List[str]:
    """仓库没有可识别的验收脚本时，去 node_modules/.bin 里看看有什么现成检查器。

    企业里的 lerna/pnpm 多包仓普遍没配 type-check/lint 脚本，但 tsc、eslint、vitest
    其实就躺在 .bin 里（云枢那几个前端仓就是这个状况）——差的只是「有人把它配成
    gate.commands」。与其让循环空转，不如把这个建议原样端给用户。
    """
    out: List[str] = []
    for bin_name, cmd in _BIN_CANDIDATES:
        if any((repo_root / "node_modules" / ".bin" / n).exists()
               for n in (bin_name, bin_name + ".cmd", bin_name + ".exe")):
            out.append(cmd)
    return out


class _Deadline:
    def __init__(self, seconds: float):
        self.end = time.time() + max(30.0, float(seconds or DEFAULT_DEADLINE_SECONDS))

    def left(self) -> float:
        return self.end - time.time()

    def expired(self) -> bool:
        return self.left() <= 0


def _fit_notes(context: List[str], budget: int) -> List[str]:
    """上下文超限时无损压缩：丢最早的「查仓库结果」，但种子与最近两次验证永远保留。"""
    out = list(context)
    total = sum(len(c) for c in out)
    i = 1                     # 0 是种子（工单 + 基线），不丢
    while total > budget and i < len(out) - 3:
        placeholder = "（该轮查仓库结果因上下文超限已省略）"
        total -= len(out[i]) - len(placeholder)
        out[i] = placeholder
        i += 1
    return out


def _auto_summary(conclusion: str, attempts: int) -> str:
    if conclusion == CONCLUSION_VERIFIED:
        return (f"沙箱验证通过：基线里失败的验收命令在打补丁后转绿，且无新增失败项。"
                f"共提交 {attempts} 次候选补丁。")
    if conclusion == CONCLUSION_GREEN:
        return (f"补丁在沙箱里跑过了所有验收命令且全绿（基线本来就全绿，说明改动没有破坏"
                f"构建，但缺陷本身是否修好仍需人工在页面上确认）。共提交 {attempts} 次。")
    if conclusion == CONCLUSION_STALLED:
        return "未能收敛，已认输转人工。最后一次真实报错见「尝试轨迹」。"
    if conclusion == CONCLUSION_BUDGET:
        return (f"轮次/时间预算用尽仍未跑绿（提交 {attempts} 次）。已保留表现最好的一次补丁"
                "供人工判断，请勿直接采纳。")
    if conclusion == CONCLUSION_NO_SANDBOX:
        return "沙箱不可用，未获得任何真实运行结果；本次结论与旧的一次成型链路等价。"
    if conclusion == CONCLUSION_UNVERIFIED:
        return "补丁已生成，但该仓库没有可跑的验收命令，未经真实验证。"
    return "模型未能给出可用的补丁（工单信息不足，或根因不在前端仓库）。"



def _json_short(obj: Dict) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)[:160]
    except Exception:
        return ""


def _plain(text: str) -> str:
    """剥掉 SEARCH/REPLACE 块，只留模型写的自然语言说明。"""
    return re.sub(r"<<<<<<< SEARCH.*?>>>>>>> REPLACE", "", text or "", flags=re.S).strip()


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return min(max(n, lo), hi)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
