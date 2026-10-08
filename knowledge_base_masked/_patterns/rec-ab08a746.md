---
pattern_id: rec-ab08a746
name: "静默失败 / 无提示"
symptom: rec-ab08a746
module: 
recurrence: 3
applied: 1
categories: 逻辑
defects: rec-3ea831e7, rec-ba9a11f2, rec-b294bfae
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：静默失败 / 无提示

> 复发 **3** 次 · 已采纳 1 例 · 分类：逻辑

## 这类缺陷长什么样
失败路径不抛错、不提示，接口或表单仍返回成功，缺陷只在下游（数据没写进去、任务没触发）才暴露。排查时前端抓包「看起来一切正常」，最容易漏。

- 命中症状词：静默、无任何提示、无提示、直接关闭、导入0条

## 共性根因
- ✅ **已验证**（[rec-3ea831e7](逻辑/rec-3ea831e7.md)，已采纳）：packages/<内部标识>-ai/lui/components/import-dialog.vue 第 568-572 行的重命名校验被 this.shouldValidateRename(item.moduleType) 过滤，导致 AGENT 类型即使 policy 为 RENAME 也不会把 newCode 收集进 renamedResourceCodes 做非空/格式校验，空编…
- ⚠️ 待验证（[rec-ba9a11f2](逻辑/rec-ba9a11f2.md)，proposed）：核心回退逻辑位于未提供的 packages/<内部标识>-ai/lui/pages/schedule/schedule-repeat.vue 的「noRepeat」分支：该分支把日期控件置为 readOnly 并把时间在失焦（含浮层点选后失焦）时重置回默认 08:00，因此用户键入的 23:15 被丢弃；
- ⚠️ 待验证（[rec-b294bfae](逻辑/rec-b294bfae.md)，invalid）：提供的嫌疑文件（runtime-chat-updater.ts、typings/ai-runtime-log.ts、generic/presets/common.ts、ai-runtime-log/index.vue 的 1005-1084 窗口）均不包含移动端「个性化设置」提交链路，没有任何一处调用 /api/api/runtime/ai/memory/update 或 nickname/occ…
- 共性线索（≥2 例根因均提及）：<内部标识>、errcode
- 反复涉及的文件：`packages/<内部标识>-form/runtime/components/form-detail.ts`

## 处理方式
### 可复用改法（来自已采纳案例，可直接对照）

| 案例 | 状态 | 涉及文件 | 改法要点 |
| --- | --- | --- | --- |
| rec-3ea831e7 | applied | `packages/<内部标识>-ai/lui/components/import-dialog.vue`、`packages/cl… | packages/<内部标识>-ai/lui/components/import-dialog.vue 第 568-572 行的重命… |

### 预防措施（来自案例）

1）为冲突处理策略的“修改编码”选项补充强制输入框，并在提交前统一走同一套 newCode 校验函数；

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-3ea831e7](逻辑/rec-3ea831e7.md) | 逻辑 | applied | 【迭代N】个人助理导入：选择「修改编码」后静默导入0条且无提示（import_perso… | packages/<内部标识>-ai/lui/components/import-dialog.vue 第 568-572 行的重命… |
| [rec-ba9a11f2](逻辑/rec-ba9a11f2.md) | 逻辑 | proposed | 【迭代N】定时任务「不重复」模式时间被静默回退为08:00且日期只读，导致无法创建未来执… | 核心回退逻辑位于未提供的 packages/<内部标识>-ai/lui/pages/schedule/schedule-repeat… |
| [rec-b294bfae](逻辑/rec-b294bfae.md) | 逻辑 | invalid | 【迭代N】【LUI-多层级的记忆】移动端个性化设置：字段超过长度上限时提交静默失败，无任… | 提供的嫌疑文件（runtime-chat-updater.ts、typings/ai-runtime-log.ts、generic/pres… |

## 复发提示
- 同类缺陷已出现 **3 次**，这是**系统性缺陷**，不是个案：建议固化为评审 checklist 条目，并在该模块补一条单测或 lint 规则护栏。
- 复用入口：优先看 [rec-3ea831e7](逻辑/rec-3ea831e7.md) 的补丁，其次再看本模式的其它案例差异。
