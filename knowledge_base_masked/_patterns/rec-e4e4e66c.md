---
pattern_id: rec-e4e4e66c
name: "时间口径不一致"
symptom: rec-e4e4e66c
module: 
recurrence: 1
applied: 0
categories: 接口
defects: rec-3b72f546
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：时间口径不一致

> 复发 **1** 次 · 已采纳 0 例 · 分类：接口

## 这类缺陷长什么样
时间在前端展示或提交时经过了错误的时区 / 格式转换，与真实时间不一致。

- 命中症状词：早8小时、时区、utc、本地时间

## 共性根因
- ⚠️ 该模式下**尚无已采纳案例**，以下判断均未经验证：
- ⚠️ 待验证（[rec-3b72f546](接口/rec-3b72f546.md)，invalid）：提供的嫌疑文件中没有任何一个属于 AI 运行日志（admin/#/ai-skill-runtime-log）的列表或详情渲染代码——lui/pages/schedule/*、lui/mobile/pages/schedule/*、packages/<内部标识>-utils/src/full-calendar.ts 内既没有日志字段（startTime/endTime/executeTime…

## 处理方式
### 当前最佳判断（未验证）

尚未有已采纳案例。参考置信度最高的一例 [rec-3b72f546](接口/rec-3b72f546.md)：

提供的嫌疑文件中没有任何一个属于 AI 运行日志（admin/#/ai-skill-runtime-log）的列表或详情渲染代码——lui/pages/schedule/*、lui/mobile/pages/schedule/*、packages/<内部标识>-utils/src/full-calendar.ts 内既没有日志字段（startTime/endTime/executeTime）的格式化代码，也没有出现给无时区的时间串加 Z 的逻辑，因此无法在这批文件里定位到产生 -8 小时的具体行。

嫌疑文件：`packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue`、`packages/<内部标识>-ai/lui/mobile/pages/schedule/schedule-editor.vue`、`packages/<内部标识>-ai/lui/pages/schedule/api.ts`、`packages/<内部标识>-utils/src/full-calendar.ts`、`packages/<内部标识>-locale/admin/system/system.zh-CN.ts`

### 预防措施（来自案例）

1) 接口契约中明确时间字段的时区语义（本地时间不带 Z，UTC 才带 Z），并在 API 文档与后端序列化基类中统一实现；

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-3b72f546](接口/rec-3b72f546.md) | 接口 | invalid | 【迭代N】AI运行日志显示时间比实际时间早8小时 | 提供的嫌疑文件中没有任何一个属于 AI 运行日志（admin/#/ai-skill-runtime-log）的列表或详情渲染代码——lui/… |

## 复发提示
- 首次出现，暂作模式种子保留。下次同类缺陷会自动归入本模式，复发次数随之累加——**同一个模式被反复命中，就说明该处缺护栏**。
