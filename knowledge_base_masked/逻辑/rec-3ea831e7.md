---
defect_id: rec-3ea831e7
title: "【迭代N】个人助理导入：选择「修改编码」后静默导入0条且无提示（import_personal 仍返回 success:true）"
category: 逻辑
priority: 
ai_mode: openai
proposal_id: rec-3ea831e7-20260922-181628
status: applied
gate_level: degraded
commit: 
created: 2026-09-22T18:16:28
updated: 2026-09-24T11:08:02
kb_version: 2
---

# 【迭代N】个人助理导入：选择「修改编码」后静默导入0条且无提示（import_personal 仍返回 success:true）

## 现象
【测试环境】

agent-workspace

【测试分支】

develop

【测试账号】

<已脱敏账号凭据>

【前置条件】 1、环境：test-develop，以 <内部标识> 登录 LUI（/agent-workspace）； 2、账号下存在个人助理（导入前共 9 个）； 3、已导出合法导入包（导出时仅选中 1 个助理「公司制度查询助理」，包内 moduleStats.AGENT=1、package_index.json agentCount=1，导出范围正确）。

【测试步骤】 1、在个人助理页点「导 入」，上传上一步导出的合法 zip； 2、文件格式校验通过后，提示「导入文件编码与系统中编码重复」，处理方式下拉选择「修改编码」； 3、点「继续」&rarr; 下一步 &rarr; 点「导 入」； 4、观察导入结果、页面提示，并核对助理列表数量。

【预期结果】 按用例「合法文件可导入并生成对应助理」：选择「修改编码」后应能填写新编码并成功导入，或至少给出明确失败提示。

【实际结果】 1、步骤 3 接口返回「成功」但实际导入 0 条： POST /api/api/runtime/ai/importexport/import_personal &rarr; HTTP 200 {"errcode":0,"errmsg":"操作成功","data":{"success":true,"skillCount":0,"agentCount":0, "conflictItems":null,"importedSkillCodes":[],"importedAgentCodes":[],"overwrittenCodes":[], "warnings":["助理跳过（编码冲突未导入）：0cdf101b55bb4a45a8ab8f4145504995（RENAME 未提供新编码）"]}} 即 data.success = true，但 agentCount = 0、importedAgentCodes = []，一条都没有导入。 2、步骤 4 无任何提示：导入弹窗直接关闭，toast 轮询 6 秒为空；助理列表导入前 9 &rarr; 导入后 9，无新增。 用户看到的是「顺利完成」，实际什么都没导入。 3、根因（服务端 warnings 已自证）：界面选择了「修改编码」却**没有提供填写新编码的输入框**， 服务端因此收到「RENAME 未提供新编码」并把该助理跳过。 4、附带问题：validate_personal 返回的 conflictItems[].policy **恒为 "IGNORE"**： POST /api/api/runtime/ai/importexport/validate_personal &rarr; {"data":{"agentCount":1,"conflictItems":[{"moduleType":"AGENT","name":"公司制度查询助理", "code":"0cdf101b55bb4a45a8ab8f4145504995","policy":"IGNORE","newCode":null,"duplicated":true}], ...}} 即处理方式的选择未随校验请求下发，界面上选择「修改编码」后再次校验仍返回 IGNORE。

【对照实验（证明不是导入功能整体损坏）】 用同一个上传通道、同一个 zip 外壳上传结构非法的包 &rarr; 被正确拦截并提示「导入包解析失败」，页面不白屏。 说明通道本身可用，问题出在「编码冲突」分支。 另附接口契约问题：import_personal 在跳过全部条目的情况下仍返回 success:true， 建议以 importedAgentCodes.length 判定成功，并在 agentCount 为 0 时给出明确提示。 复现路径：agent-workspace &rarr; 个人助理 &rarr; 导入（选择已存在编码的合法包 &rarr; 处理方式选「修改编码」）

【截图】

未提供：该问题为接口在跳过全部条目时仍返回 success:true、且界面无提示，关键证据为 import_personal 与 validate_personal 的响应原文，界面截图仅能体现「弹窗关闭、列表无变化」，原文已写入描述正文

## 根因
packages/<内部标识>-ai/lui/components/import-dialog.vue 第 568-572 行的重命名校验被 this.shouldValidateRename(item.moduleType) 过滤，导致 AGENT 类型即使 policy 为 RENAME 也不会把 newCode 收集进 renamedResourceCodes 做非空/格式校验，空编码被直接提交，服务端跳过并返回 success:true；同时第 618-624 行只判断 response.data.success === false，未校验 importedAgentCodes/importedSkillCodes 为空，导致导入 0 条时页面仍按成功关闭。

## 说明
第一处改动放宽重命名校验条件：只要 policy 为 RENAME 就收集 newCode，空值或非法格式会命中下方 renamedResourceCodes.some 的非空/格式判断，在第 575-582 行给出明确错误并停留步骤 3。第二处改动在导入响应后追加判定：当 importedAgentCodes 和 importedSkillCodes 都为空时也抛出错误，并优先使用服务端 warnings 文案（如“RENAME 未提供新编码”）作为提示，避免 0 条导入被静默当成功。影响面仅限个人助理导入的冲突处理与结果提示，不涉及其他导入分支。

## 影响文件
| 文件 | 补丁块 | +行 | -行 | 状态 | 告警 |
| --- | --- | --- | --- | --- | --- |
| `packages/<内部标识>-ai/lui/components/import-dialog.vue` | 2 | 12 | 5 | ✅ 可应用 | — |

## 补丁（SEARCH/REPLACE）
```search-replace
<<<<<<< SEARCH packages/<内部标识>-ai/lui/components/import-dialog.vue
        if (this.shouldValidateRename(item.moduleType) && d.policy === 'RENAME')
          renamedResourceCodes.push(d.newCode);
=======
        if (d.policy === 'RENAME')
          renamedResourceCodes.push(d.newCode);
>>>>>>> REPLACE

<<<<<<< SEARCH packages/<内部标识>-ai/lui/components/import-dialog.vue
        if (response.errcode !== 0 || response.data?.success === false)
          throw new Error(
            response.data?.errorMessage ||
              response.errmsg ||
              this.resolveText(this.config.errors.importFailed),
          );
=======
        if (
          response.errcode !== 0 ||
          response.data?.success === false ||
          (!response.data?.importedAgentCodes?.length &&
            !response.data?.importedSkillCodes?.length)
        )
          throw new Error(
            response.data?.warnings?.length
              ? response.data.warnings.join('；')
              : response.data?.errorMessage ||
                response.errmsg ||
                this.resolveText(this.config.errors.importFailed),
          );
>>>>>>> REPLACE
```

## 建议 diff
```diff
--- a/packages/<内部标识>-ai/lui/components/import-dialog.vue
+++ b/packages/<内部标识>-ai/lui/components/import-dialog.vue
@@ -566,7 +566,7 @@
           policy: 'IGNORE',
           newCode: '',
         };
-        if (this.shouldValidateRename(item.moduleType) && d.policy === 'RENAME')
+        if (d.policy === 'RENAME')
           renamedResourceCodes.push(d.newCode);
       });
       if (
@@ -612,11 +612,18 @@
           fileName: file.fileName,
           conflictPolicyMap: map,
         });
-        if (response.errcode !== 0 || response.data?.success === false)
+        if (
+          response.errcode !== 0 ||
+          response.data?.success === false ||
+          (!response.data?.importedAgentCodes?.length &&
+            !response.data?.importedSkillCodes?.length)
+        )
           throw new Error(
-            response.data?.errorMessage ||
-              response.errmsg ||
-              this.resolveText(this.config.errors.importFailed),
+            response.data?.warnings?.length
+              ? response.data.warnings.join('；')
+              : response.data?.errorMessage ||
+                response.errmsg ||
+                this.resolveText(this.config.errors.importFailed),
           );
         responseData = response.data;
       }

```

## 验收闸门
```json
{
  "level": "degraded",
  "ok": true,
  "checks": [
    {
      "name": "skip:packages/<内部标识>-ai/lui/components/import-dialog.vue",
      "cmd": "<script> 块括号扫描（仅供参考）",
      "kind": "syntax",
      "returncode": 0,
      "ok": true,
      "skipped": true,
      "seconds": 0.0,
      "output_tail": "自动扫描疑似不平衡（文件结束时仍有未闭合的括号: {{），该检查对多行模板字符串会误报，请以 tsc/eslint 或人工复核为准"
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
  "status": "ok",
  "phase": "before",
  "route": "/agent-workspace",
  "route_source": "ticket",
  "final_url": "<已脱敏链接>",
  "page_title": "<内部标识>-Agent 工作区",
  "plan_note": "AI 根据工单描述编排的复现操作（选择器可能不准，逐步留痕）",
  "actions": [
    "goto /agent-workspace",
    "wait",
    "click button:text-is(\"导入\"):visible",
    "wait_for input[type=\"file\"]",
    "fill input[type=\"file\"]",
    "wait",
    "click [role=\"dialog\"] .el-select:visible",
    "click [role=\"dialog\"] .el-select-dropdown__item:text-is(\"修改编码\")",
    "click [role=\"dialog\"] button:text-is(\"继续\")",
    "click [role=\"dialog\"] button:text-is(\"导入\")"
  ],
  "actions_log": [
    {
      "op": "goto",
      "target": "/agent-workspace",
      "ok": true
    },
    {
      "op": "wait",
      "target": "",
      "ok": true
    },
    {
      "op": "click",
      "target": "button:text-is(\"导入\"):visible",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"button:text-is(\\\"导入\\\"):visible\")\n"
    },
    {
      "op": "wait_for",
      "target": "input[type=\"file\"]",
      "ok": false,
      "error": "Page.wait_for_selector: Timeout 5000ms exceeded.\nCall log:\n  - waiting for locator(\"input[type=\\\"file\\\"]\") to be visible\n    14 × locator re"
    },
    {
      "op": "fill",
      "target": "input[type=\"file\"]",
      "ok": false,
      "error": "Page.fill: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"input[type=\\\"file\\\"]\")\n    - locator resolved to <input type=\"file\" "
    },
    {
      "op": "wait",
      "target": "",
      "ok": true
    },
    {
      "op": "click",
      "target": "[role=\"dialog\"] .el-select:visible",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"[role=\\\"dialog\\\"] .el-select:visible\")\n"
    },
    {
      "op": "click",
      "target": "[role=\"dialog\"] .el-select-dropdown__item:text-is(\"修改编码\")",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"[role=\\\"dialog\\\"] .el-select-dropdown__item:text-is(\\\"修改编码\\\")\")\n"
    },
    {
      "op": "click",
      "target": "[role=\"dialog\"] button:text-is(\"继续\")",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"[role=\\\"dialog\\\"] button:text-is(\\\"继续\\\")\")\n"
    },
    {
      "op": "click",
      "target": "[role=\"dialog\"] button:text-is(\"导入\")",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"[role=\\\"dialog\\\"] button:text-is(\\\"导入\\\")\")\n"
    }
  ],
  "console_errors": [],
  "page_errors": []
}
```

## 复现步骤
1. 打开 `/`
2. （工单未提供更细复现步骤，需补充）

## 预防措施
1）为冲突处理策略的“修改编码”选项补充强制输入框，并在提交前统一走同一套 newCode 校验函数；2）前端对导入接口的 success 判定补齐 imported 数量兜底，避免依赖服务端 success 布尔值；3）为 validate_personal 与 import_personal 的 conflictPolicyMap 下发策略增加单元测试，覆盖 RENAME 空编码、RENAME 合法编码、IGNORE 全部冲突等分支。

## 关联信息
- 嫌疑文件: packages/<内部标识>-ai/lui/components/import-dialog.vue, packages/<内部标识>-form/runtime/components/form-detail.ts, packages/<内部标识>-ai/chat-kit/runtime-chat/form-assistant/form-assistant-index.mixin.ts, packages/<内部标识>-form/runtime/form-action-modal.ts, packages/<内部标识>-form/runtime/relevance/relevance-form-control.ts, packages/<内部标识>-form/runtime/components/pc/form-reject.vue, packages/<内部标识>-form/runtime/components/pc/form-next-node.vue, packages/<内部标识>-form/runtime/components/pc/form-dept-select.vue
- 关键词: package_index.js, import_personal, moduleStats, package_index, agentCount, skillCount, conflictItems, importedSkillCodes, importedAgentCodes, overwrittenCodes, validate_personal, moduleType
- 提案状态: applied；commit: —
- 提案文件: `proposals/rec-3ea831e7-20260922-181628.json`
