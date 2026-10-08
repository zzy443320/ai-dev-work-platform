---
pattern_id: rec-4ef990fd
name: "展示遮挡 / 截断"
symptom: rec-4ef990fd
module: 
recurrence: 1
applied: 0
categories: UI
defects: rec-d17c3799
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 模式：展示遮挡 / 截断

> 复发 **1** 次 · 已采纳 0 例 · 分类：UI

## 这类缺陷长什么样
布局或样式在特定条件下容不下内容，文案被遮挡、截断或错位。

- 命中症状词：遮挡、被遮、截断

## 共性根因
- ⚠️ 该模式下**尚无已采纳案例**，以下判断均未经验证：
- ⚠️ 待验证（[rec-d17c3799](UI/rec-d17c3799.md)，rejected）：es-setting.vue 中“SSL 证书校验”这一表单项直接复用 formItemLayout（labelCol.span = 2，约占 8.3% 宽度），当协议切为 https 时该项目才渲染，其 label 文案（SSL 证书校验）明显长于其它 label（协议类型/服务 IP/端口等），在 2 栅格的固定宽度内被截断/换行后遮挡。

## 处理方式
### 当前最佳判断（未验证）

尚未有已采纳案例。参考置信度最高的一例 [rec-d17c3799](UI/rec-d17c3799.md)：

es-setting.vue 中“SSL 证书校验”这一表单项直接复用 formItemLayout（labelCol.span = 2，约占 8.3% 宽度），当协议切为 https 时该项目才渲染，其 label 文案（SSL 证书校验）明显长于其它 label（协议类型/服务 IP/端口等），在 2 栅格的固定宽度内被截断/换行后遮挡。

嫌疑文件：`packages/<内部标识>-admin-core/system/src/views/integration-setting/es-setting.vue`、`packages/<内部标识>-admin-core/system/src/views/integration-setting/modal/notify-setting-drawer.vue`、`packages/<内部标识>-admin-core/system/src/views/common-setting/portal-setting.vue`、`packages/<内部标识>-admin-core/system/src/views/integration-setting/annex-preview.vue`、`packages/<内部标识>-admin-core/system/src/views/integration-setting/modal/sms-template-edit.vue`

### 预防措施（来自案例）

1) 新增表单项时按最长语言文案预估 label 宽度，长文案项单独指定 labelCol，不要一律复用统一 formItemLayout；

## 典型案例

| 缺陷 | 分类 | 状态 | 标题 | 根因摘要 |
| --- | --- | --- | --- | --- |
| [rec-d17c3799](UI/rec-d17c3799.md) | UI | rejected | 【迭代N】系统管理-统一配置-高级检索，https协议的配置项名称被遮挡 | es-setting.vue 中“SSL 证书校验”这一表单项直接复用 formItemLayout（labelCol.span = 2，约… |

## 复发提示
- 首次出现，暂作模式种子保留。下次同类缺陷会自动归入本模式，复发次数随之累加——**同一个模式被反复命中，就说明该处缺护栏**。
