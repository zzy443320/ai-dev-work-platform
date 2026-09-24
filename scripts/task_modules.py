"""The three frontend-dev task modules beyond defect fixing:

- reqdev   需求开发：Figma 设计稿 + 需求描述 → 前端代码
- apidebug 接口联调：接口文档 + 现有代码上下文 → 对接方案 / 调用代码 / mock 数据
- codetest 代码测试：目标源文件 → 单元测试代码

Every task goes through AIModel.complete (JSON contract) and returns a normalised
dict: {summary, plan, files: [{path, action, content, description}], checklist,
mock_data?, cases?, ai_mode, error?}. Mock mode returns clearly-labelled demo
output so the UI can be exercised offline.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from .mcp_client import MAX_TOOL_TEXT  # noqa: F401 —— 工具结果截断上限（曾漏 import 致 NameError）

MAX_FILE_CHARS = 20000
MAX_FILES = 8

TASK_LABELS = {
    "reqdev": "需求开发（设计稿转代码）",
    "apidebug": "接口联调",
    "codetest": "代码测试",
}


class TaskError(Exception):
    pass


# ----------------------------------------------------------------- run entry
def run_task(kind: str, ai, ctx: Dict, skills_text: str = "",
             tool_specs: Optional[List[Dict]] = None,
             tool_executor=None) -> Dict:
    """kind ∈ {reqdev, apidebug, codetest}; `ai` is an AIModel instance.

    skills_text: 团队技能/规范文本，追加到 system prompt（扩展能力-技能）。
    tool_specs/executor: MCP 工具清单与执行回调；非空时走「可调用工具」的多轮
    循环，模型可先取数据再产出最终 JSON。"""
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
                         tool_executor, max_rounds: int = 4) -> Dict:
    """Multi-round loop: model may call MCP tools before producing the final
    task JSON. Without tools this degrades to a single ai.complete."""
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
        if not res.get("files"):
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
    }
    out["files"] = _files(result.get("files"))
    if not out["error"] and not out["files"]:
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


def _files(v) -> List[Dict]:
    out: List[Dict] = []
    for f in (v or [])[:MAX_FILES] if isinstance(v, list) else []:
        if not isinstance(f, dict):
            continue
        path = str(f.get("path") or f.get("file_path") or "").strip().replace("\\", "/")
        content = str(f.get("content") or "")
        if not path or not content.strip():
            continue
        out.append({
            "path": path.lstrip("/"),
            "action": "overwrite" if str(f.get("action", "create")).lower() == "overwrite"
                      else "create",
            "description": str(f.get("description") or ""),
            "content": content[:MAX_FILE_CHARS],
        })
    return out


# -------------------------------------------------------------- mock output
def mock_task_result(kind: str, ctx: Dict) -> Dict:
    """Offline demo output, clearly labelled mock."""
    label = TASK_LABELS.get(kind, kind)
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


def _mock(kind, label, *, summary, plan, files, checklist, cases=None, mock_data=""):
    return {
        "task": kind, "label": label, "ai_mode": "mock", "error": "",
        "summary": summary, "plan": plan,
        "files": [{"path": f["path"], "action": "create", "description": f["description"],
                   "content": f["content"]} for f in files],
        "checklist": checklist, "endpoints": [], "mock_data": mock_data,
        "cases": cases or [], "raw": "",
    }
