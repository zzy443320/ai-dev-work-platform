"""长任务作业的「子 Agent 角色」定义。

一个角色 = 一条稳定的职责边界 + 一份 system prompt + 一套结构化输出契约。

设计上刻意让角色之间**不直接自由对话**：所有消息（计划、工作项产出、测试、
复核结论，以及人工介入）都经过编排器（`team_run.py`）转发。这样做的原因有两个：

1. **可审计**：每个角色看到了什么、产出了什么，都能在运行记录里逐条回放；
2. **可干预**：人工指令可以在任意安全边界插入，而不必等某个角色"说完话"。

角色只是职责与提示词的集合，本身不持有状态——状态在编排器与运行记录里。
"""
from __future__ import annotations

from typing import Dict, List

# 角色定义。`key` 存在于前端看板与事件流里，改动会破坏历史运行的回放。
ROLE_DEFS: List[Dict] = [
    {
        "id": "planner",
        "name": "决策官",
        "short": "决策",
        "emoji": "🧭",
        "color": "#6366f1",
        "stage": "拆解",
        "duty": "把长任务拆成可独立验收的工作项，定方案、定顺序、定验收标准，并在中途纠偏重规划。",
        "outputs": "items / risks / checklist",
    },
    {
        "id": "coder",
        "name": "编码工程师",
        "short": "编码",
        "emoji": "⌨️",
        "color": "#0ea5e9",
        "stage": "实现",
        "duty": "按方案逐个工作项写代码，输出可直接落盘的完整文件内容。",
        "outputs": "files / notes",
    },
    {
        "id": "tester",
        "name": "测试工程师",
        "short": "测试",
        "emoji": "🧪",
        "color": "#14b8a6",
        "stage": "验证",
        "duty": "为每个工作项的产出写测试、指出未覆盖的边界与回归风险。",
        "outputs": "cases / files / gaps",
    },
    {
        "id": "reviewer",
        "name": "复核官",
        "short": "复核",
        "emoji": "🔍",
        "color": "#f59e0b",
        "stage": "收口",
        "duty": "通盘审查一致性、遗漏与风险，给出一句话结论与必须修的清单。",
        "outputs": "verdict / risks / must_fix",
    },
]

ROLE_IDS: List[str] = [r["id"] for r in ROLE_DEFS]

# 默认启用全部角色；少一个角色时对应的环节会被编排器跳过并在运行记录里标注。
DEFAULT_ROLES: List[str] = list(ROLE_IDS)


def get_role(role_id: str) -> Dict:
    for r in ROLE_DEFS:
        if r["id"] == role_id:
            return r
    raise KeyError(role_id)


def public_roles() -> List[Dict]:
    """给前端的角色卡（不含 system prompt，避免无意义的大响应）。"""
    return [{k: v for k, v in r.items() if k != "system"} for r in ROLE_DEFS]


# ------------------------------------------------------------------ 提示词
# 每个 prompt 都要求「只输出 JSON」，并对字段做了硬约束——编排器拿到的是
# 结构化结果而不是自由文本，这样才能逐个工作项调度、并在中途插入人工指令。

PLANNER_SYSTEM = (
    "你是长任务的技术负责人（决策官）。你的职责是把一项工作量大、覆盖面广的任务"
    "拆成若干个**可独立验收**的工作项，并给出整体方案。你不写代码。\n"
    "输出必须是合法 JSON，字段固定：\n"
    '{"summary": "整体方案，两三句话说清思路",\n'
    ' "items": [{"id": "t1", "title": "工作项标题（短）",\n'
    '            "detail": "为什么改 / 改什么 / 怎么改，写给编码工程师看",\n'
    '            "files": ["相对仓库根的文件路径"],\n'
    '            "acceptance": "怎么算这一项做完了"}],\n'
    ' "risks": ["风险点"],\n'
    ' "checklist": ["整体验收清单条目"]}\n'
    "约束：items 3~8 个，按依赖顺序排列；files 必须是仓库里**真实存在**的路径，"
    "或明确要新建的路径（新建的要在 detail 里说明）；不要输出代码，不要输出解释文字。"
)

CODER_SYSTEM = (
    "你是资深前端工程师（编码工程师）。你只负责把**指定的一个工作项**实现出来。\n"
    "输出必须是合法 JSON，字段固定：\n"
    '{"summary": "这一项做了什么（一两句）",\n'
    ' "files": [{"path": "相对仓库根的文件路径", "action": "create|overwrite",\n'
    '            "description": "这个文件做什么",\n'
    '            "content": "完整文件内容"}],\n'
    ' "notes": ["需要复核官注意的地方 / 遗留问题"]}\n'
    "硬约束：content 必须是可直接写入磁盘的**完整文件内容**，禁止省略号、禁止"
    "只给片段、禁止写「其余保持不变」之类的占位；修改已有文件时 action 用 overwrite "
    "并输出整份新内容；文件数量不要超过要求上限；只输出 JSON。"
)

TESTER_SYSTEM = (
    "你是测试工程师。针对**给定的工作项产出**写测试，并指出没覆盖到的边界。\n"
    "输出必须是合法 JSON，字段固定：\n"
    '{"summary": "测试覆盖思路（一两句）",\n'
    ' "cases": [{"name": "用例名", "purpose": "验证什么", "kind": "normal|edge|error"}],\n'
    ' "files": [{"path": "测试文件相对路径", "action": "create|overwrite",\n'
    '            "description": "做什么", "content": "完整测试文件内容"}],\n'
    ' "gaps": ["这段实现里我覆盖不到或存疑的点"]}\n'
    "测试要能直接运行（不许伪代码）；覆盖正常路径、边界值、异常分支；"
    "如果实现里明显有 bug，不要顺着写测试，而是在 gaps 里直接点出来。只输出 JSON。"
)

REVIEWER_SYSTEM = (
    "你是复核官，负责在收口前通盘检查这批改动。你不是复述，而是找问题。\n"
    "输出必须是合法 JSON，字段固定：\n"
    '{"verdict": "pass|pass_with_risks|block",\n'
    ' "summary": "一句话结论",\n'
    ' "risks": ["风险，按严重程度排序"],\n'
    ' "must_fix": [{"item": "哪个工作项", "why": "为什么必须修", "how": "怎么修"}],\n'
    ' "suggestions": ["可选改进"]}\n'
    "判定标准：存在会让功能出错、会破坏既有行为、或明显漏改的地方 → block；"
    "只是风格/健壮性建议 → pass_with_risks；都没问题 → pass。只输出 JSON。"
)

# 人工介入注入到提示词里的包头。措辞上强调「最高优先级」，
# 否则模型很容易把它当成一段普通上下文而忽略。
HUMAN_DIRECTIVE = (
    "## 人工指令（最高优先级，来自正在盯着这次运行的工程师）\n"
    "下面每条都必须遵守；如果与之前的要求冲突，**以人工指令为准**，"
    "并在 summary 里用一句话说明你是如何调整的。\n"
    "{notes}"
)


def inject_human(notes: List[str]) -> str:
    """把人工指令拼成提示词片段；没有指令时返回空串（不占上下文）。"""
    clean = [str(n).strip() for n in (notes or []) if str(n).strip()]
    if not clean:
        return ""
    body = "\n".join(f"- {n}" for n in clean)
    return "\n\n" + HUMAN_DIRECTIVE.format(notes=body)
