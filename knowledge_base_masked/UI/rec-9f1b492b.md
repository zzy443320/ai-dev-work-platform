---
defect_id: rec-9f1b492b
title: "【业务集成】SAP适配器方法 SingleObject/List 缺「结构体」「表名」"
category: UI
priority: 
ai_mode: openai
proposal_id: rec-9f1b492b-20260929-160250
status: invalid
gate_level: none
commit: 
created: 2026-09-29T16:02:50
updated: 2026-09-29T16:02:50
kb_version: 2
---

# 【业务集成】SAP适配器方法 SingleObject/List 缺「结构体」「表名」

## 现象
【测试环境】

<已脱敏链接>

【测试分支】

8.8.0-SNAPSHOT（AI-Platform-v2.1.0）

【测试账号】

<已脱敏账号凭据>

【前置条件】

管理后台

【业务集成】-

【集成服务】下已存在 SAP 适配器服务（本例：编码 e_cluster_sap_service_r1，服务属性-数据源已选择 SAP 数据源）。

该服务下已有已保存的方法（用于验证编辑态）。

【测试步骤】

业务集成 &rarr; 集成服务 &rarr; 在 SAP 适配器服务行点击

【新增】，打开「新增方法」抽屉。

点击

【返回值类型】下拉，选择 SingleObject；查看「属性名称」区出现哪些配置项。

再次打开

【返回值类型】下拉，切换为 List；查看「属性名称」区。

保存一个 List 类型的方法后，点击其行内

【编辑】重新打开「编辑方法」抽屉，重复步骤 2~3。

【预期结果】

依据《<内部标识>用户手册（企业AI平台V1.1版本）》业务集成\集成服务\业务集成场景示例.md「示例六：SAP 适配器」原文：「当返回类型选择 SingObject 时，需要设置结构体（SAP自定义生成）」「当返回类型选择 List 时，需要设置对应数据库表名」。

即：返回值类型选 SingleObject 时，「属性名称」区应显示

【函数名】

【结构体】两项必填；选 List 时应显示

【函数名】

【表名】两项必填。

【实际结果】

新增与编辑方法抽屉中，返回值类型切换为 SingleObject 或 List 后，「属性名称」区始终只有

【函数名】一项（必填短文本，maxlength=200），既没有

【结构体】也没有

【表名】。

实测证据：Void / SingleObject / List 三种类型下抽屉内表单项数量均为 7，标签序列完全一致（方法名称、方法编码、返回值类型、函数名、允许前端在线开发调用、空值入参处理方式、方法描述）；在 SingleObject、List 状态下检索整个抽屉 HTML，「结构体」「表名」字符串命中数均为 0（新增态与编辑态各验证一次）。

影响：SAP 适配器二期能力中，SingleObject/List 返回值无法按手册配置结构体与表名，配置入口缺失

T88075-01-singobject字段显示.png

## 根因
提供的嫌疑文件中，edit-method_table.vue 是表/SQL 适配器的编辑组件，其「属性名称」区固定显示 entityTable 与 sqlStatement，未按 returnType 渲染结构体/表名；SAP 适配器方法编辑抽屉组件（应为独立的 edit-method_sap.vue 或经由 method-extra-fields.vue 按 adapterType 分支）未在嫌疑文件提供，需要查看该组件的模板与 configs 处理逻辑。

## 说明
该缺陷是 SAP 适配器编辑抽屉未根据返回值类型 SingleObject/List 渲染「结构体」/「表名」字段。修复需在 SAP 适配器方法编辑组件中按 configs.returnType 动态显示对应必填项，并补充变量与多语言。当前提供的窗口不包含该组件，无法给出安全的最小补丁。

## 影响文件
（无文件改动）

## 补丁（SEARCH/REPLACE）
```search-replace
(模型未给出 SEARCH/REPLACE 块)
```

## 建议 diff
```diff
(未生成 diff)
```

## 验收闸门
```json
{
  "level": "none",
  "ok": false,
  "checks": [],
  "notes": [
    "补丁未构建成功，未执行验收"
  ],
  "failed_checks": []
}
```

## 页面验证
```json
{
  "status": "unreachable",
  "phase": "before",
  "route": "/integration",
  "route_source": "ticket",
  "console_errors": [],
  "error": "<已脱敏链接> 不可达，跳过截图（没有真实验证，请勿视为通过）"
}
```

## 复现步骤
1. 打开 `/`
2. （工单未提供更细复现步骤，需补充）

## 预防措施
适配器方法配置字段建议配置化：按 adapterType + returnType 组合定义字段清单，由统一的表单渲染器生成，避免每个适配器独立维护模板。

## 关联信息
- 嫌疑文件: packages/<内部标识>-admin-core/shared/src/common/list-config-all.ts, packages/<内部标识>-list/list/src/components/pc/helper/list-config.ts, packages/<内部标识>-admin-core/app/src/views/biz-rule/property-schema/build-in-node/panel-get-list.ts, packages/<内部标识>-admin-core/app/src/views/biz-rule/modal/mappings-list.vue, packages/<内部标识>-list/list/src/components/pc/scripts/application-list.ts, packages/<内部标识>-admin-core/integration/src/views/integration/modals/edit-method_table.vue, packages/<内部标识>-admin-core/integration/src/views/integration/modals/add-method_table.vue, packages/<内部标识>-admin-core/integration/src/views/integration/modals/add-method_sql.vue
- 关键词: SingleObject, e_cluster_sap_service_r1, SingObject, SingleObject/List, <内部标识>/<内部标识>, 示例六：SAP 适配器, SNAPSHOT, rarr, 业务集成, 适配器方法, 前置条件, 管理后台
- 提案状态: invalid；commit: —
- 提案文件: `proposals/rec-9f1b492b-20260929-160250.json`
