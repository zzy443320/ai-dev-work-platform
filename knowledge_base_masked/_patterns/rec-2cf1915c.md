---
pattern_id: rec-2cf1915c
name: "未归类（待人工归类） · "docs"
symptom: unclassified
module: "docs
recurrence: 2
applied: 1
categories: UI, 逻辑
defects: rec-8d55ce50, rec-125bc07b
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：未归类（待人工归类） · "docs

> 复发 **2** 次 · 已采纳 1 例 · 涉及模块 `"docs` · 分类：UI、逻辑

## 这类缺陷长什么样
现有词表没覆盖到这类症状，暂存此处；补充 _SYMPTOM_FAMILIES 后会被重新归族。

## 共性根因
- ✅ **已验证**（[rec-8d55ce50](逻辑/rec-8d55ce50.md)，已采纳）：FormAiBizObjectSkillConditions 的 parseConditionGroups 将后端持久化数据按二维数组（Condition[][]）解析，但实际持久化结构为按组存放的 { conditions: [...] } 对象数组。
- ⚠️ 待验证（[rec-125bc07b](UI/rec-125bc07b.md)，gate_failed）：packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts 中 agentIconUrl getter 直接将 (this.agentData as any)?
- 反复涉及的文件：`packages/<内部标识>-ai/lui/components/workspace-header.vue`、`"docs/AI-Platform-v2.1.0\347\211\210\346\234\254\350\257\264\346\230\216.md"`

## 处理方式
### 可复用改法（来自已采纳案例，可直接对照）

| 案例 | 状态 | 涉及文件 | 改法要点 |
| --- | --- | --- | --- |
| rec-8d55ce50 | applied | `packages/<内部标识>-ai/condition-group-handlers/form-ai-biz-object-sk… | FormAiBizObjectSkillConditions 的 parseConditionGroups 将后端持久化数据按二维数组（Co… |

### 预防措施（来自案例）

在条件组处理器中增加单元测试，覆盖持久化格式的解析与序列化往返；

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-8d55ce50](逻辑/rec-8d55ce50.md) | 逻辑 | applied | 【迭代N】AI智能提交规则，条件组模式配置条件并保存后，条件组内容丢失。 | FormAiBizObjectSkillConditions 的 parseConditionGroups 将后端持久化数据按二维数组（Co… |
| [rec-125bc07b](UI/rec-125bc07b.md) | UI | gate_failed | 【迭代N】添加数据洞察协作助理，图标破碎 | packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin… |

## 复发提示
- 同类缺陷已出现 **2 次**，建议在评审 checklist 中补一条对应检查项。
- 复用入口：优先看 [rec-8d55ce50](逻辑/rec-8d55ce50.md) 的补丁，其次再看本模式的其它案例差异。
