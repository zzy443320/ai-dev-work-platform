---
pattern_id: rec-76cbe851
name: "校验缺失 / 非法值可提交 · schedule"
symptom: rec-e086be65
module: packages/<内部标识>-ai/lui/pages/schedule
recurrence: 2
applied: 1
categories: 逻辑
defects: rec-a99725d6, rec-ed594697
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：校验缺失 / 非法值可提交 · schedule

> 复发 **2** 次 · 已采纳 1 例 · 涉及模块 `packages/<内部标识>-ai/lui/pages/schedule` · 分类：逻辑

## 这类缺陷长什么样
提交前校验不完整：必填项、互斥条件或长度上限没拦住，数据以非法状态落库，往往到下游使用（无法触发、展示为空）才炸。

- 命中症状词：必填、校验

## 共性根因
- ✅ **已验证**（[rec-a99725d6](逻辑/rec-a99725d6.md)，已采纳）：packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue 中「重复周期」表单项（模板中 `<a-form-model-item :label="$t('languages.aiPlatform.interval')">`）没有绑定 prop、rules 中也没有对应规则，空重复周期可提交；
- ⚠️ 待验证（[rec-ed594697](逻辑/rec-ed594697.md)，proposed）：两个日程编辑器的提交可用性判断 canSubmit 只校验了 name、instruction 以及「不重复」时的 date/time，没有校验 repeat.scheduleType 是否存在，导致重复周期为空也能提交；
- 共性线索（≥2 例根因均提及）：<内部标识>、editor、endAt、form、pages、schedule
- 反复涉及的文件：`packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue`、`packages/<内部标识>-ai/lui/mobile/pages/schedule/schedule-editor.vue`

## 处理方式
### 可复用改法（来自已采纳案例，可直接对照）

| 案例 | 状态 | 涉及文件 | 改法要点 |
| --- | --- | --- | --- |
| rec-a99725d6 | applied | `packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue`、`packa… | packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue 中「重复周期」表… |

### 预防措施（来自案例）

新增/调整定时任务表单字段时，凡产品定义为必填的项必须同时具备 prop 绑定 + rules 规则，并在自测用例中覆盖「留空提交」与「条件必填（勾选后不填）」两类场景；

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-a99725d6](逻辑/rec-a99725d6.md) | 逻辑 | applied | 【迭代N】重复周期应该必填，终止时间勾选了也应该必填。 | packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue 中「重复周期」表… |
| [rec-ed594697](逻辑/rec-ed594697.md) | 逻辑 | proposed | 【流式验证】重复周期应该必填 | 两个日程编辑器的提交可用性判断 canSubmit 只校验了 name、instruction 以及「不重复」时的 date/time，没有… |

## 复发提示
- 同类缺陷已出现 **2 次**，建议在评审 checklist 中补一条对应检查项。
- 复用入口：优先看 [rec-a99725d6](逻辑/rec-a99725d6.md) 的补丁，其次再看本模式的其它案例差异。
