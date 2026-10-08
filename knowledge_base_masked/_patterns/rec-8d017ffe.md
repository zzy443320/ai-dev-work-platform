---
pattern_id: rec-8d017ffe
name: "解析兜底 / 误报告警"
symptom: rec-8d017ffe
module: 
recurrence: 1
applied: 1
categories: 其他
defects: rec-853da915
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：解析兜底 / 误报告警

> 复发 **1** 次 · 已采纳 1 例 · 分类：其他

## 这类缺陷长什么样
解析或兜底逻辑把非预期输入当异常处理，产生噪音告警，甚至误判数据类型。

- 命中症状词：json.parse、解析失败、failed to parse、console 告警、控制台告警、兜底、误报

## 共性根因
- ✅ **已验证**（[rec-853da915](其他/rec-853da915.md)，已采纳）：packages/<内部标识>-utils/src/common-utils.ts 中通用解析工具 `safeGetObject` 在 JSON.parse 失败的 catch 分支里直接 `console.warn('Failed to parse JSON string:', error)` 并返回 {}；

## 处理方式
### 可复用改法（来自已采纳案例，可直接对照）

| 案例 | 状态 | 涉及文件 | 改法要点 |
| --- | --- | --- | --- |
| rec-853da915 | applied | `packages/<内部标识>-utils/src/common-utils.ts`、`packages/<内部标识>-a… | packages/<内部标识>-utils/src/common-utils.ts 中通用解析工具 `safeGetObject` … |

### 预防措施（来自案例）

1) 约定通用工具函数的“可预期失败分支”使用 console.debug 或静默返回，禁止 console.warn/error；

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-853da915](其他/rec-853da915.md) | 其他 | applied | 【迭代N】<内部标识>Agent工作区：AI纯文本回复被当JSON解析，每条消息误报consol… | packages/<内部标识>-utils/src/common-utils.ts 中通用解析工具 `safeGetObject` … |

## 复发提示
- 首次出现，暂作模式种子保留。下次同类缺陷会自动归入本模式，复发次数随之累加——**同一个模式被反复命中，就说明该处缺护栏**。
- 复用入口：优先看 [rec-853da915](其他/rec-853da915.md) 的补丁，其次再看本模式的其它案例差异。
