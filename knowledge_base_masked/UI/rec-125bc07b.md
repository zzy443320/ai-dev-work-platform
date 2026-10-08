---
defect_id: rec-125bc07b
title: "【迭代N】添加数据洞察协作助理，图标破碎"
category: UI
priority: 
ai_mode: openai
proposal_id: rec-125bc07b-20260924-113401
status: gate_failed
gate_level: degraded
commit: 
created: 2026-09-24T11:34:01
updated: 2026-09-24T11:34:01
kb_version: 2
---

# 【迭代N】添加数据洞察协作助理，图标破碎

## 现象
【测试环境】

<内部标识>-Agent 工作区

【测试分支】

develop

【测试账号】

<已脱敏账号凭据>

【前置条件】

【测试步骤】

【预期结果】

【实际结果】

## 根因
packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts 中 agentIconUrl getter 直接将 (this.agentData as any)?.icon 传入 resolveAgentIconUrl，当数据洞察协作助理未配置 icon 字段（值为 undefined/null）时，resolveAgentIconUrl 可能返回一个 truthy 但无效的 URL 字符串，导致 <img> 渲染出破碎图标。需确认 <内部标识>-ai/shared/utils/agent-icon 中 resolveAgentIconUrl 对空值的处理逻辑。

## 说明
在 agentIconUrl getter 中增加空值守卫：若 icon 字段为空则直接返回空字符串，避免将 undefined 传入 resolveAgentIconUrl 产生无效地址。下游 applyAgentDataToMessage 已有 if (this.agentIconUrl) 判断，空字符串不会被赋值到消息的 agentIcon，从而避免渲染破碎 <img>。影响面仅限于 icon 为空的助理（如新添加的数据洞察协作助理），其余已配置图标的助理行为不变。

## 影响文件
| 文件 | 补丁块 | +行 | -行 | 状态 | 告警 |
| --- | --- | --- | --- | --- | --- |
| `packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts` | 1 | 3 | 1 | ✅ 可应用 | — |

## 补丁（SEARCH/REPLACE）
```search-replace
<<<<<<< SEARCH packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts
  /** 将助理图标标识转换为可直接渲染的地址。 */
  get agentIconUrl(): string {
    return resolveAgentIconUrl((this.agentData as any)?.icon);
  }
=======
  /** 将助理图标标识转换为可直接渲染的地址。 */
  get agentIconUrl(): string {
    const icon = (this.agentData as any)?.icon;
    if (!icon) return '';
    return resolveAgentIconUrl(icon);
  }
>>>>>>> REPLACE
```

## 建议 diff
```diff
--- a/packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts
+++ b/packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts
@@ -323,7 +323,9 @@
 
   /** 将助理图标标识转换为可直接渲染的地址。 */
   get agentIconUrl(): string {
-    return resolveAgentIconUrl((this.agentData as any)?.icon);
+    const icon = (this.agentData as any)?.icon;
+    if (!icon) return '';
+    return resolveAgentIconUrl(icon);
   }
 
   /** 返回经过兼容处理后的开场引导问题文本。 */

```

## 验收闸门
```json
{
  "level": "degraded",
  "ok": false,
  "checks": [
    {
      "name": "balance:packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts",
      "cmd": "括号/引号闭合检查",
      "kind": "syntax",
      "returncode": 1,
      "ok": false,
      "skipped": false,
      "seconds": 0.0,
      "output_tail": "文件结束时仍有未闭合的括号: {{"
    }
  ],
  "notes": [
    "package.json 里没有可识别的 type-check/lint/test/build 脚本。",
    "已降级为括号/字符串闭合与 py 编译检查——**这只证明代码没被写坏，不证明类型和测试通过，请务必人工复核 diff。**"
  ],
  "failed_checks": [
    "balance:packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts"
  ]
}
```

## 页面验证
```json
{
  "status": "unreachable",
  "phase": "before",
  "route": "/<内部标识>/<内部标识>",
  "route_source": "ticket",
  "console_errors": [],
  "error": "<已脱敏链接> 不可达，跳过截图（没有真实验证，请勿视为通过）"
}
```

## 复现步骤
1. 打开 `/`
2. （工单未提供更细复现步骤，需补充）

## 预防措施
1. 在 resolveAgentIconUrl 工具函数内部对 falsy 入参统一返回空字符串并补充单元测试；2. 新增助理类型时，在集成测试中断言 agentIconUrl 为合法 URL 或空串，不允许出现 'undefined'/'null' 等非法字符串。

## 关联信息
- 嫌疑文件: packages/<内部标识>-ai/lui/components/workspace-header.vue, packages/<内部标识>-ai/lui/pages/schedule/index.vue, packages/<内部标识>-ai/lui/mobile/pages/schedule/index.vue, "docs/AI-Platform-v2.1.0\347\211\210\346\234\254\350\257\264\346\230\216.md", packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts, packages/<内部标识>-apis/api/src/ai.api.ts, packages/<内部标识>-ai/chat-kit/components/composer/pc/instruction/insight-instruction-presets.ts, packages/<内部标识>-ai/ai-platform/components/ai-agent/ai-agent-detail/agent-role-textarea/skill-select-panel.vue
- 关键词: <内部标识>/<内部标识>, 图标破碎, 前置条件, 测试步骤, 加数, 据洞, 洞察, 察协, 协作, 作助, 助理, 图标
- 提案状态: gate_failed；commit: —
- 提案文件: `proposals/rec-125bc07b-20260924-113401.json`
