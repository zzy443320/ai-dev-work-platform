"""The frontend-dev task modules beyond defect fixing:

- req      需求开发：需求原文 → 变更类型判定 + 影响面 + 实施计划（要设计稿时给设计说明）
- reqdev   Figma 转代码：Figma 设计稿 + 需求描述 → 前端代码
- apidebug 接口联调：接口文档 + 现有代码上下文 → 对接方案 / 调用代码 / mock 数据
- codetest 代码测试：目标源文件 → 单元测试代码

Every task goes through AIModel.complete (JSON contract) and returns a normalised
dict: {summary, plan, files: [{path, action, content, description}], checklist,
mock_data?, cases?, ai_mode, error?}. Mock mode returns clearly-labelled demo
output so the UI can be exercised offline.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from .mcp_client import MAX_TOOL_TEXT
from .analyzer import _GREP_EXCLUDES  # 全仓 grep 的排除口径只维护一份  # noqa: F401 —— 工具结果截断上限（曾漏 import 致 NameError）

MAX_FILE_CHARS = 20000
MAX_FILES = 8
# 单个文件里允许带多少条 find/replace（局部改动的粒度上限）
MAX_EDITS = 20
# 影响面最多列多少个文件（再多就不是「影响面」而是「全仓」了）
MAX_IMPACTS = 40

TASK_LABELS = {
    "req": "需求开发",
    "reqdev": "Figma 转代码",
    "apidebug": "接口联调",
    "codetest": "代码测试",
}


class TaskError(Exception):
    pass


# ----------------------------------------------------------------- run entry
def run_task(kind: str, ai, ctx: Dict, skills_text: str = "",
             tool_specs: Optional[List[Dict]] = None,
             tool_executor=None, progress=None) -> Dict:
    """kind ∈ {req, reqdev, apidebug, codetest}; `ai` is an AIModel instance.

    skills_text: 团队技能/规范文本，追加到 system prompt（扩展能力-技能）。
    tool_specs/executor: MCP 工具清单与执行回调；非空时走「可调用工具」的多轮
    循环，模型可先取数据再产出最终 JSON。
    progress: 仅需求开发用，`progress(阶段名, 细节)` 把进度镜像到运行租约。"""
    if kind == "req":
        return run_req(ai, ctx, skills_text, tool_specs, tool_executor, progress=progress)
    if kind == "reqdev":
        return run_reqdev(ai, ctx, skills_text, tool_specs, tool_executor)
    if kind == "apidebug":
        return run_apidebug(ai, ctx, skills_text, tool_specs, tool_executor)
    if kind == "codetest":
        return run_codetest(ai, ctx, skills_text, tool_specs, tool_executor)
    raise TaskError(f"未知任务类型: {kind}")


TOOL_LOOP_RULES = (
    "\n\n## 工具调用规则\n"
    "你可以调用上方列出的工具来获取真实信息（最多 <<max_rounds>> 轮）。\n"
    "需要取数据时只输出这个 JSON：{\"action\": \"call\", \"tool\": \"工具名\", "
    "\"arguments\": {参数}}\n"
    "工具结果会以 JSON 回给你；信息足够后，输出最终答案——必须去掉 action 字段，"
    "直接输出任务要求的完整 JSON（summary/plan/files/...）。不要编造工具返回的内容。"
)


def _complete_with_tools(ai, system: str, prompt: str,
                         tool_specs: Optional[List[Dict]],
                         tool_executor, max_rounds: int = 4,
                         require_files: bool = True) -> Dict:
    """Multi-round loop: model may call MCP tools before producing the final
    task JSON. Without tools this degrades to a single ai.complete.

    require_files=False 用于「中间阶段」（分诊 / 定位 / 复核）：这些阶段的产出
    本来就没有 files 字段，不能按最终交付的同一条规则去催模型补文件。
    """
    # 推理模型会拿 hidden reasoning tokens 抢 max_tokens 配额，给默认 4000 时
    # content 字段可能整个缺失（finish_reason=length）；统一留足余量。
    budget = 16000
    if not tool_specs or tool_executor is None:
        return ai.complete(prompt, system, max_tokens=budget)

    tool_block = "## 可用 MCP 工具\n" + json.dumps(
        [{"name": t["name"], "description": (t.get("description") or "")[:160],
          "parameters": t.get("inputSchema") or {"type": "object"}} for t in tool_specs],
        ensure_ascii=False, indent=1)
    rules = TOOL_LOOP_RULES.replace("<<max_rounds>>", str(max_rounds))
    history: List[str] = []
    for _ in range(max_rounds):
        convo = prompt + "\n\n" + tool_block + rules
        if history:
            convo += "\n\n## 历史工具调用记录\n" + "\n".join(history)
        res = ai.complete(convo, system, max_tokens=budget)
        if res.get("error") and not res.get("files"):
            return res
        if res.get("action") == "call":
            tool_name = str(res.get("tool") or "")
            args = res.get("arguments") if isinstance(res.get("arguments"), dict) else {}
            spec = next((t for t in tool_specs if t["name"] == tool_name), None)
            if spec is None:
                history.append(json.dumps({
                    "call": tool_name,
                    "result": f"[错误] 工具 {tool_name!r} 不在可用列表里，只能用列出的工具",
                }, ensure_ascii=False))
                continue
            try:
                result_text = tool_executor(tool_name, args)
            except Exception as e:
                result_text = f"[工具执行异常] {e}"
            history.append(json.dumps({
                "call": tool_name, "arguments": args,
                "result": str(result_text)[:MAX_TOOL_TEXT],
            }, ensure_ascii=False))
            continue
        # 没有 action 字段 = 最终答案
        res.pop("action", None)
        if require_files and not res.get("files"):
            # 模型只给了文字说明：给一次补全机会
            retry = ai.complete(
                convo + ("\n\n## 历史工具调用记录\n" + "\n".join(history) if history else "")
                + "\n\n## 上一次输出缺少 files 字段\n"
                "请重新输出最终 JSON：files 必须存在，且每项包含 path/description/"
                "content（content 为可直接写入磁盘的完整文件内容）。",
                system, max_tokens=budget)
            retry.pop("action", None)
            if retry.get("files"):
                return retry
        return res
    # 轮次用尽：最后强制要求直接给最终 JSON
    final = ai.complete(
        prompt + "\n\n" + tool_block
        + "\n\n## 历史工具调用记录\n" + "\n".join(history)
        + "\n\n## 最后要求\n不要再调用任何工具，立即输出任务要求的最终 JSON。",
        system, max_tokens=budget)
    final.pop("action", None)
    return final


def _with_skills(system: str, skills_text: str) -> str:
    if skills_text.strip():
        return (system + "\n\n## 团队技能与规范（必须遵守）\n"
                + skills_text.strip()[:4000])
    return system


# --------------------------------------------------------------------- req
#: 变更类型 → 中文标签。**口径写在服务端**：让模型自己编标签，同一种改法会时而
#: 叫「页面改造」时而叫「UI 调整」，界面上的分诊结论就没法一眼对比了。
#: unknown 是刻意保留的：模型漏字段时宁可显示「未判定」，也不要猜一个错类型。
CHANGE_TYPES = {
    "new_page": "新增页面",
    "page_change": "页面改造",
    "api_data": "仅接口 / 数据",
    "logic": "逻辑 / 重构",
    "mixed": "混合变更",
    "unknown": "未判定",
}
#: 这些类型可以不看设计稿直接实施；page_change 由模型按「视觉是否变化」判断
_DESIGN_TYPES = ("new_page",)

#: 交付方式（界面「交付方式」下拉）：
#:   auto   —— 能直接改就直接改，缺设计稿时只做非视觉部分（默认，也是「把需求做完」）
#:   triage —— 只出方案与影响面，不动代码（快速评估 / 需要先评审时用）
#:   code   —— 强制出代码：新页面的视觉部分也先给骨架（要求已经知道大致结构）
REQ_DELIVER = ("auto", "triage", "code")

#: 阶段顺序（前端阶段条按这个顺序渲染；key 改动要同步 ReqPane.vue）
REQ_STAGES = (
    ("triage", "需求分诊"),
    ("locate", "定位代码"),
    ("implement", "生成改动"),
    ("selfcheck", "自检"),
)


_REQ_TRIAGE_SYSTEM = (
    "你是资深前端技术负责人，负责把一段需求分诊成可执行的开发方案。"
    "第一步永远是判断「这次到底改什么」，而不是默认新建页面。只输出 JSON，字段：\n"
    '{"summary": "一句话结论：这次是什么改动、风险在哪", '
    '"change_type": "new_page|page_change|api_data|logic|mixed", '
    '"needs_design": true, '
    '"design_brief": "需要设计稿时写：要哪些页面/弹窗/状态、关键区域与组件、'
    '要设计给出什么（尺寸/交互/空态）；不需要时留空", '
    '"plan": "分步实施计划（多行文本）", '
    '"impacts": [{"path": "相对仓库根的真实文件路径", "reason": "为什么要动它", '
    '"change": "modify|create|delete"}], '
    '"endpoints": [{"method": "GET", "path": "/api/x", "note": "备注"}], '
    '"search_terms": ["用于在仓库里定位的代码关键词，3-8 个"], '
    '"acceptance": ["可验证的验收标准"], '
    '"checklist": ["需要向产品/后端确认的点", "联调/自检项"]}\n'
    "判定口径：\n"
    "- new_page：需要新建页面/路由 → 一般需要设计稿；\n"
    "- page_change：既有页面改交互或样式 → 有明显视觉改动才把 needs_design 置 true；\n"
    "- api_data：只是接口字段、数据映射、枚举/文案变化 → 不需要设计稿；\n"
    "- logic：纯逻辑、重构、性能、埋点 → 不需要设计稿；\n"
    "- mixed：以上多条并存，summary 里说清各占什么。\n"
    "search_terms 要具体到能 grep 到的标识符：组件名、接口路径、字段名、页面标题；"
    "不要用 page/data/view 这类泛词。\n"
    "只改接口字段或数据映射时，不要为了凑产出而新建页面文件。"
    "impacts 里的路径必须来自仓库索引或你确实读到的文件，不要猜目录。只输出 JSON。"
)

_REQ_LOCATE_SYSTEM = (
    "你是要把需求真正落到代码里的前端工程师。现在只做一件事：在真实仓库里找到"
    "必须改的文件，并给出**逐字来自文件**的改动锚点。只输出 JSON，字段：\n"
    '{"targets": [{"path": "相对仓库根的真实文件路径", "why": "为什么改它", '
    '"anchors": [{"find": "从文件里逐字复制的 2-6 行（含缩进）", '
    '"what": "这段代码负责什么"}]}], '
    '"new_files": [{"path": "要新建的文件路径", "why": "为什么新建"}], '
    '"verify": ["改完怎么验证：命令或人工检查项"], '
    '"gaps": ["需求里没写清、必须确认的信息"]}\n'
    "锚点规则（最容易被拒的地方）：\n"
    "1. find 必须原样复制自 repo_read 返回的内容，包括缩进与换行，不要补全、不要润色；\n"
    "2. 一个锚点只圈住要改的那几行 + 足够区分的上下文，保证在文件里只出现一次；\n"
    "3. 不要臆造路径。路径不存在时工具会给出相近的真实路径，照着改；\n"
    "4. 新文件放 new_files，不要混进 targets。\n"
    "先 grep 找入口，再 read 确认结构，最后才写锚点。信息不足时宁可留空 targets，"
    "把缺什么写进 gaps，也不要编造代码。只输出 JSON。"
)

_REQ_EDIT_SYSTEM = (
    "你是资深前端工程师，本次只允许改一个文件。输出 JSON："
    '{"path": "文件路径", "edits": [{"find": "原文片段", "replace": "替换后片段", '
    '"why": "这处改动的目的"}], "notes": "需要提醒的副作用"}\n'
    "硬性要求：\n"
    "1. find 必须**逐字**来自用户给出的现有代码（含缩进、标点、换行），"
    "至少带 2 行上下文以保证在文件中唯一；\n"
    "2. 只改必须改的片段，不要把整份文件重写进 replace；一次改动对应一处语义；\n"
    "3. 保持既有代码风格、导入方式、组件库用法，不引入新依赖；\n"
    "4. 不要在 JSON 外包 Markdown 代码围栏。\n"
    "如果确实没有可插入的锚点，把 edits 留空并在 notes 里说明。只输出 JSON。"
)

_REQ_NEWFILE_SYSTEM = (
    "你是资深前端工程师。按需求新写一个文件，输出 JSON："
    '{"path": "相对仓库根的路径", "content": "完整文件内容", "notes": "需要提醒的点"}\n'
    "content 必须是可直接写入磁盘的完整内容，不要 Markdown 围栏、不要省略号、"
    "不要写「其余同上」。沿用用户给出的技术栈与既有代码风格，不引入新依赖。只输出 JSON。"
)

_REQ_CHECK_SYSTEM = (
    "你是复核工程师。给你一段需求、分诊结论和已经生成的改动，判断这次到底做完没有。"
    "只输出 JSON："
    '{"verdict": "done|partial|blocked", "problems": ["没做到/做错的地方"], '
    '"followups": ["采纳后还要做什么"], "checklist": ["验收时要逐条确认的点"]}\n'
    "verdict 口径：done=覆盖验收标准且改动自洽；partial=部分完成或存在未处理分支；"
    "blocked=关键信息缺失、或改动根本落不了地。不要为了显得完成而忽略缺失项。只输出 JSON。"
)


def _as_bool(v, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "y", "1", "是", "需要"):
            return True
        if s in ("false", "no", "n", "0", "否", "不需要", ""):
            return False
    if isinstance(v, (int, float)):
        return bool(v)
    return default


def _repo_files(root: str) -> List[str]:
    """仓库内文本候选文件（相对路径）。与 scripts/chat.RepoReader 同口径：
    优先 git ls-files（自带 .gitignore 语义），失败退化成有限深度遍历。

    只在这里读一次：目录索引、候选文件筛选与「路径校验」共用同一份清单。
    """
    root = str(root or "").strip()
    if not root:
        return []
    base = Path(root)
    if not base.exists():
        return []
    try:
        r = subprocess.run(
            ["git", "-C", str(base), "ls-files", "--exclude-standard", "-c", "-o"],
            capture_output=True, timeout=90,
        )
        if r.returncode == 0:
            text = r.stdout.decode("utf-8", "replace")
            files = [l.strip().replace("\\", "/") for l in text.splitlines() if l.strip()]
            if files:
                return files[:20000]
    except Exception:
        pass
    out: List[str] = []
    skip = {"node_modules", ".git", "dist", "build", ".next", "coverage", "vendor"}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                out.append(str(p.relative_to(base)).replace("\\", "/"))
            except ValueError:
                continue
            if len(out) >= 20000:
                return out
    return out


def _repo_index(files: List[str], max_dirs: int = 90) -> str:
    """把文件清单压成「目录 · 文件数」的索引。

    为什么不直接贴全部路径：目标仓 8000+ 文件 / 10 万字符级，塞进提示词既贵又
    稀释需求原文。目录索引足够让模型圈定影响面，具体文件靠仓库工具去读。
    """
    counts: Dict[str, int] = {}
    for f in files:
        parts = [p for p in f.split("/") if p]
        if len(parts) <= 1:
            key = "(仓库根目录)"
        else:
            depth = 3 if len(parts) > 3 else len(parts) - 1
            key = "/".join(parts[:depth]) + "/"
        counts[key] = counts.get(key, 0) + 1
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_dirs]
    head = (f"仓库内共 {len(files)} 个文件、{len(counts)} 个目录（按文件数取前 {len(rows)} 个）：")
    return head + "\n" + "\n".join(f"{k}  {n}" for k, n in rows)


def _candidate_files(root: str, terms, limit: int = 80) -> List[str]:
    """用需求里的关键词**一次** grep 出候选文件清单。

    这一步在服务端做，比让模型逐词调 repo_grep 便宜一个数量级（一次 git 进程 vs
    每词一次全树扫描），而且能把模型的注意力直接放到 10~80 个文件上。
    多个 -e 一次扫完的写法与 analyzer._grep_hits 同源。
    """
    root = str(root or "").strip()
    kws = [str(t).strip() for t in (terms or [])
           if isinstance(t, (str, int, float)) and len(str(t).strip()) >= 2]
    kws = list(dict.fromkeys(kws))[:12]
    if not root or not kws:
        return []
    cmd = ["git", "-C", root, "grep", "-l", "-I", "-i", "-F", "--untracked"]
    for kw in sorted(kws, key=len, reverse=True):
        cmd += ["-e", kw]
    cmd += ["--", ".", *_GREP_EXCLUDES]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=90)
    except Exception:
        return []
    if r.returncode not in (0, 1):
        return []
    out = [l.strip().replace("\\", "/") for l in
           r.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    return list(dict.fromkeys(out))[:limit]


def _req_reader(repo_root: str):
    """需求开发的只读仓库视图（repo_grep / repo_read / repo_list）。

    「定位」阶段必须能读真实代码，否则模型只能按目录名猜路径，生成的补丁一定
    对不上锚点。工具与问答页共用 RepoReader，越界判定只有一份实现。
    """
    from .chat import RepoReader

    root = str(repo_root or "").strip()
    if not root or not Path(root).exists():
        return None
    return RepoReader(root, tools_enabled=True)


def _req_toolkit(reader, tool_specs, tool_executor):
    """仓库工具在前、MCP 工具在后。同名时仓库工具优先（它是内建且无副作用的）。"""
    specs: List[Dict] = []
    if reader is not None:
        specs.extend(reader.spec())
    specs.extend(tool_specs or [])
    if not specs:
        return [], None

    def run(name: str, args: Dict) -> str:
        if reader is not None and name in ("repo_list", "repo_read", "repo_grep"):
            return reader.call(name, args)
        if tool_executor is None:
            return f"[错误] 工具 {name!r} 未挂载"
        return tool_executor(name, args)

    return specs, run


def _safe_rel(p) -> str:
    raw = str(p or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/"):
        return ""
    parts = [seg for seg in raw.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        return ""
    if ":" in parts[0]:
        return ""
    return "/".join(parts)


def _req_verdict(result: Dict, repo_files: List[str]) -> Dict:
    """把模型的分诊字段收敛成界面能直接渲染的结论。

    路径校验是这里最值钱的一步：模型给的 impacts 经常「差一层目录」（工单/文档
    里的路径过时是老问题）。仓库里有同名文件且**只有一个**候选时直接改正并记
    `corrected_from`；多个候选或完全没有时保留原样、标 exists=False，界面会提示
    「仓库里没有这个文件」，而不是把假路径当结论展示。
    """
    ct = str(result.get("change_type") or "").strip().lower()
    if ct not in CHANGE_TYPES:
        ct = "unknown"
    impacts, corrected = _validate_impacts(result.get("impacts"), repo_files)
    needs = _as_bool(result.get("needs_design"), ct in _DESIGN_TYPES)
    if ct in _DESIGN_TYPES:
        needs = True          # 新增页面没有设计稿就没法开始，别让模型一句 false 抹掉
    return {
        "change_type": ct,
        "change_type_label": CHANGE_TYPES[ct],
        "needs_design": needs,
        "design_brief": str(result.get("design_brief") or "")[:4000],
        "impacts": impacts,
        "impacts_corrected": corrected,
        "repo_file_count": len(repo_files),
        "search_terms": _str_list(result.get("search_terms"))[:12],
        "acceptance": _str_list(result.get("acceptance"))[:20],
    }


def _validate_impacts(v, repo_files: List[str]):
    from collections import defaultdict

    by_base = defaultdict(list)
    for f in repo_files:
        by_base[f.rsplit("/", 1)[-1].lower()].append(f)
    known = {f.lower() for f in repo_files}
    out: List[Dict] = []
    corrected = 0
    for it in (v or [])[:MAX_IMPACTS] if isinstance(v, list) else []:
        if isinstance(it, str):
            it = {"path": it}
        if not isinstance(it, dict):
            continue
        raw = _safe_rel(it.get("path") or it.get("file"))
        if not raw:
            continue
        entry = {
            "path": raw,
            "reason": str(it.get("reason") or "")[:400],
            "change": str(it.get("change") or "modify").strip().lower(),
            "exists": raw.lower() in known,
        }
        if entry["change"] not in ("modify", "create", "delete"):
            entry["change"] = "modify"
        if not entry["exists"]:
            cands = by_base.get(raw.rsplit("/", 1)[-1].lower(), [])
            if len(cands) == 1:
                entry["corrected_from"] = raw
                entry["path"] = cands[0]
                entry["exists"] = True
                corrected += 1
        out.append(entry)
    return out, corrected


def _req_triage(ai, ctx: Dict, doc: str, skills_text: str, repo_files: List[str],
                tools, executor) -> Dict:
    parts = [f"## 需求原文\n{doc[:12000]}"]
    if (ctx.get("source") or "").strip():
        parts.append(f"## 需求来源\n{ctx['source'].strip()[:500]}")
    if (ctx.get("scope") or "").strip():
        parts.append(f"## 影响范围（用户圈定）\n{ctx['scope'].strip()[:500]}")
    parts.append(f"## 技术栈\n{ctx.get('framework', 'React + TypeScript')}")
    if (ctx.get("repo_context") or "").strip():
        parts.append(f"## 现有代码参考（保持风格一致）\n{ctx['repo_context'][:8000]}")
    if (ctx.get("notes") or "").strip():
        parts.append(f"## 其他约定\n{ctx['notes'][:2000]}")
    if repo_files:
        parts.append("## 仓库目录索引\n" + _repo_index(repo_files))
        parts.append("需要确认某个文件是否存在时，用 repo_grep / repo_list 查，不要猜。")
    else:
        parts.append(
            "## 仓库信息\n没有可用的仓库索引（未配置仓库或读取失败）：不要编造文件路径，"
            "impacts 只写你能从需求/参考代码里确定的部分。")
    parts.append(
        "## 输出要求\n"
        "1. 先判定 change_type，再决定要不要设计稿；2. 影响面按「文件 + 理由 + 改法」列；"
        "3. 给 search_terms（后续定位代码用）与可验证的 acceptance；"
        "4. checklist 写清联调点、边界情况、需要产品/后端确认的点。只输出 JSON。")
    return _complete_with_tools(ai, _with_skills(_REQ_TRIAGE_SYSTEM, skills_text),
                                "\n\n".join(parts), tools, executor, max_rounds=3,
                                require_files=False)


def _req_locate(ai, ctx: Dict, doc: str, tri: Dict, verdict: Dict,
                repo_files: List[str], candidates: List[str],
                tools, executor, skills_text: str) -> Dict:
    parts = [f"## 需求原文\n{doc[:9000]}"]
    parts.append("## 分诊结论\n" + json.dumps({
        "summary": tri.get("summary", ""),
        "change_type": verdict.get("change_type_label", ""),
        "needs_design": verdict.get("needs_design"),
        "plan": str(tri.get("plan") or "")[:2000],
        "impacts": verdict.get("impacts", []),
    }, ensure_ascii=False, indent=1))
    parts.append(f"## 技术栈\n{ctx.get('framework', 'React + TypeScript')}")
    if (ctx.get("scope") or "").strip():
        parts.append(f"## 用户圈定的范围\n{ctx['scope'].strip()[:500]}")
    if (ctx.get("notes") or "").strip():
        parts.append(f"## 约定（不许动的接口 / 埋点 / 兼容性）\n{ctx['notes'][:2000]}")
    if candidates:
        parts.append("## 关键词命中的候选文件（先用 repo_read 看，再决定要不要动）\n"
                     + "\n".join(f"- {f}" for f in candidates))
    if repo_files:
        parts.append("## 仓库目录索引\n" + _repo_index(repo_files))
    parts.append(
        "## 本轮要求\n"
        "用仓库工具确认真实文件与锚点，输出 JSON（targets / new_files / verify / gaps）。"
        "不要输出最终代码，只输出锚点。")
    res = _complete_with_tools(ai, _with_skills(_REQ_LOCATE_SYSTEM, skills_text),
                               "\n\n".join(parts), tools, executor, max_rounds=8,
                               require_files=False)
    return _req_normalize_locate(res, repo_files, verdict)


def _req_normalize_locate(res: Dict, repo_files: List[str], verdict: Dict) -> Dict:
    """校验定位结果：路径必须在仓库里（新文件除外），同名唯一时自动校正。"""
    from collections import defaultdict

    known = {f.lower(): f for f in repo_files}
    by_base = defaultdict(list)
    for f in repo_files:
        by_base[f.rsplit("/", 1)[-1].lower()].append(f)
    gaps = _str_list(res.get("gaps"))
    if res.get("error"):
        gaps.insert(0, f"定位阶段模型调用失败：{res['error']}")

    def fix(raw: str) -> str:
        rel = _safe_rel(raw)
        if not rel:
            return ""
        if rel.lower() in known:
            return known[rel.lower()]
        cands = by_base.get(rel.rsplit("/", 1)[-1].lower(), [])
        if len(cands) == 1:
            return cands[0]
        return ""

    targets: List[Dict] = []
    for t in (res.get("targets") or [])[:MAX_FILES] if isinstance(res.get("targets"), list) else []:
        if not isinstance(t, dict):
            continue
        raw = str(t.get("path") or "")
        path = fix(raw)
        if not path:
            gaps.append(f"定位到的文件在仓库里不存在：{raw or '(空路径)'}")
            continue
        anchors = []
        for a in (t.get("anchors") or [])[:12] if isinstance(t.get("anchors"), list) else []:
            if isinstance(a, dict) and str(a.get("find") or "").strip():
                anchors.append({"find": str(a["find"]),
                                "what": str(a.get("what") or "")[:200]})
        targets.append({"path": path, "why": str(t.get("why") or "")[:400],
                        "anchors": anchors, "action": "edit"})
        if len(targets) >= MAX_FILES:
            break

    new_files: List[Dict] = []
    for n in (res.get("new_files") or [])[:MAX_FILES] if isinstance(res.get("new_files"), list) else []:
        if not isinstance(n, dict):
            continue
        rel = _safe_rel(n.get("path"))
        if not rel:
            gaps.append(f"新文件路径不合法：{n.get('path')}")
            continue
        if rel.lower() in known:
            # 模型以为要新建、其实已存在 → 当改造处理，别把已有文件整份覆盖掉
            if all(t["path"] != known[rel.lower()] for t in targets):
                targets.append({"path": known[rel.lower()],
                                "why": str(n.get("why") or "已存在，改为在原文件上修改")[:400],
                                "anchors": [], "action": "edit"})
            continue
        new_files.append({"path": rel, "why": str(n.get("why") or "")[:400]})
        if len(targets) + len(new_files) >= MAX_FILES:
            break

    # 分诊给的 impacts 里「明确要改但定位阶段漏掉」的文件补进来：漏改比多列更危险
    seen = {t["path"] for t in targets}
    for it in verdict.get("impacts") or []:
        if it.get("change") == "delete" or not it.get("exists") or it["path"] in seen:
            continue
        if len(targets) + len(new_files) >= MAX_FILES:
            break
        targets.append({"path": it["path"], "why": it.get("reason", ""),
                        "anchors": [], "action": "edit"})
        seen.add(it["path"])

    return {
        "targets": targets,
        "new_files": new_files,
        "verify": _str_list(res.get("verify")),
        "gaps": list(dict.fromkeys(gaps))[:20],
        "error": str(res.get("error") or ""),
    }


def _req_file_context(content: str, anchors: List[Dict], limit: int = 14000) -> str:
    """给模型看「文件的相关部分」，不是无脑全文。

    大文件（几千行）整份塞进去会：① 烧掉大量 token（推理模型还会二次放大）；
    ② 让模型在无关代码里挑错锚点。有锚点时只给锚点前后 25 行，没有锚点才退化成
    头尾窗口，并明确告诉它「没看到的部分不要猜」。
    """
    lines = content.split("\n")
    total = len(lines)
    numbered_full = "\n".join(f"{i + 1}\t{ln}" for i, ln in enumerate(lines))
    if len(content) <= limit:
        return f"（文件共 {total} 行，以下为全文；行号仅供定位，find 里不要带行号）\n{numbered_full}"

    spans = []
    for a in anchors:
        find = str(a.get("find") or "")
        if not find:
            continue
        hit = content.find(find)
        if hit < 0:
            continue
        start = content.count("\n", 0, hit)
        end = start + find.count("\n") + 1
        spans.append((max(0, start - 25), min(total, end + 25)))
    if not spans:
        spans = [(0, min(total, 220)), (max(0, total - 40), total)]

    spans.sort()
    merged: List[List[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    out: List[str] = []
    used = 0
    for a, b in merged:
        block = "\n".join(f"{i + 1}\t{lines[i]}" for i in range(a, b))
        if used + len(block) > limit and out:
            out.append(f"…（第 {a + 1}-{b} 行因长度限制未展开）")
            break
        out.append(block)
        used += len(block)
    return (f"（文件共 {total} 行；下面只给你相关片段，没显示的部分不要猜，"
            f"需要更多就用 repo_read 指定行号）\n" + "\n…\n".join(out))


def _strip_fence(text: str) -> str:
    t = str(text or "").strip()
    if t.startswith("```"):
        lines = t.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines)
    return t.strip("\n")


def _line_stats(old: str, new: str) -> Dict[str, int]:
    import difflib

    a, b = old.split("\n"), new.split("\n")
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return {"added": added, "removed": removed}


def _req_edit_one(ai, path: str, content: str, anchors: List[Dict], why: str,
                  doc: str, framework: str, notes: str, skills_text: str):
    """给一个文件生成 edits，并在服务端**预演**（apply_edits）通过后才收下。

    为什么要预演：模型给的 find 只要多一个空格就匹配不上，等到采纳时才报错，
    用户看到的是「点了采纳却什么都没发生」。在这里先跑一遍真实匹配，失败就把
    具体原因（未匹配 / 多处命中）回灌给模型重试一次——这是「自己做不完」和
    「能把需求做完」的分界线。
    """
    from .patch_engine import apply_edits

    context = _req_file_context(content, anchors)
    anchor_note = ""
    if anchors:
        anchor_note = ("\n## 建议改动的锚点（来自定位阶段，可参考但要以现有代码为准）\n"
                       + "\n".join(f"- {a['find'][:400]}" for a in anchors[:6]))
    prompt = (f"## 需求\n{doc[:6000]}\n\n## 这个文件为什么改\n{why or '（定位阶段未说明）'}\n"
              f"## 技术栈\n{framework}\n\n## 现有代码\n{context}\n{anchor_note}")
    if notes.strip():
        prompt += f"\n## 约定\n{notes[:1500]}"
    system = _with_skills(_REQ_EDIT_SYSTEM, skills_text)

    last_err = ""
    for _attempt in (1, 2):
        convo = prompt
        if last_err:
            convo += ("\n\n## 上一次输出被服务端拒绝\n" + last_err +
                      "\n请重新给出 edits：find 必须逐字来自上面的现有代码，"
                      "并带足够上下文保证唯一。")
        res = ai.complete(convo, system, max_tokens=16000)
        if res.get("error"):
            last_err = f"模型调用失败：{res['error']}"
            continue
        edits = _edits(res.get("edits"))
        if not edits:
            last_err = "没有给出 edits 数组（或 find 为空被丢弃）"
            continue
        new_text, errs, warns = apply_edits(content, edits, path)
        if errs:
            last_err = "；".join(errs)
            continue
        if new_text == content:
            last_err = "edits 没有产生任何实际改动（find 与 replace 相同？）"
            continue
        return {
            "path": path,
            "action": "edit",
            "description": str(res.get("notes") or why or "")[:400],
            "content": "",
            "edits": edits,
            "stats": _line_stats(content, new_text),
            "match": "fuzzy" if warns else "exact",
        }, ""
    return None, last_err or "未能生成可用改动"


def _req_new_one(ai, path: str, why: str, doc: str, framework: str,
                 notes: str, skills_text: str):
    prompt = (f"## 需求\n{doc[:6000]}\n\n## 要新建的文件\n{path}\n"
              f"## 为什么需要它\n{why or '（定位阶段未说明）'}\n"
              f"## 技术栈\n{framework}\n")
    if notes.strip():
        prompt += f"\n## 约定\n{notes[:1500]}"
    res = ai.complete(prompt, _with_skills(_REQ_NEWFILE_SYSTEM, skills_text),
                      max_tokens=16000)
    if res.get("error"):
        return None, f"模型调用失败：{res['error']}"
    content = _strip_fence(res.get("content"))
    if not content.strip():
        return None, "没有给出文件内容"
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS]
    return {
        "path": path,
        "action": "create",
        "description": str(res.get("notes") or why or "")[:400],
        "content": content,
        "stats": {"added": content.count("\n") + 1, "removed": 0},
    }, ""


def _req_selfcheck(ai, doc: str, verdict: Dict, entries: List[Dict],
                   unresolved: List[Dict], gaps: List[str], skills_text: str) -> Dict:
    """自检：先看服务端事实（哪些补丁预演通过），再让模型判断需求覆盖度。"""
    if not entries:
        return {
            "verdict": "blocked",
            "problems": ["没有任何改动被生成（" + (gaps[0] if gaps else "定位阶段没有找到可改的文件") + "）"],
            "followups": [g for g in gaps[:6]],
            "checklist": [],
        }
    lines = []
    budget = 12000
    for e in entries:
        head = f"- {e['path']} ({e['action']}, +{e['stats']['added']}/-{e['stats']['removed']})"
        body = ""
        if e["action"] == "edit":
            body = "\n".join(f"  改：{str(ed.get('replace') or '')[:400]}" for ed in e["edits"][:6])
        else:
            body = "  新建文件，内容 " + str(len(e.get("content") or "")) + " 字符"
        block = head + "\n" + body
        if budget - len(block) < 0:
            lines.append("- …（其余文件因长度限制未展开）")
            break
        lines.append(block)
        budget -= len(block)
    prompt = (f"## 需求\n{doc[:7000]}\n\n## 分诊结论\n"
              + json.dumps({"change_type": verdict.get("change_type_label"),
                            "needs_design": verdict.get("needs_design")},
                           ensure_ascii=False)
              + f"\n\n## 验收标准\n" + "\n".join(f"- {a}" for a in verdict.get("acceptance") or [])
              + "\n\n## 已生成的改动\n" + "\n".join(lines))
    if unresolved:
        prompt += ("\n\n## 没改成功的文件（服务端预演失败）\n"
                   + "\n".join(f"- {u['path']}: {u['error']}" for u in unresolved[:8]))
    if gaps:
        prompt += "\n\n## 定位阶段记录的信息缺口\n" + "\n".join(f"- {g}" for g in gaps[:8])
    res = ai.complete(prompt, _with_skills(_REQ_CHECK_SYSTEM, skills_text), max_tokens=8000)
    if res.get("error"):
        return {"verdict": "partial", "problems": [f"复核调用失败：{res['error']}"],
                "followups": [], "checklist": []}
    v = str(res.get("verdict") or "").strip().lower()
    if v not in ("done", "partial", "blocked"):
        v = "partial"
    # 服务端事实优先：还有没改成功的文件，就不能自称 done
    if unresolved and v == "done":
        v = "partial"
    return {
        "verdict": v,
        "problems": _str_list(res.get("problems"))[:12],
        "followups": _str_list(res.get("followups"))[:12],
        "checklist": _str_list(res.get("checklist"))[:20],
    }


def _req_merge_checklist(tri: Dict, verdict: Dict, verify: List[str],
                         check: Dict, gaps: List[str]) -> List[str]:
    items: List[str] = []
    for src in (_str_list(tri.get("checklist")), verdict.get("acceptance") or [],
                verify, check.get("checklist") or [], gaps):
        for it in src:
            s = str(it).strip()
            if s and s not in items:
                items.append(s)
    return items[:40]


def run_req(ai, ctx: Dict, skills_text: str = "",
            tool_specs: Optional[List[Dict]] = None, tool_executor=None,
            progress=None) -> Dict:
    """需求开发：把一段需求**做完**，而不是只做分诊。

    四个阶段：
      triage    判类型 / 要不要设计稿 / 影响面 / 验收标准
      locate    在真实仓库里定位文件与锚点（repo_grep / repo_read / repo_list）
      implement 逐文件生成 find/replace 或新文件，服务端预演必须通过
      selfcheck 结合预演事实复核需求覆盖度，输出残留问题

    产出物照样只落 artifacts：人工采纳才写工作区（不 commit、不 push）。
    任何阶段失败都不会让整单变成「什么都没发生」——stage 状态、失败文件与原因
    都会如实带回界面。
    """
    doc = (ctx.get("requirement_doc") or "").strip()
    if not doc:
        raise TaskError("请先粘贴需求文档或需求描述，或上传需求文档（md / txt / pdf）")

    deliver = str(ctx.get("deliver") or "auto").strip().lower()
    if deliver not in REQ_DELIVER:
        deliver = "auto"
    framework = str(ctx.get("framework") or "React + TypeScript")
    notes = str(ctx.get("notes") or "")
    t0 = time.monotonic()
    stage_map: Dict[str, Dict] = {}

    def emit(key: str, status: str, detail: str = "") -> None:
        label = dict(REQ_STAGES).get(key, key)
        stage_map[key] = {"key": key, "label": label, "status": status,
                          "detail": str(detail)[:300],
                          "ms": int((time.monotonic() - t0) * 1000)}
        if progress:
            try:
                progress(label, detail)
            except Exception:
                pass

    repo_root = str(ctx.get("repo_root") or "").strip()
    repo_files = _repo_files(repo_root)
    reader = _req_reader(repo_root)
    tools, executor = _req_toolkit(reader, tool_specs, tool_executor)

    # ------------------------------------------------------------ 1. 分诊
    emit("triage", "running", "判定变更类型、是否需要设计稿")
    tri = _req_triage(ai, ctx, doc, skills_text, repo_files, tools, executor)
    verdict = _req_verdict(tri, repo_files)
    if tri.get("error") and not tri.get("change_type"):
        emit("triage", "failed", str(tri.get("error"))[:200])
        out = _normalize(tri, ai, "req")
        out.update(verdict)
        out.update({"stages": _req_stage_rows(stage_map), "deliver": deliver,
                    "gaps": [f"分诊调用失败：{tri.get('error')}"], "verify": [],
                    "selfcheck": {"verdict": "blocked", "problems": [str(tri.get("error"))],
                                  "followups": [], "checklist": []},
                    "unresolved": [], "targets": [], "new_files": []})
        return out
    emit("triage", "done",
         f"{verdict['change_type_label']} · {'需要设计稿' if verdict['needs_design'] else '不需要设计稿'}")

    targets: List[Dict] = []
    new_files: List[Dict] = []
    gaps: List[str] = []
    verify: List[str] = []
    entries: List[Dict] = []
    unresolved: List[Dict] = []
    check: Dict = {"verdict": "blocked", "problems": [], "followups": [], "checklist": []}

    if deliver == "triage":
        for k in ("locate", "implement", "selfcheck"):
            emit(k, "skipped", "只出方案，未改代码")
        gaps.append("当前是「只出方案」模式：确认方案后把交付方式改成「自动完成」再跑一次即可落地")
    else:
        # -------------------------------------------------------- 2. 定位
        emit("locate", "running", "在仓库里定位真实文件与锚点")
        candidates = _candidate_files(repo_root, verdict.get("search_terms") or [])
        loc = _req_locate(ai, ctx, doc, tri, verdict, repo_files, candidates,
                          tools, executor, skills_text)
        targets, new_files = loc["targets"], loc["new_files"]
        gaps, verify = loc["gaps"], loc["verify"]
        if targets or new_files:
            emit("locate", "done",
                 f"{len(targets)} 个改造文件" + (f" + {len(new_files)} 个新文件" if new_files else ""))
        else:
            emit("locate", "failed", gaps[0] if gaps else "没有定位到需要改的文件")

        # -------------------------------------------------------- 3. 实施
        if targets or new_files:
            total = len(targets) + len(new_files)
            emit("implement", "running", f"0/{total} 生成改动")
            done_n = 0
            root_path = Path(repo_root) if repo_root else None
            for t in targets:
                path = t["path"]
                try:
                    content = (root_path / path).read_text(encoding="utf-8", errors="replace") \
                        if root_path else ""
                except Exception as e:
                    unresolved.append({"path": path, "error": f"读取失败：{e}"})
                    done_n += 1
                    emit("implement", "running", f"{done_n}/{total} {path}")
                    continue
                entry, err = _req_edit_one(ai, path, content, t.get("anchors") or [],
                                           t.get("why") or "", doc, framework, notes,
                                           skills_text)
                if entry:
                    entries.append(entry)
                else:
                    unresolved.append({"path": path, "error": err})
                done_n += 1
                emit("implement", "running", f"{done_n}/{total} {path}")
            for n in new_files:
                entry, err = _req_new_one(ai, n["path"], n.get("why") or "", doc,
                                          framework, notes, skills_text)
                if entry:
                    entries.append(entry)
                else:
                    unresolved.append({"path": n["path"], "error": err})
                done_n += 1
                emit("implement", "running", f"{done_n}/{total} {n['path']}")
            ok_n = len(entries)
            emit("implement", "done" if ok_n else "failed",
                 f"{ok_n}/{total} 个文件改动已通过预演"
                 + (f"，{len(unresolved)} 个失败" if unresolved else ""))
        else:
            emit("implement", "skipped", "没有可改动的文件")
            emit("selfcheck", "skipped", "没有改动可复核")

        # -------------------------------------------------------- 4. 自检
        if entries:
            emit("selfcheck", "running", "预演补丁并复核需求覆盖度")
            check = _req_selfcheck(ai, doc, dict(verdict, acceptance=verdict.get("acceptance")),
                                   entries, unresolved, gaps, skills_text)
            label = {"done": "已完成", "partial": "部分完成", "blocked": "未完成"}[check["verdict"]]
            emit("selfcheck", "done", f"{label}" + (f" · {check['problems'][0]}" if check["problems"] else ""))

    # -------------------------------------------------------------- 组装
    files = entries
    plan_text = str(tri.get("plan") or "").strip()
    impl_lines = [f"- {e['path']}：{e['description'] or ('新增' if e['action'] == 'create' else '修改')}"
                  for e in files]
    impl_lines += [f"- {u['path']}：未改成功（{u['error']}）" for u in unresolved]
    if impl_lines:
        plan_text = (plan_text + "\n\n## 实施结果\n" + "\n".join(impl_lines)).strip()

    summary = str(tri.get("summary") or "").strip()
    if files:
        summary = (summary + f" 已生成 {len(files)} 个文件的可采纳改动"
                   + (f"，{len(unresolved)} 个没改成" if unresolved else "。")).strip()

    out = _normalize({"summary": summary, "plan": plan_text,
                      "checklist": _req_merge_checklist(tri, verdict, verify, check, gaps),
                      "endpoints": tri.get("endpoints")}, ai, "req")
    out["files"] = files
    out.update(verdict)
    out.update({
        "stages": _req_stage_rows(stage_map),
        "targets": targets,
        "new_files": new_files,
        "gaps": gaps,
        "verify": verify,
        "selfcheck": check,
        "unresolved": unresolved,
        "deliver": deliver,
        # 让人一眼看出「这次到底改没改」：前端据此切换「可采纳 / 仅方案」文案
        "changed": bool(files),
    })
    # 真失败（模型调用错误 / 一个文件都没落成）如实带出去，但不要用 error 掩盖
    # 「需要设计稿」这种正常结论——界面上 error 会渲染成红色失败。
    if not files and unresolved:
        out["error"] = (f"{len(unresolved)} 个文件的改动都没通过预演："
                        + "；".join(f"{u['path']}（{u['error'][:80]}）" for u in unresolved[:3]))
    return out


def _req_stage_rows(stage_map: Dict[str, Dict]) -> List[Dict]:
    rows = []
    for key, label in REQ_STAGES:
        rows.append(stage_map.get(key) or {
            "key": key, "label": label, "status": "pending", "detail": "", "ms": 0,
        })
    return rows


# ------------------------------------------------------------------ reqdev
_REQDEV_SYSTEM = (
    "你是资深前端工程师，负责把 Figma 设计稿和需求描述转成可落地的前端代码。"
    "严格按用户指定的技术栈与团队既有代码风格输出。只输出 JSON，字段：\n"
    '{"summary": "一句话说明实现思路", "plan": "分步实现计划（多行文本）", '
    '"files": [{"path": "相对仓库根的文件路径", "action": "create|overwrite", '
    '"description": "这个文件做什么", "content": "完整文件内容"}], '
    '"checklist": ["联调/自检清单条目", ...]}\n'
    "files 里每个 content 必须是可直接写入磁盘的完整文件内容，不要用省略号。"
    "最多 8 个文件。只输出 JSON。"
)


def run_reqdev(ai, ctx: Dict, skills_text: str = "",
               tool_specs: Optional[List[Dict]] = None, tool_executor=None) -> Dict:
    design = (ctx.get("design") or "").strip()
    if not design and not (ctx.get("requirement") or "").strip():
        raise TaskError("请先拉取设计稿或填写需求描述")
    prompt_parts = [
        f"## 需求描述\n{ctx.get('requirement', '').strip() or '（无，请根据设计稿实现）'}",
        f"## 技术栈\n{ctx.get('framework', 'React + TypeScript')}",
    ]
    if design:
        prompt_parts.append(f"## Figma 设计稿结构（缩进表示层级）\n{design[:12000]}")
    if (ctx.get("repo_context") or "").strip():
        prompt_parts.append(f"## 现有代码参考（保持风格一致）\n{ctx['repo_context'][:6000]}")
    if (ctx.get("notes") or "").strip():
        prompt_parts.append(f"## 其他约定\n{ctx['notes'][:2000]}")
    prompt_parts.append(
        "## 输出要求\n"
        "1. 组件拆分合理，状态与 Props 设计清晰；2. 样式按设计稿的间距/颜色/字号实现；"
        "3. 附带 checklist（联调点、边界情况、需要后端确认的点）。只输出 JSON。")
    result = _complete_with_tools(ai, _with_skills(_REQDEV_SYSTEM, skills_text),
                                  "\n\n".join(prompt_parts), tool_specs, tool_executor)
    return _normalize(result, ai, "reqdev")


# ----------------------------------------------------------------- apidebug
_APIDEBUG_SYSTEM = (
    "你是资深前端工程师，负责新接口的前端联调。根据接口文档和现有代码，产出对接方案。"
    "只输出 JSON，字段：\n"
    '{"summary": "一句话结论（接口能不能直接接，风险在哪）", '
    '"plan": "联调步骤（多行文本）", '
    '"files": [{"path": "相对仓库根的文件路径", "action": "create|overwrite", '
    '"description": "做什么", "content": "完整文件内容"}], '
    '"endpoints": [{"method": "GET", "path": "/api/x", "note": "备注"}], '
    '"mock_data": "可直接用的 mock JSON/JS 模块（文本）", '
    '"checklist": ["联调前需要确认的点", "参数/返回值边界情况", ...]}\n'
    "常见坑都要点出来：字段命名不一致、空值、分页、鉴权头、CORS、时间格式。只输出 JSON。"
)


def run_apidebug(ai, ctx: Dict, skills_text: str = "",
                 tool_specs: Optional[List[Dict]] = None, tool_executor=None) -> Dict:
    doc = (ctx.get("api_doc") or "").strip()
    if not doc:
        raise TaskError("请粘贴接口文档（Swagger JSON / Markdown / curl 示例均可）")
    prompt_parts = [
        f"## 接口文档\n{doc[:12000]}",
        f"## 技术栈\n{ctx.get('framework', 'React + TypeScript')}",
    ]
    if (ctx.get("base_url") or "").strip():
        prompt_parts.append(f"## 后端 Base URL\n{ctx['base_url'].strip()}")
    if (ctx.get("repo_context") or "").strip():
        prompt_parts.append(
            f"## 现有代码（请求封装/相关页面，输出要与它兼容）\n{ctx['repo_context'][:8000]}")
    if (ctx.get("notes") or "").strip():
        prompt_parts.append(f"## 其他约定\n{ctx['notes'][:2000]}")
    prompt_parts.append(
        "## 输出要求\n给出：对接步骤、需要新建/修改的文件（含请求封装、类型定义、"
        "调用代码）、mock 数据、联调 checklist。只输出 JSON。")
    result = _complete_with_tools(ai, _with_skills(_APIDEBUG_SYSTEM, skills_text),
                                  "\n\n".join(prompt_parts), tool_specs, tool_executor)
    return _normalize(result, ai, "apidebug")


# ----------------------------------------------------------------- codetest
_CODETEST_SYSTEM = (
    "你是测试工程师，为指定源文件编写高质量单元测试。只输出 JSON，字段：\n"
    '{"summary": "一句话说明测试覆盖思路", "plan": "测试计划（多行文本）", '
    '"files": [{"path": "测试文件路径（相对仓库根）", "action": "create|overwrite", '
    '"description": "做什么", "content": "完整测试文件内容"}], '
    '"cases": [{"name": "用例名", "purpose": "验证什么"}], '
    '"checklist": ["运行测试的命令", "可能跑不过的点", ...]}\n'
    "覆盖：正常路径、边界值、异常分支、异步/回调、mock 外部依赖。"
    "测试要能直接运行，不要用伪代码。只输出 JSON。"
)


def run_codetest(ai, ctx: Dict, skills_text: str = "",
                 tool_specs: Optional[List[Dict]] = None, tool_executor=None) -> Dict:
    src = (ctx.get("file_content") or "").strip()
    if not src:
        raise TaskError("目标文件为空或不存在")
    prompt_parts = [
        f"## 目标文件\n路径: {ctx.get('file_path', '')}\n```\n{src[:12000]}\n```",
        f"## 测试框架\n{ctx.get('framework', 'vitest')}",
    ]
    if (ctx.get("requirements") or "").strip():
        prompt_parts.append(f"## 重点关注\n{ctx['requirements'][:2000]}")
    if (ctx.get("related") or "").strip():
        prompt_parts.append(f"## 相关依赖文件\n{ctx['related'][:6000]}")
    prompt_parts.append(
        "## 输出要求\n输出可运行的测试文件（完整内容）与用例清单。只输出 JSON。")
    result = _complete_with_tools(ai, _with_skills(_CODETEST_SYSTEM, skills_text),
                                  "\n\n".join(prompt_parts), tool_specs, tool_executor)
    return _normalize(result, ai, "codetest")


# ---------------------------------------------------------------- normalize
def _normalize(result: Dict, ai, kind: str) -> Dict:
    out: Dict[str, object] = {
        "ai_mode": getattr(ai, "mode", ""),
        "task": kind,
        "label": TASK_LABELS.get(kind, kind),
        "error": result.get("error", ""),
        "summary": str(result.get("summary") or result.get("root_cause") or "")[:2000],
        "plan": str(result.get("plan") or result.get("explanation") or ""),
        "checklist": _str_list(result.get("checklist")),
        "endpoints": _endpoints(result.get("endpoints")),
        "mock_data": str(result.get("mock_data") or ""),
        "cases": _cases(result.get("cases")),
        "raw": str(result.get("raw") or "")[:2000],
        # 需求开发（req）的分诊字段；其它类型保持空值，前端按同一份结构读，
        # 不用为「有没有这些字段」分支。
        "change_type": "",
        "change_type_label": "",
        "needs_design": False,
        "design_brief": "",
        "impacts": [],
    }
    out["files"] = _files(result.get("files"))
    if not out["error"] and not out["files"] and kind != "req":
        # 需求开发的正常结论可能就是「不用改代码，只要设计稿」——不能当成失败
        out["error"] = "模型没有输出任何文件内容，只有文字说明（见摘要），可重试或换模型"
    return out


def _str_list(v) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()][:20]
    if isinstance(v, str) and v.strip():
        return [l.strip("-• ") for l in v.splitlines() if l.strip()][:20]
    return []


def _endpoints(v) -> List[Dict]:
    out = []
    for e in (v or [])[:20] if isinstance(v, list) else []:
        if isinstance(e, dict):
            out.append({"method": str(e.get("method", "GET")),
                        "path": str(e.get("path", "")), "note": str(e.get("note", ""))})
    return out


def _cases(v) -> List[Dict]:
    out = []
    for c in (v or [])[:40] if isinstance(v, list) else []:
        if isinstance(c, dict):
            out.append({"name": str(c.get("name", "")), "purpose": str(c.get("purpose", ""))})
        elif isinstance(c, str):
            out.append({"name": c, "purpose": ""})
    return out


def _edits(v) -> List[Dict]:
    """问答提案里的「局部改动」：[{"find": 原文片段, "replace": 新片段}]。

    为什么需要它：语言包/大组件动辄几千行，让模型在回答里吐出「改完后的完整文件」
    既不现实（输出预算不够，会截断成半个文件）也不安全（截断的内容被采纳就写坏了）。
    find/replace 只描述改动点，采纳时由服务端对真实文件做匹配替换——匹配不上或多处
    命中都会整单拒绝，不会写坏文件。
    """
    out: List[Dict] = []
    for e in (v or [])[:MAX_EDITS] if isinstance(v, list) else []:
        if not isinstance(e, dict):
            continue
        find = e.get("find")
        if find is None:
            find = e.get("search")
        repl = e.get("replace")
        if repl is None:
            repl = e.get("new")
        find = str(find or "")
        if not find.strip():
            continue  # 空 find 等于无条件插入，直接丢
        out.append({
            "find": find[:MAX_FILE_CHARS],
            "replace": str(repl or "")[:MAX_FILE_CHARS],
            "description": str(e.get("description") or ""),
        })
    return out


def _files(v) -> List[Dict]:
    out: List[Dict] = []
    for f in (v or [])[:MAX_FILES] if isinstance(v, list) else []:
        if not isinstance(f, dict):
            continue
        path = str(f.get("path") or f.get("file_path") or "").strip().replace("\\", "/")
        content = str(f.get("content") or "")
        act = str(f.get("action", "create") or "create").strip().lower()
        edits = _edits(f.get("edits"))
        # 显式 edit / 只给了 edits 都算局部改动；局部改动不需要 content
        if act in ("edit", "patch", "replace") or (edits and not content.strip()):
            if not path or not edits:
                continue
            out.append({
                "path": path.lstrip("/"),
                "action": "edit",
                "description": str(f.get("description") or ""),
                "content": "",
                "edits": edits,
            })
            continue
        if not path or not content.strip():
            continue
        out.append({
            "path": path.lstrip("/"),
            "action": "overwrite" if act == "overwrite" else "create",
            "description": str(f.get("description") or ""),
            "content": content[:MAX_FILE_CHARS],
        })
    return out


# -------------------------------------------------------------- mock output
def mock_task_result(kind: str, ctx: Dict) -> Dict:
    """Offline demo output, clearly labelled mock."""
    label = TASK_LABELS.get(kind, kind)
    if kind == "req":
        # Mock 演示的是「把需求做完」的完整形态：分诊 → 定位 → 改动 → 自检，
        # 有可采纳文件、有阶段结果、有自检结论。只演示分诊会让界面用例覆盖不到
        # 流水线的任何一个真实分支。
        return _mock(
            kind, label,
            summary=("（Mock 演示）新增页面：需要设计稿；非视觉部分（接口返回类型）"
                     "已先落地，1 个可采纳改动通过预演，自检判定「部分完成」。"),
            plan=("1. 找到订单请求封装\n2. 给返回类型补 statusText\n"
                  "3. 预演补丁并自检\n4. 等设计稿后再实现页面视觉\n（Mock 演示内容）"),
            files=[{
                "path": "src/api/order.ts",
                "action": "edit",
                "description": "（Mock 演示）列表返回补 statusText 字段",
                "edits": [{"find": "export interface OrderItem {\n  id: string;",
                           "replace": "export interface OrderItem {\n  id: string;\n  statusText: string;"}],
                "stats": {"added": 1, "removed": 0},
            }],
            checklist=["确认后端已下发 statusText", "空列表渲染兜底"],
            endpoints=[{"method": "GET", "path": "/api/order/list",
                        "note": "（Mock 演示）列表数据来源"}],
            change_type="new_page", change_type_label=CHANGE_TYPES["new_page"],
            needs_design=True,
            design_brief=("（Mock 演示）需要一张列表页设计稿：含顶部筛选、表格、"
                          "空态与加载态；请给出 1440 / 1280 两档间距与颜色标注。"),
            impacts=[
                {"path": "src/api/order.ts", "change": "modify",
                 "reason": "（Mock 演示）订单返回类型", "exists": True},
                {"path": "src/views/Order/List.tsx", "change": "modify",
                 "reason": "（Mock 演示）列表展示新字段", "exists": False},
            ],
            stages=[
                {"key": "triage", "label": "需求分诊", "status": "done",
                 "detail": "新增页面 · 需要设计稿", "ms": 420},
                {"key": "locate", "label": "定位代码", "status": "done",
                 "detail": "1 个改造文件", "ms": 1600},
                {"key": "implement", "label": "生成改动", "status": "done",
                 "detail": "1/1 个文件改动已通过预演", "ms": 2400},
                {"key": "selfcheck", "label": "自检", "status": "done",
                 "detail": "部分完成 · 页面视觉等设计稿", "ms": 3100},
            ],
            selfcheck={"verdict": "partial",
                       "problems": ["（Mock 演示）页面视觉部分未实现：等设计稿"],
                       "followups": ["（Mock 演示）采纳后跑一次 type-check"],
                       "checklist": ["旧数据没有 statusText 时显示兜底文案"]},
            verify=["npm run type-check"],
            gaps=["（Mock 演示）页面视觉需等设计稿后才能实现"],
            unresolved=[], changed=True, deliver="auto",
        )
    if kind == "reqdev":
        comp = (ctx.get("requirement") or "新需求").strip()[:20] or "NewFeature"
        name = re.sub(r"[^A-Za-z0-9]", "", re.sub(r"\s+", "-", comp)) or "NewFeature"
        return _mock(kind, label,
                     summary="（Mock 演示）按设计稿拆出一个展示组件 + 一个容器组件。",
                     plan="1. 拆组件\n2. 实现样式\n3. 接入数据\n（Mock 演示内容）",
                     files=[{
                         "path": f"src/components/{name}/{name}.tsx",
                         "description": "（Mock 演示）展示组件",
                         "content": f"// MOCK 演示内容 —— 配置真实大模型后会生成完整代码\n"
                                    f"export default function {name}() {{\n  return <div>{comp}</div>;\n}}\n",
                     }],
                     checklist=["与设计稿比对间距/字号", "移动端断点自检"])
    if kind == "apidebug":
        return _mock(kind, label,
                     summary="（Mock 演示）接口可直接对接，注意分页字段与空值。",
                     plan="1. 封装请求\n2. 定义类型\n3. 页面接入\n（Mock 演示内容）",
                     files=[{
                         "path": "src/api/newApi.ts",
                         "description": "（Mock 演示）接口封装",
                         "content": "// MOCK 演示内容\nexport async function fetchList(params: any) {\n  return request.get('/api/list', { params });\n}\n",
                     }],
                     mock_data='{"code": 0, "data": {"list": [], "total": 0}}',
                     checklist=["确认鉴权头由网关注入", "空列表渲染兜底"])
    return _mock(kind, label,
                 summary="（Mock 演示）覆盖正常路径 + 空值 + 异常分支。",
                 plan="1. 正常渲染\n2. 空数据\n3. 请求失败\n（Mock 演示内容）",
                 files=[{
                     "path": "src/__tests__/demo.test.ts",
                     "description": "（Mock 演示）单元测试",
                     "content": "// MOCK 演示内容\nimport { describe, it, expect } from 'vitest';\n\ndescribe('demo', () => {\n  it('works', () => { expect(true).toBe(true); });\n});\n",
                 }],
                 cases=[{"name": "works", "purpose": "（Mock 演示）基础渲染"}],
                 checklist=["npm run test:unit"])


def _mock(kind, label, *, summary, plan, files, checklist, cases=None, mock_data="",
          **extra):
    out = {
        "task": kind, "label": label, "ai_mode": "mock", "error": "",
        "summary": summary, "plan": plan,
        "files": [dict({"path": f["path"], "action": f.get("action", "create"),
                        "description": f["description"], "content": f.get("content", "")},
                       **({"edits": f["edits"]} if f.get("edits") else {}),
                       **({"stats": f["stats"]} if f.get("stats") else {}))
                  for f in files],
        "checklist": checklist, "endpoints": [], "mock_data": mock_data,
        "cases": cases or [], "raw": "",
        # 与 _normalize 保持同一份字段；req 之外的模块用默认值
        "change_type": "", "change_type_label": "", "needs_design": False,
        "design_brief": "", "impacts": [],
    }
    out.update(extra)
    return out
