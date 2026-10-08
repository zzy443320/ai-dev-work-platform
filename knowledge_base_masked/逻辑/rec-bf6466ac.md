---
defect_id: rec-bf6466ac
title: "【迭代N】【视图设计】导入新增和更新模式未选必填匹配字段不拦截"
category: 逻辑
priority: 
ai_mode: openai
proposal_id: rec-bf6466ac-20260929-154130
status: proposed
gate_level: degraded
commit: 
created: 2026-09-29T15:41:30
updated: 2026-09-29T15:41:30
kb_version: 2
---

# 【迭代N】【视图设计】导入新增和更新模式未选必填匹配字段不拦截

## 现象
【测试环境】

<已脱敏链接>

【测试分支】

develop

【测试账号】

<已脱敏账号凭据>

【前置条件】

客户管理  vd_customer  列表有数据（含已存在编码 C003）；视图「新增」按钮已绑定业务表单；使用系统管理员账号。

【测试步骤】

前台列表 &rarr; 点「导 入」&rarr; 导入方式选「 新增和更新 」。

上传含已存在客户编码 C003 的文件（3 行：C003 已存在 + W001/W002 全新）。

不选择「匹配字段」 （该控件带红色 require-icon，属必填）直接点「下一步」。

执行「导入」，完成后重载列表查看条数与客户编码 C003 的条数。

【预期结果】

手册《列表数据导入.md》：「新增和更新：根据选择的字段，将导入文件数据与系统内数据匹配… 匹配字段需上传文件后选择 」。匹配字段为必填，未选择时应被拦截，不应进入预览与导入。

【实际结果】

不选匹配字段 未被拦截 ：可正常进入预览并导入，3 行 全部按新增插入 ，导致同一客户编码出现重复数据。

关键证据： import_progress  =  {successCount:3, errorCount:0, type:"ADD_UPDATE", taskStatus:"END"} ；完成文案「共处理3条数据 导入成功3条数据」；列表总条数 22 &rarr; 25；随后列表中出现 两条 C003 （华东分公司/280000 与 华东分公司改名RL4/888888）。

对照：切到「 仅更新 」模式做同样「不选匹配字段」操作，会被拦截、停留在第 1 步 &rarr; 说明该必填校验在「新增和更新」模式下缺失。

「新增和更新」模式未选必填的匹配字段，仍能进入预览

导入后列表出现两条相同客户编码 C003 的重复数据

## 根因
packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue 的 forbiddenImport 计算属性未对“新增和更新”/“仅更新”模式下匹配字段（coverCode）做必填校验，只要 uploadCompleteStatus 为 true 就放行；且 coverCode 为空数组时是 truthy，导致未选匹配字段也能点击下一步。

## 说明
显式判断当前导入类型：非 ADD 模式且 coverCode 为空（false 或空数组）时返回 true 禁用“下一步”按钮，从而在进入预览前拦截，防止未选匹配字段导致全部按新增插入产生重复数据。

## 影响文件
| 文件 | 补丁块 | +行 | -行 | 状态 | 告警 |
| --- | --- | --- | --- | --- | --- |
| `packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue` | 1 | 9 | 17 | ✅ 可应用 | — |

## 补丁（SEARCH/REPLACE）
```search-replace
<<<<<<< SEARCH packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue
  get forbiddenImport() {
    if (this.uploadCompleteStatus && this.coverStatus && this.coverCode) {
      return false;
    } else if (
      this.uploadCompleteStatus &&
      !this.coverStatus &&
      this.coverCode
    ) {
      return false;
    } else if (
      this.uploadCompleteStatus &&
      !this.coverStatus &&
      !this.coverCode
    ) {
      return false;
    } else {
      //Else Empty block statement
    }
    return true;
  }
=======
  get forbiddenImport() {
    if (!this.uploadCompleteStatus) {
      return true;
    }
    if (
      this.importType !== 'ADD' &&
      (!this.coverCode || this.coverCode.length === 0)
    ) {
      return true;
    }
    return false;
  }
>>>>>>> REPLACE
```

## 建议 diff
```diff
--- a/packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue
+++ b/packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue
@@ -372,24 +372,16 @@
   }
 
   get forbiddenImport() {
-    if (this.uploadCompleteStatus && this.coverStatus && this.coverCode) {
-      return false;
-    } else if (
-      this.uploadCompleteStatus &&
-      !this.coverStatus &&
-      this.coverCode
+    if (!this.uploadCompleteStatus) {
+      return true;
+    }
+    if (
+      this.importType !== 'ADD' &&
+      (!this.coverCode || this.coverCode.length === 0)
     ) {
-      return false;
-    } else if (
-      this.uploadCompleteStatus &&
-      !this.coverStatus &&
-      !this.coverCode
-    ) {
-      return false;
-    } else {
-      //Else Empty block statement
-    }
-    return true;
+      return true;
+    }
+    return false;
   }
 
   get shrinkImportModal() {

```

## 验收闸门
```json
{
  "level": "degraded",
  "ok": true,
  "checks": [
    {
      "name": "skip:packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue",
      "cmd": "<script> 块括号扫描（仅供参考）",
      "kind": "syntax",
      "returncode": 0,
      "ok": true,
      "skipped": true,
      "seconds": 0.0,
      "output_tail": "自动扫描疑似不平衡（文件结束时仍有未闭合的括号: {{{），该检查对多行模板字符串会误报，请以 tsc/eslint 或人工复核为准"
    }
  ],
  "notes": [
    "package.json 里没有可识别的 type-check/lint/test/build 脚本。",
    "已降级为括号/字符串闭合与 py 编译检查——**这只证明代码没被写坏，不证明类型和测试通过，请务必人工复核 diff。**"
  ],
  "failed_checks": []
}
```

## 页面验证
```json
{
  "status": "unreachable",
  "phase": "before",
  "route": "/list/vd_customer",
  "route_source": "ticket",
  "console_errors": [],
  "error": "<已脱敏链接> 不可达，跳过截图（没有真实验证，请勿视为通过）"
}
```

## 复现步骤
1. 打开 `/`
2. （工单未提供更细复现步骤，需补充）

## 预防措施
为导入方式切换与匹配字段必填校验增加单元测试，覆盖 ADD_UPDATE/UPDATE 未选匹配字段的场景；统一 coverCode 空值语义（false 或空数组）。

## 关联信息
- 嫌疑文件: packages/<内部标识>-apis/api/src/api.mappings.ts, packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import-reports.vue, packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import-attachment.vue, packages/<内部标识>-form/core/components/Sheet/merge-value-fun.ts, packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import.vue, packages/<内部标识>-list/list/src/components/pc/components/model-table-import/import-input.vue, packages/<内部标识>-ai/ai-platform/components/business-semantic/dialogs/import-modal.vue, packages/<内部标识>-form/designer/components/import-data-new/index.vue
- 关键词: vd_customer, import_progress, successCount, errorCount, taskStatus, <内部标识>/<内部标识>, W001/W002, RL4/888888, ADD_UPDATE, 不选匹配字段, W001, W002
- 提案状态: proposed；commit: —
- 提案文件: `proposals/rec-bf6466ac-20260929-154130.json`
