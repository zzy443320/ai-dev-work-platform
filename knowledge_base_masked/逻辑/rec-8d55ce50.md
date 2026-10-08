---
defect_id: rec-8d55ce50
title: "【迭代N】AI智能提交规则，条件组模式配置条件并保存后，条件组内容丢失。"
category: 逻辑
priority: 
ai_mode: openai
proposal_id: rec-8d55ce50-20261008-120822
status: applied
gate_level: package_json
commit: 
created: 2026-10-08T12:08:22
updated: 2026-10-08T12:12:27
kb_version: 2
---

# 【迭代N】AI智能提交规则，条件组模式配置条件并保存后，条件组内容丢失。

## 现象
【测试环境】 <已脱敏链接>

【测试分支】develop

【测试账号】

## 根因
FormAiBizObjectSkillConditions 的 parseConditionGroups 将后端持久化数据按二维数组（Condition[][]）解析，但实际持久化结构为按组存放的 { conditions: [...] } 对象数组。parseConditionGroups 遇到对象组时因 Array.isArray(group) 为 false 而 push 空数组，导致条件组内容全部丢失；stringifyConditionGroups 也按二维数组返回，保存时未包装 conditions 字段，使得后端存储的格式错误。

## 说明
将 parseConditionGroups 改为兼容并正确解析 { conditions: [...] } 格式（同时兼容旧二维数组格式），将 stringifyConditionGroups 改为输出 { conditions: [...] } 格式。这样读取后端数据时能正确还原条件组，保存时能生成后端期望的数据结构，条件组内容不再丢失。影响范围仅限该 AI 技能条件组处理器，不影响其他条件组场景。

## 影响文件
| 文件 | 补丁块 | +行 | -行 | 状态 | 告警 |
| --- | --- | --- | --- | --- | --- |
| `packages/<内部标识>-ai/condition-group-handlers/form-ai-biz-object-skill-conditions.ts` | 2 | 17 | 13 | ✅ 可应用 | — |

## 补丁（SEARCH/REPLACE）
```search-replace
<<<<<<< SEARCH packages/<内部标识>-ai/condition-group-handlers/form-ai-biz-object-skill-conditions.ts
    conditionGroupValueFromBack.forEach((group: any) => {
      if (!Array.isArray(group)) {
        conditionsGroups.push([]);
        return;
      }

      const conditions: Condition[] = group.map((condition: any) => {
=======
    conditionGroupValueFromBack.forEach((group: any) => {
      let conditionsArray: any[] = [];
      if (Array.isArray(group)) {
        conditionsArray = group;
      } else if (group && Array.isArray(group.conditions)) {
        conditionsArray = group.conditions;
      } else {
        conditionsGroups.push([]);
        return;
      }

      const conditions: Condition[] = conditionsArray.map((condition: any) => {
>>>>>>> REPLACE

<<<<<<< SEARCH packages/<内部标识>-ai/condition-group-handlers/form-ai-biz-object-skill-conditions.ts
      result.push(resultConditions);
    });

    let finalResult = result.map((item) => {
      if (Array.isArray(item)) {
        item = item.filter((el: any) => el.propertyCode);
      }
      if (Array.isArray(item) && item.length === 0) {
        return null;
      }
      return item;
    });
    finalResult = finalResult.filter((item) => item !== null);
    return finalResult;
  }
=======
      result.push({ conditions: resultConditions });
    });

    let finalResult = result
      .map((item) => {
        item.conditions = item.conditions.filter((el: any) => el.propertyCode);
        if (item.conditions.length === 0) {
          return null;
        }
        return item;
      })
      .filter((item) => item !== null);
    return finalResult;
  }
>>>>>>> REPLACE
```

## 建议 diff
```diff
--- a/packages/<内部标识>-ai/condition-group-handlers/form-ai-biz-object-skill-conditions.ts
+++ b/packages/<内部标识>-ai/condition-group-handlers/form-ai-biz-object-skill-conditions.ts
@@ -308,12 +308,17 @@
     const conditionsGroups: Condition[][] = [];
 
     conditionGroupValueFromBack.forEach((group: any) => {
-      if (!Array.isArray(group)) {
+      let conditionsArray: any[] = [];
+      if (Array.isArray(group)) {
+        conditionsArray = group;
+      } else if (group && Array.isArray(group.conditions)) {
+        conditionsArray = group.conditions;
+      } else {
         conditionsGroups.push([]);
         return;
       }
 
-      const conditions: Condition[] = group.map((condition: any) => {
+      const conditions: Condition[] = conditionsArray.map((condition: any) => {
         let propertyCode: string = condition.propertyCode;
         if (
           typeof propertyCode === 'string' &&
@@ -494,19 +499,18 @@
         resultConditions.push(resultCondition);
       });
 
-      result.push(resultConditions);
+      result.push({ conditions: resultConditions });
     });
 
-    let finalResult = result.map((item) => {
-      if (Array.isArray(item)) {
-        item = item.filter((el: any) => el.propertyCode);
-      }
-      if (Array.isArray(item) && item.length === 0) {
-        return null;
-      }
-      return item;
-    });
-    finalResult = finalResult.filter((item) => item !== null);
+    let finalResult = result
+      .map((item) => {
+        item.conditions = item.conditions.filter((el: any) => el.propertyCode);
+        if (item.conditions.length === 0) {
+          return null;
+        }
+        return item;
+      })
+      .filter((item) => item !== null);
     return finalResult;
   }
 

```

## 验收闸门
```json
{
  "level": "package_json",
  "ok": false,
  "checks": [
    {
      "name": "typecheck",
      "cmd": "npm run type-check",
      "kind": "command",
      "returncode": 2,
      "ok": false,
      "skipped": false,
      "seconds": 33.9,
      "output_tail": "> frontend@AI-Platform-v2.1.0 type-check\n> yarn type-check:portal && yarn type-check:admin && yarn type-check:mobile\n\nyarn run v1.22.22\n$ cd packages/portal && yarn run type-check\n$ tsc --noEmit -p tsconfig.json\nerror TS2688: Cannot find type definition file for 'jest'.\ninfo Visit <已脱敏链接> for documentation about this command.\ninfo Visit <已脱敏链接> for documentation about this command.\nerror Command failed with exit code 2.\nerror Command failed with exit code 2."
    }
  ],
  "notes": [
    "未配置 gate.commands，已从 package.json scripts 自动选用：typecheck",
    "验收命令失败：本次提案不会自动进入可采纳状态，需修复建议或在 UI 勾选强制采纳。"
  ],
  "failed_checks": [
    "typecheck"
  ]
}
```

## 页面验证
```json
{
  "status": "unreachable",
  "phase": "before",
  "route": "/apps/model/lqy83111/c1/data/dataRuleCalculation",
  "route_source": "ticket",
  "console_errors": [],
  "error": "<已脱敏链接> 不可达，跳过截图（没有真实验证，请勿视为通过）"
}
```

## 复现步骤
1. 打开 `/`
2. （工单未提供更细复现步骤，需补充）

## 预防措施
在条件组处理器中增加单元测试，覆盖持久化格式的解析与序列化往返；持久化格式变更时同步更新 parse/stringify 并添加双向映射的回归用例。

## 关联信息
- 嫌疑文件: packages/<内部标识>-utils/src/full-calendar.ts, packages/<内部标识>-ai/lui/components/workspace-header.vue, "docs/\344\272\221\346\236\242\345\211\215\347\253\257\345\274\200\345\217\221\346\214\207\345\215\227.md", "docs/AI\345\257\271\350\257\235\350\241\250\345\215\225\346\217\220\344\272\244\345\211\215\345\220\216\347\253\257\344\270\200\350\207\264\346\200\247\346\211\247\350\241\214\346\270\205\345\215\225.md", "docs/AI-Platform-v2.1.0\347\211\210\346\234\254\350\257\264\346\230\216.md", packages/<内部标识>-ai/condition-group-handlers/form-ai-biz-object-skill-conditions.ts, packages/<内部标识>-form/renderer/controls/ai-control.ts, packages/<内部标识>-form/runtime/components/form-detail.ts
- 关键词: 智能提交规则, 条件组内容丢失, 智能, 能提, 交规, 件组, 组模, 模式, 式配, 置条
- 提案状态: applied；commit: —
- 提案文件: `proposals/rec-8d55ce50-20261008-120822.json`
