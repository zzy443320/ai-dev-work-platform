---
pattern_id: rec-28e7e43e
name: "查询条件不生效"
symptom: rec-28e7e43e
module: 
recurrence: 1
applied: 0
categories: 接口
defects: rec-4bf58854
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：查询条件不生效

> 复发 **1** 次 · 已采纳 0 例 · 分类：接口

## 这类缺陷长什么样
查询参数发出去了但结果不对：筛选、时间范围或分页条件在匹配时丢失或被忽略，表现为「选了条件结果恒为 0」或「条件怎么改都一样」。

- 命中症状词：不生效、筛选、恒为0、无数据

## 共性根因
- ⚠️ 该模式下**尚无已采纳案例**，以下判断均未经验证：
- ⚠️ 待验证（[rec-4bf58854](接口/rec-4bf58854.md)，invalid）：缺失的 packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/generic/presets/common.ts（createResourceLogFetchData / runtimeCommonFilterProperties / runtimeCommonColumns）才是条件拼装与字段映射的位置，本次未提供；

## 处理方式
### 当前最佳判断（未验证）

尚未有已采纳案例。参考置信度最高的一例 [rec-4bf58854](接口/rec-4bf58854.md)：

缺失的 packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/generic/presets/common.ts（createResourceLogFetchData / runtimeCommonFilterProperties / runtimeCommonColumns）才是条件拼装与字段映射的位置，本次未提供；

嫌疑文件：`packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/generic/presets/skill.ts`、`packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/generic/runtime-log-table.vue`、`packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/generic/runtime-log-page.vue`、`packages/<内部标识>-ai/ai-platform/components/ai-skill/skill-table-panel.vue`、`packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/generic/presets/common.ts`

### 预防措施（来自案例）

1) 补齐 presets/common.ts 到排查清单，保证查询参数拼装代码与预设文件同批提供；

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-4bf58854](接口/rec-4bf58854.md) | 接口 | invalid | 【迭代N】AI技能运行日志「触发方式」「触发用户」筛选不生效，选中任一条件结果恒为0条 | 缺失的 packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/gener… |

## 复发提示
- 首次出现，暂作模式种子保留。下次同类缺陷会自动归入本模式，复发次数随之累加——**同一个模式被反复命中，就说明该处缺护栏**。
