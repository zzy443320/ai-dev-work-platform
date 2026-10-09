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
from .apicontract import (contract_tokens, corroborate, escape_hatches,
                          normalize_verdict, parse_citations, shape_guessing,
                          verify_citations)
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
# 后端归因是三档，不是一档：**有据的**后端问题、**没据的**后端问题、以及"前端先兼容等上游"。
# 把这三档分开，才谈得上统计与可信；混进 not_converged 就等于判对了也没有回报。
CONCLUSION_BACKEND = "backend_issue"
CONCLUSION_BACKEND_UNVERIFIED = "backend_issue_unverified"
CONCLUSION_ADAPTED = "adapted_pending_backend"
# 命令全绿，但沙箱里把页面跑起来一看，工单描述的现象还在 —— 这是最该单独标出来的一档，
# 否则「验收通过」会把一个没修好的补丁抬进可采纳状态
CONCLUSION_PERSISTS = "symptom_persists"
# 人点了「停止」：既不等于没修好，也不等于预算用尽。混进 budget_exhausted 会让人
# 以为模型尽力了，而实际是这一版根本没跑完——提案要标得清清楚楚。
CONCLUSION_CANCELLED = "cancelled"

# 结论 → 是否需要人工介入（写进提案，UI 直接读这个字段决定徽标颜色）
NEEDS_HUMAN = {CONCLUSION_STALLED, CONCLUSION_BUDGET, CONCLUSION_NO_PATCH,
               CONCLUSION_NO_SANDBOX, CONCLUSION_DISABLED, CONCLUSION_PERSISTS,
               CONCLUSION_CANCELLED,
               # 后端归因天然要人转交；"未验证"那档更需要人先看证据
               CONCLUSION_BACKEND, CONCLUSION_BACKEND_UNVERIFIED, CONCLUSION_ADAPTED}

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
9. ⚖ **前端不是唯一的责任方**。有些缺陷根因在后端接口、数据或权限；有些要等后端改完前端再适配。
   - 判「这是后端问题」是一等结论（`{"action":"verdict","kind":"backend_issue",…}`），
     **不是失败**：它会直接结束本次循环、把证据整理成给后端的交接说明。但必须带可核查证据：
     一次成功的 `api_probe`，或仓库里的 `路径:行号`（系统会真去读那一行，编造的会被戳穿）。
   - 如果"前端只能先做兼容、上游契约本该统一"，补丁照交，再补一条
     `{"action":"verdict","kind":"adapted_pending_backend","cleanup":"后端改好后该撤哪段"}`。
   - **禁止用容错代码代替查证**：一次新增 ≥3 个响应字段兼容读取（`list`/`rows`/`items` 全列进
     候选）会被直接拒绝，要求你先 `api_probe` 拿真实结构。把返回值"都接受一遍"不是修复，
     是把不确定性永久写进代码。
   - 同理，改请求参数名/URL 形状时，那个名字要在仓库里另有出处（DTO、typings、别处调用），
     不能凭语义推断。拿不到证据就别改契约——去问后端。
   - 用 `as any` / 交叉类型断言 / `@ts-ignore` 绕过 typings 不一致，等于把疑问藏起来：
     要么给出证据，要么改判后端问题。
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
        ("api_probe", "只读探一次接口拿真实返回结构（只允许配置的被测域名，只回字段名与类型，"
                      "不回数据）：{\"url\": \"https://…/api/…\"}"),
        ("check_run", "在沙箱里跑一条检查命令：{\"cmd\": \"npx vitest run src/x.spec.ts\"}"),
        ("patch_try", "提交候选补丁：直接输出 SEARCH/REPLACE 块，系统自动识别并真跑验证"),
        ("verdict", "归因结论（不是认输，是交付）：{\"action\":\"verdict\",\"kind\":"
                    "\"backend_issue|adapted_pending_backend\",\"endpoint\":\"…\","
                    "\"expected\":\"…\",\"actual\":\"…\",\"citations\":[\"路径:行号\"],"
                    "\"handoff\":\"给后端的话术\",\"cleanup\":\"后端修好后前端该撤什么\"}"),
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
    if action == "verdict":
        return {"verdict": True, "raw": obj}
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


def conclusion_of(verdict: Dict, has_baseline_fail: bool,
                  repro_flipped: bool = False) -> str:
    """一次验证的结论。返回 CONCLUSION_* 之一。

    原先只认「所有命令全绿」，这在**基线本来就红**的仓库里等于宣布 verified 永不可达：
    云枢这个仓 tsconfig 写了 `"types": ["jest"]` 但全仓没装 @types/jest，`tsc` 未打补丁
    就是 rc=2，补丁再对也不可能让它变绿。真正该问的是两件事——**有没有弄坏原本好的**、
    **这条缺陷自己的判据转绿了没有**。所以多给一条出路：无回归 + 复现判据从红转绿，
    也算 verified（verified_via 会如实标成 repro-*，基线仍红的项在提案里列出来）。
    """
    if verdict["regressions"]:
        return "regression"
    if verdict["all_green"]:
        return CONCLUSION_VERIFIED if has_baseline_fail else CONCLUSION_GREEN
    if repro_flipped:
        return CONCLUSION_VERIFIED
    return "failing"


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
                 emit: Optional[Callable[[Dict], None]] = None,
                 should_cancel: Optional[Callable[[], bool]] = None,
                 page_after_hook: Optional[Callable[..., Dict]] = None,
                 api_probe_fn: Optional[Callable[[str], Dict]] = None,
                 precedents: str = ""):
        """`page_after_hook(sandbox, budget_left) -> Dict`：补丁已落在沙箱、还没还原回
        原始内容的那一刻回调一次，由上层起 dev server 拍「修复后」的页面。

        只有命令级证据就说「修好了」是不诚实的：lint 全绿也可能页面照样白屏。所以给了
        钩子就只跑一次（第一次拿到绿时）；判读为「现象仍在」时这次绿不算收敛，继续改。
        `precedents` 是同类历史缺陷的先例文本（scripts/precedents.py 生成），只作参考。
        """
        self.repo_root = Path(repo_path).resolve()
        self.ai = ai
        self.page_after_hook = page_after_hook
        self.api_probe_fn = api_probe_fn
        self.precedents = str(precedents or "")
        # 契约护栏与归因都要读仓库核对引用，所以 reader 是实例状态而不是 run() 局部：
        # 只在 run() 里建会让任何绕过 run() 的调用（含单测）拿不到它。
        self.reader = RepoReader(str(self.repo_root), tools_enabled=True)
        self.probes: List[Dict] = []
        self.verdict: Dict = {}
        self._shots = 0
        self.page_after: Dict = {}
        self.cfg = dict(agent_cfg or {})
        # 页面复验最多跑几次：判读说「现象仍在」后，下一版的绿必须重新看一眼页面才算数
        self.shot_max = _int(self.cfg.get("sandbox_server_max_checks"), 2, 1, 5)
        self.gate_cfg = dict(gate_cfg or {})
        self.emit = emit
        # 取消谓词（一般是 web 层那份租约的 threading.Event.is_set）：
        # 只在轮次边界读它，绝不在模型调用/沙箱命令中途砍——那会留下半截沙箱。
        self.should_cancel = should_cancel
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
            "page_after": {},
            "verdict": {},
            "probes": [],
            "contract_flags": {},
            "verified_via": "",
            "rework_ref": {},
            "suggested_commands": [],
            "elapsed_seconds": 0.0,
            "started_at": _now(),
            "stall_counts": {},
        }
        # 复现用例的状态机：none → red（合格判据，开始修）→ green（修好了）
        #                    或 → blocked（写了几个用例都复现不出来，放弃复现环节）
        self.reader = RepoReader(str(self.repo_root), tools_enabled=True)
        self.probes: List[Dict] = []
        self.verdict: Dict = {}
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
        self._deadline = deadline
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
        verdict_tries = 0
        verdict_asked = False
        adapted = False

        try:
            for rnd in range(1, self.max_rounds + 1):
                result["rounds"] = rnd
                if _cancel_requested(self.should_cancel):
                    conclusion = CONCLUSION_CANCELLED
                    result["cancelled"] = True
                    result["notes"].append(
                        "收到停止请求，在第 " + str(rnd) + " 轮边界收口。"
                        "历史尝试与真实报错都保留在「修复轨迹」里，可以直接接着改。")
                    self._stage("已按请求停止本次修复循环", status="warn")
                    broke_early = True
                    break
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
                if action and action.get("verdict"):
                    verdict_tries += 1
                    fb = self._take_verdict(action["raw"])
                    context.append(f"### 系统（第 {rnd} 轮 · 归因结论已收到）\n{fb}")
                    v = self.verdict or {}
                    if v.get("kind") == "backend_issue":
                        if v.get("verified") or verdict_tries >= 2:
                            # 有据 → 正式归因后端；反复无据也给结论，但标成未验证，
                            # 让它可统计、可追溯，而不是假装"没修好"是模型能力问题
                            conclusion = (CONCLUSION_BACKEND if v.get("verified")
                                          else CONCLUSION_BACKEND_UNVERIFIED)
                            broke_early = True
                            break
                        continue
                    if v.get("kind") == "adapted_pending_backend":
                        # 兼容层已经交过并跑绿了（或马上会交），归因说清楚就可以收口
                        adapted = True
                        broke_early = True
                        break
                    continue
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
                        # 跑绿了就想收口——但这一版**动了接口契约**时，绿只说明"没改坏"，
                        # 说明不了"该前端改"。所以多给一轮，专门问它：这是真修法，还是
                        # 等上游改之前的兼容层？两种答案都有地方放，不会被挤进 give_up。
                        if (attempt.get("contract_surface") and not self.verdict
                                and not verdict_asked and rnd < self.max_rounds):
                            verdict_asked = True
                            context.append(
                                "### 系统（补丁已通过验证）\n命令与判据都绿了，可以收口。"
                                "但这一版动了接口契约面（请求字段名 / URL / 响应形状）。"
                                "请只回答一次：\n"
                                "- 若根因在上游、前端这段只是**权宜兼容** → "
                                '{"action":"verdict","kind":"adapted_pending_backend",'
                                '"endpoint":"…","expected":"…","actual":"…",'
                                '"handoff":"要后端改什么","cleanup":"后端改好后该撤哪段"}\n'
                                "- 若这就是正确的修法 → 直接给最终说明（不要再交补丁）。")
                            continue
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
        result["verdict"] = self.verdict or {}
        result["probes"] = [{k: v for k, v in pr.items()
                             if k in ("ok", "status", "url", "content_type",
                                    "shape", "error", "note", "query_param_names")}
                            for pr in self.probes[-4:]]
        result["rework_ref"] = dict(seed.get("rework") or {})
        if best:
            result["patch_text"] = best.get("patch_text", "")
            result["verified_via"] = best.get("verified_via", "")
            result["patch_canonical"] = best.get("patch_canonical", "")
            result["blocks"] = best.get("blocks", [])
            result["combined_diff"] = best.get("combined_diff", "")
            result["files"] = best.get("files", [])
            result["verification"] = best.get("verification", {})
            result["best_round"] = best.get("round")
            result["page_after"] = best.get("page_after") or self.page_after or {}
            result["contract_notes"] = best.get("contract_notes") or []
            # 命令全绿但页面复验说现象仍在：这条不算收敛，别让它冒充 verified
            if best.get("page_still_wrong"):
                conclusion = CONCLUSION_PERSISTS
                result["notes"].append(
                    "验收命令全绿，但沙箱里把页面跑起来后工单描述的现象仍然存在，"
                    "已按「未修好」处理（判读见修复轨迹）。")
            elif (best.get("page_after") or {}).get("verdict") == "gone":
                conclusion = CONCLUSION_VERIFIED
            pa = result["page_after"]
            if pa and pa.get("status") not in (None, "", "ok"):
                # 复验没做成也要在提案里说清楚：一路证据"缺席"和一路证据"通过"
                # 是两回事，别让读者以为页面已经看过了
                result["notes"].append(
                    f"沙箱页面复验未完成（{pa.get('status')}）："
                    f"{pa.get('reason') or pa.get('note') or '原因未记录'}；"
                    "本次结论只有命令级证据。")
            # 最终结论以「历史最好的一次」为准：模型最后一轮崩了/超预算，都不能把
            # 之前已经跑绿的补丁说成失败，反过来也不能把没跑绿的补成 verified。
            # 但 symptom_persists 是**比命令结果更高一级**的证据（页面复验），不许被降级
            # 前的结论覆盖回去。
            if (conclusion != CONCLUSION_PERSISTS
                    and best.get("conclusion") in (CONCLUSION_VERIFIED,
                                                   CONCLUSION_GREEN,
                                                   CONCLUSION_UNVERIFIED)):
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
        if adapted and conclusion in (CONCLUSION_VERIFIED, CONCLUSION_GREEN):
            # 「前端已兼容、等上游契约」优先级高于 verified：结论词要能告诉人这段该撤
            conclusion = CONCLUSION_ADAPTED
            result["notes"].append(
                "前端兼容层已写好并通过验证，但根因在上游契约："
                + str((self.verdict or {}).get("handoff") or "")[:200])
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
        if self.precedents.strip():
            lines.append(
                "## 同类历史缺陷的先例（**只作参考，不是标准答案**）\n"
                "仓库可能已经变过：先例里的路径、行号、补丁内容都必须你自己用工具"
                "重新确认一遍再用。历史结论标了「已采纳 / 被拒绝 / 未验证」，"
                "被拒绝的那类要避开它当时的改法。\n" + self.precedents)
        page = seed.get("page") or {}
        if page:
            lines.append("## 页面现场（Playwright 复刻工单操作后观察到的）")
            lines.append(f"- 路由：{page.get('route') or '-'}  状态：{page.get('status') or '-'}")
            if page.get("console_errors"):
                lines.append("- 控制台报错：\n" + _clip(
                    "\n".join(f"  · {e}" for e in page["console_errors"][:12]), 2500))
            if page.get("ai_verdict"):
                lines.append("- 视觉判读：" + _clip(str(page["ai_verdict"]), 1200))
        rw = seed.get("rework") or {}
        if rw:
            detail = str(rw.get("detail") or "").strip()
            rework_lines = [
                "## 人工返工反馈（**最高优先级判据**，高于工单原文）",
                f"- 上一版提案：{rw.get('proposal_id') or '—'}"
                + ("（当时已采纳后被撤销）" if rw.get("undone") else "（当时未采纳）"),
                f"- 人工判定：{rw.get('note') or '自测未通过'}",
            ]
            if detail:
                rework_lines.append(f"- 补充描述：{detail}")
            rework_lines += [
                "要求：",
                "1. 这次的「修好了」就等于**这条反馈描述的现象消失**。复现用例必须能把反馈里"
                "的现象跑红（先红再修）；做不到就在最终说明里写清卡在哪、需要什么信息，"
                "不要交一份只让命令变绿的补丁了事。",
                "2. 不许重演上一版的改法（它出现在下面的先例里）。要么改别的地方，"
                "要么拿仓库证据证明这次和上次不同在哪。",
                "3. 反馈与工单描述冲突时以反馈为准，并在说明里指出冲突点"
                "（可能是工单信息不准，也可能是上一版修错了文件）。",
            ]
            lines.append("\n".join(rework_lines))
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
        elif name in ("api_probe", "probe_api", "api_get"):
            result = self._api_probe(str(args.get("url") or args.get("path") or ""))
        elif name in ("repro_add", "repro_write", "add_repro"):
            result = self._add_repro(args, sandbox, rnd)
        else:
            result = ("[错误] 未知工具 " + repr(name) + "。可用：repo_list / repo_read / "
                      "repo_grep / repro_add / check_run / patch_try（直接输出 "
                      "SEARCH/REPLACE 块）/ give_up")
        self._tool_event(name, args, result)
        return [f"### 工具 {name} {_json_short(args)}（第 {rnd} 轮）\n"
                + _clip(result, MAX_FEEDBACK_CHARS)]

    # --------------------------------------------------------------- 契约护栏
    def _contract_guard(self, built, text: str, rnd: int) -> Dict:
        """拦住"拿容错代替查证"这两类动作，并要求可核查的证据。

        A 臂：改请求参数名 / URL 形状 —— 那个名字必须在仓库里**另有出处**（本次不改动的
            文件也用它）。没有就是模型自己起的字段名，正好是首批第 1 条的行为。
        B 臂：一次新增 ≥3 个响应字段读取（"list/rows/items 全都兼容一遍"）—— 必须有
            本次会话里成功的 `api_probe` 结构，或仓库内 `路径:行号` 引用（会真去读那行）。

        判定不过就当轮未构建返回，把话说清楚：要么补证据，要么改判 `backend_issue`。
        """
        changes = [{"file_path": c.file_path, "diff": c.diff()}
                   for c in built.changes if c.ok]
        touched = {str(c.get("file_path") or "") for c in changes}
        blocked: List[str] = []
        tokens: List[str] = []
        notes: List[str] = []

        ct = contract_tokens(changes)
        for name in ct["fields"]:
            proofs = corroborate(self.repo_root, name, touched)
            if not proofs:
                blocked.append(
                    f"补丁新增/改动了请求字段 `{name}`，但仓库里除本次改动的文件外"
                    f"没有任何地方使用它 —— 这说明字段名是推断出来的。")
            else:
                notes.append(f"请求字段 `{name}` 有旁证：{', '.join(proofs[:3])}")
        for guess in shape_guessing(changes):
            fields = guess.get("fields") or []
            probed = [f for f in fields if self._probed_has_field(f)]
            cited, bad = verify_citations(self.reader, parse_citations(text))
            if probed:
                notes.append(f"响应字段 {', '.join(probed)} 已由接口探测证实（{guess['file_path']}）")
                continue
            if cited:
                notes.append(f"响应形状改动引用了仓库证据："
                             + "、".join(c["ref"] for c in cited[:3])
                             + "；请人工确认这些引用确实支撑了这些字段")
                continue
            blocked.append(
                f"在 {guess.get('file_path')} 一次新增了 {len(fields)} 个响应字段兼容读取"
                f"（{'、'.join(fields[:6])}）——这是拿容错代码代替查证。"
                "请先用 `api_probe` 拿一次真实返回结构，或给出仓库内 `路径:行号` 证据；"
                "确实需要后端先改，就交 `{\"action\":\"verdict\",\"kind\":\"backend_issue\",…}`。")
        for hatch in escape_hatches(changes):
            notes.append(f"⚠ 新增行里有绕过类型系统的写法：{hatch}")
        if blocked:
            exits = ("两条出路：① 用 `api_probe` 拿一次真实返回结构，或给出仓库内"
                     " `路径:行号` 引用（DTO / typings / 别处调用）；② 这本来就该后端先改，"
                     '就交 {"action":"verdict","kind":"backend_issue",...} 把证据与交接话术写清楚。')
            return {"blocked": True, "tokens": tokens, "surface": bool(blocked),
                    "signature": "|".join(sorted(set(blocked)))[:700],
                    "messages": (blocked + notes + [exits])[:8]}
        return {"blocked": False, "notes": notes, "tokens": tokens,
                "surface": bool(ct["fields"] or ct["urls"]), "messages": []}

    def _probed_has_field(self, field: str) -> bool:
        key = "." + str(field)
        for probe in self.probes:
            for line in probe.get("shape") or []:
                head = line.split(":", 1)[0]
                if head == field or head.endswith(key):
                    return True
        return False

    # ----------------------------------------------------------- 接口真相探测
    def _api_probe(self, url: str) -> str:
        """只读探一次接口。回给模型的是**字段名与类型**，不含任何字段值。

        生产数据不进 prompt、不进提案、不进知识卡片 —— 判契约不匹配需要的是
        `data.list: array` 这种形状信息，不是列表里到底有什么。
        """
        if self.api_probe_fn is None:
            return ("[错误] 当前未启用接口探测（配置 agent.api_probe: off，或被测应用地址未填）。"
                    "改用仓库内 `路径:行号` 证据，或改判 backend_issue 并写清需要什么信息。")
        if not url:
            return '[错误] api_probe 需要 {"url": "https://…/api/…"}'
        self._stage(f"只读探测接口：{url[:90]}", stage="接口探测")
        try:
            info = self.api_probe_fn(url) or {}
        except Exception as e:
            info = {"ok": False, "error": f"{type(e).__name__}: {e}", "url": url}
        info.setdefault("url", url)
        self.probes.append(info)
        if not info.get("ok"):
            return ("[探测失败] " + json.dumps(
                {k: info.get(k) for k in ("status", "error", "note", "url") if k in info},
                ensure_ascii=False)[:900]
                + "\n这不代表没有缺陷，只代表拿不到证据：可以试别的接口地址，"
                  "或改判 backend_issue 并在 handoff 里写清需要什么信息。")
        shape = info.get("shape") or []
        return (f"HTTP {info.get('status')} {info.get('content_type')} "
                f"{info.get('url')}（{info.get('bytes')} 字节）\n"
                "返回结构（只有字段名与类型，取值已丢弃）：\n" + "\n".join(shape[:60]))

    # --------------------------------------------------------------- 归因结论
    def _take_verdict(self, raw: Dict) -> str:
        """收下模型的归因结论，并**核查它给的证据**——编造的引用会被戳穿。"""
        v = normalize_verdict(raw)
        if not v.get("ok"):
            return "[错误] " + v.get("error", "结论格式不对")
        cited, bad = verify_citations(self.reader, parse_citations(" ".join(
            [v.get("handoff", ""), v.get("expected", ""), v.get("actual", ""),
             " ".join(v.get("citations") or [])])))
        probed = [p for p in self.probes if p.get("ok")]
        v["citations_ok"] = [c["ref"] for c in cited]
        v["citations_bad"] = bad
        v["citation_snippets"] = {c["ref"]: c["snippet"] for c in cited}
        v["probes_ok"] = len(probed)
        if v["kind"] == "backend_issue":
            if not cited and not probed:
                v["verified"] = False
                self.verdict = v
                return ("[结论暂不采信] 你判的是「后端问题」，却没给任何可核查证据："
                        "既没有成功的 `api_probe`，也没有能在仓库里读到的 `路径:行号`。"
                        "补一条证据（探一次接口，或引用 DTO / typings / 另一处调用），"
                        "否则只能记为「未验证的后端归因」。")
            v["verified"] = True
        else:
            v["verified"] = True
        self.verdict = v
        return json.dumps(v, ensure_ascii=False)[:1500]

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
        if not built.ok:
            attempt["errors"] = list(dict.fromkeys(built.errors))[:8]
            attempt["signature"] = "unbuilt:" + "|".join(sorted(attempt["errors"]))[:800]
            attempt["conclusion"] = "unbuilt"
            return attempt
        # 契约护栏放在落盘之前：拦下来时一个字都不写，反馈里给的是"补证据或改判"，
        # 而不是"改了再说"。
        guard = self._contract_guard(built, text, rnd)
        attempt["contract_notes"] = guard.get("notes") or []
        attempt["contract_surface"] = bool(guard.get("surface"))
        if guard.get("blocked"):
            attempt["errors"] = guard["messages"][:6]
            attempt["signature"] = "contract:" + str(guard.get("signature"))[:700]
            attempt["conclusion"] = "unbuilt"
            attempt["contract_blocked"] = True
            return attempt
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

        # 页面级证据：只在**第一次拿到绿**的时候起一次 dev server 拍一次图。
        # 放在还原文件之前，是因为这一刻沙箱里正好是「打了补丁的代码」。
        pre_restore = (not attempt.get("verification")
                       or attempt.get("conclusion") in (CONCLUSION_VERIFIED,
                                                        CONCLUSION_GREEN))
        if (self.page_after_hook is not None and self._shots < self.shot_max
                and checks and pre_restore):
            self._shots += 1
            self._stage("在沙箱里起 dev server，看修复后的页面…", stage="页面复验")
            try:
                shot = self.page_after_hook(
                    sandbox, int(max(0, self._deadline.left()))) or {}
            except Exception as e:
                shot = {"status": "failed", "reason": f"页面复验异常：{e}"}
            attempt["page_after"] = shot
            self.page_after = shot
            verdict = str(shot.get("verdict") or "")
            self._stage(f"页面复验：{shot.get('status')} / 判读 {verdict or '无'}"
                        + (f"（{shot.get('reason')}）" if shot.get("reason") else ""),
                        status="done" if verdict == "gone" else "warn",
                        stage="页面复验")

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
            # 复现判据是否「从红转绿」：命令级基线永久红的仓库，只有这条路能给出 verified
            repro_flipped = REPRO_CHECK_NAME in verdict.get("fixed", [])
            concl = conclusion_of(verdict, has_baseline_fail, repro_flipped)
            attempt["verification"] = verdict
            attempt["conclusion"] = concl
            attempt["repro_flipped"] = repro_flipped
            if concl == CONCLUSION_VERIFIED and not verdict["all_green"]:
                attempt["baseline_still_red"] = [
                    n for n, ok in passed.items() if not ok]
            attempt["rank"] = {CONCLUSION_VERIFIED: RANK_VERIFIED,
                               CONCLUSION_GREEN: RANK_GREEN,
                               "regression": RANK_FAIL,
                               "failing": RANK_FAIL}.get(concl, RANK_FAIL)
            attempt["signature"] = checks_signature(checks)
            repro_green = bool(passed.get(REPRO_CHECK_NAME))
            attempt["verified_via"] = self._via_of(repro_green, bool(checks))
            page = attempt.get("page_after") or {}
            if page.get("verdict") == "same":
                # 命令全绿而现象仍在：这不是收敛。降级成失败，让模型继续改，
                # 而不是拿着一个「看起来通过了」的补丁去等人采纳。
                attempt["conclusion"] = CONCLUSION_PERSISTS
                attempt["rank"] = RANK_FAIL
                attempt["page_still_wrong"] = True
            elif not page and self.page_after.get("verdict") == "same"                     and concl in (CONCLUSION_VERIFIED, CONCLUSION_GREEN):
                # 上一版页面复验说过「现象仍在」，这一版没再复验（次数用尽/预算不足）：
                # 不许拿一次没看过的绿去覆盖看过的坏消息
                attempt["conclusion"] = CONCLUSION_PERSISTS
                attempt["rank"] = RANK_FAIL
                attempt["page_still_wrong"] = True
                attempt["page_after"] = dict(self.page_after, stale=True)
            elif page.get("verdict") == "gone" and concl in (CONCLUSION_VERIFIED,
                                                             CONCLUSION_GREEN):
                attempt["rank"] = RANK_VERIFIED + 1     # 页面级证据 > 只有命令绿
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
        if attempt.get("baseline_still_red"):
            lines.append("说明：这些命令**未打补丁时就是红的**（仓库自身的环境/配置问题，"
                         "不是你引入的），只要没新增失败项就不算回归；这条缺陷的判据是"
                         "复现用例，它已经转绿。不要为了把那些历史红项修绿而扩大改动。")
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
        page = attempt.get("page_after") or {}
        if page:
            v = str(page.get("verdict") or "")
            if v == "same":
                lines.append(
                    "⚠ **命令全绿，但把页面跑起来看，工单描述的现象仍然存在。**"
                    f"判读依据：{page.get('evidence') or page.get('reason') or '（无原文）'}\n"
                    "别再用「测试都过了」当理由：现象没消失就说明没修对。"
                    "回去看工单里的复现步骤与修复前截图，找出还差在哪，再交一版补丁。")
            elif v == "gone":
                lines.append("✅ 沙箱页面复验：修复后现象已消失（有截图与判读为证）。"
                             "可以收尾了，给最终说明。")
            elif page.get("status") in ("unusable", "failed", "skipped"):
                lines.append(
                    f"（页面复验没做成：{page.get('status')} — "
                    f"{page.get('reason') or '原因未记'}）。这一条只算命令级证据，"
                    "最终说明里必须写清「页面未复验」，别让读者以为看过了。")
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
    if conclusion == CONCLUSION_BACKEND:
        return ("判定为后端/接口/数据问题，且给出了可核查证据（接口探测或仓库内引用）。"
                "本次不产出前端补丁——证据与交接话术见「归因与证据」。")
    if conclusion == CONCLUSION_BACKEND_UNVERIFIED:
        return ("模型判为后端问题，但两次都拿不出可核查证据（没有成功的接口探测，"
                "也没有能在仓库里读到的引用）。这条**不能当定论**，需要人工核实归因。")
    if conclusion == CONCLUSION_ADAPTED:
        return ("前端兼容层已写好并通过验证，但根因在上游契约不一致：这段兼容是权宜，"
                "后端把结构统一后应当回收（撤销点见「归因与证据」）。")
    if conclusion == CONCLUSION_UNVERIFIED:
        return "补丁已生成，但该仓库没有可跑的验收命令，未经真实验证。"
    if conclusion == CONCLUSION_PERSISTS:
        return ("验收命令在沙箱里全绿，但把页面跑起来复验后，工单描述的现象仍然存在——"
                "按未修好处理。判读与截图见「修复轨迹 → 页面复验」。")
    if conclusion == CONCLUSION_CANCELLED:
        return (f"人工发起了停止，修复循环在轮次边界收口（此前提交 {attempts} 次补丁）。"
                "已跑过的真实报错与尝试都保留在「修复轨迹」里，重跑会带上它们继续。")
    return "模型未能给出可用的补丁（工单信息不足，或根因不在前端仓库）。"



def _json_short(obj: Dict) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)[:160]
    except Exception:
        return ""


def _plain(text: str) -> str:
    """剥掉 SEARCH/REPLACE 块，只留模型写的自然语言说明。"""
    return re.sub(r"<<<<<<< SEARCH.*?>>>>>>> REPLACE", "", text or "", flags=re.S).strip()


def _cancel_requested(should_cancel) -> bool:
    """读取消信号。None / 抛异常一律当「没被取消」——中断机制不能反过来搞挂循环。"""
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return min(max(n, lo), hi)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
