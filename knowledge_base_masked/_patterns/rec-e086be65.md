---
pattern_id: rec-e086be65
name: "校验缺失 / 非法值可提交"
symptom: rec-e086be65
module: 
recurrence: 3
applied: 0
categories: UI, 逻辑
defects: rec-9f1b492b, rec-bf6466ac, rec-7a94d193
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：校验缺失 / 非法值可提交

> 复发 **3** 次 · 已采纳 0 例 · 分类：UI、逻辑

## 这类缺陷长什么样
提交前校验不完整：必填项、互斥条件或长度上限没拦住，数据以非法状态落库，往往到下游使用（无法触发、展示为空）才炸。

- 命中症状词：必填

## 共性根因
- ⚠️ 该模式下**尚无已采纳案例**，以下判断均未经验证：
- ⚠️ 待验证（[rec-9f1b492b](UI/rec-9f1b492b.md)，invalid）：提供的嫌疑文件中，edit-method_table.vue 是表/SQL 适配器的编辑组件，其「属性名称」区固定显示 entityTable 与 sqlStatement，未按 returnType 渲染结构体/表名；
- ⚠️ 待验证（[rec-bf6466ac](逻辑/rec-bf6466ac.md)，proposed）：packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue 的 forbiddenImport 计算属性未对“新增和更新”/“仅更新”模式下匹配字段（coverCode）做必填校验，只要 uploadCompleteStatus 为 true 就放行；
- ⚠️ 待验证（[rec-7a94d193](逻辑/rec-7a94d193.md)，gate_failed）：packages/<内部标识>-admin-core/app/src/views/biz-rule/property-schema/helper/custom-validate/variable-validate.ts 中的 variableDeleted 函数只处理字符串和数组类型的变量编码，当规则中引用 AI 变量时，condition.value 可能是对象等非字符串类型，调用 va…
- 共性线索（≥2 例根因均提及）：<内部标识>

## 处理方式
### 当前最佳判断（未验证）

尚未有已采纳案例。参考置信度最高的一例 [rec-9f1b492b](UI/rec-9f1b492b.md)：

提供的嫌疑文件中，edit-method_table.vue 是表/SQL 适配器的编辑组件，其「属性名称」区固定显示 entityTable 与 sqlStatement，未按 returnType 渲染结构体/表名；

嫌疑文件：`packages/<内部标识>-admin-core/shared/src/common/list-config-all.ts`、`packages/<内部标识>-list/list/src/components/pc/helper/list-config.ts`、`packages/<内部标识>-admin-core/app/src/views/biz-rule/property-schema/build-in-node/panel-get-list.ts`、`packages/<内部标识>-admin-core/app/src/views/biz-rule/modal/mappings-list.vue`、`packages/<内部标识>-list/list/src/components/pc/scripts/application-list.ts`

### 预防措施（来自案例）

适配器方法配置字段建议配置化：按 adapterType + returnType 组合定义字段清单，由统一的表单渲染器生成，避免每个适配器独立维护模板。

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-9f1b492b](UI/rec-9f1b492b.md) | UI | invalid | 【业务集成】SAP适配器方法 SingleObject/List 缺「结构体」「表名」 | 提供的嫌疑文件中，edit-method_table.vue 是表/SQL 适配器的编辑组件，其「属性名称」区固定显示 entityTabl… |
| [rec-bf6466ac](逻辑/rec-bf6466ac.md) | 逻辑 | proposed | 【迭代N】【视图设计】导入新增和更新模式未选必填匹配字段不拦截 | packages/<内部标识>-list/list/src/components/pc/components/model-table… |
| [rec-7a94d193](逻辑/rec-7a94d193.md) | 逻辑 | gate_failed | 【迭代N】AI智能校验的规则列表，如果变量使用了AI变量时，规则列表上显示错误。 | packages/<内部标识>-admin-core/app/src/views/biz-rule/property-schema/… |

## 复发提示
- 同类缺陷已出现 **3 次**，这是**系统性缺陷**，不是个案：建议固化为评审 checklist 条目，并在该模块补一条单测或 lint 规则护栏。
