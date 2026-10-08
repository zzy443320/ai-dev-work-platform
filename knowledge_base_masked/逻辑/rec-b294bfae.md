---
defect_id: rec-b294bfae
title: "【迭代N】【LUI-多层级的记忆】移动端个性化设置：字段超过长度上限时提交静默失败，无任何提示且弹窗直接关闭、输入丢失"
category: 逻辑
priority: 
ai_mode: openai
proposal_id: rec-b294bfae-20260922-185500
status: invalid
gate_level: none
commit: 
created: 2026-09-22T18:55:00
updated: 2026-09-24T11:08:04
kb_version: 2
---

# 【迭代N】【LUI-多层级的记忆】移动端个性化设置：字段超过长度上限时提交静默失败，无任何提示且弹窗直接关闭、输入丢失

## 现象
【测试环境】

mobile/#/agent-workspace/

【测试分支】

develop

【测试账号】

<已脱敏账号凭据>

【前置条件】

使用 <内部标识> 登录测试环境，在桌面端「个性化设置 → 个人资料」已配置昵称、职业；「个人记忆」已有内容；

使用移动端浏览器（iPhone Safari UA，390×844）访问移动端 LUI；

对照能力：桌面端同一接口在超限时有明确 toast 提示（见下方

【对照】）。

【测试步骤】

移动端进入 /mobile/#/agent-workspace/ ，展开侧边栏，点击右上角「个性化设置」（齿轮）；

点击「记忆详情」，在弹层底部点击「修改记忆」；

在「告诉AI要记住什么或忘记什么...」输入框中输入 2500 个字符（上限为 2000）；

点击「确定」，分别在 1.2 秒、4.7 秒后观察界面，并抓取 /api/api/runtime/ai/memory/update 响应；

发散验证：按同一路径，分别在「昵称」输入 101 字符（上限 100）、在「职业」输入超长值（上限 256），点击「确定」观察界面。

【预期结果】

前端给出明确的错误提示（与桌面端一致，如「调整指令超过上限 2000 字符」）；弹窗不关闭；用户输入保留，便于修改后重试。

【实际结果】

点击「确定」后，接口返回超限错误，但 界面无任何错误提示 ——既无 toast、也无行内红字、也无任何文案：

POST /api/api/runtime/ai/memory/update
{"errcode":100003,"errmsg":"参数错误！","data":"调整指令超过上限 2000 字符","traceId":"f963b890ed6b4acd9947f98..."}

下图依次为：修改记忆弹窗中输入 2500 字符的编辑态；点「确定」后 1.2 秒界面无任何提示；点「确定」后 4.7 秒弹窗已关闭、输入丢失、仍无任何提示。

修改记忆弹窗中输入 2500 字符（上限 2000）的编辑态

点「确定」后 1.2 秒，界面无任何错误提示

点「确定」后 4.7 秒，弹窗已关闭、输入丢失，仍无任何提示

「修改记忆」弹窗 被直接关闭 ，用户已输入的 2500 字符 全部丢失 ；

详情区曾短暂出现「记忆更新中，可能需要一点时间，请稍后查看」，但该文案为成功态提示，约 2 秒内即消失，与本次失败无关；

连续 3 次复现，3/3 稳定触发 ，非偶现；

发散验证：同一问题在其它字段同样存在（根因相同，均为个性化设置提交链路） ——

昵称输入 101 字符 → 接口返回  {"errcode":100003,"data":"nickname 超过上限 100 字符"} ，界面无任何提示

职业输入超长 → 接口返回  {"errcode":100003,"data":"occupation 超过上限 256 字符"} ，界面无任何提示

下图依次为：昵称输入 101 字符的编辑态；点「确定」后同样无任何提示。

发散验证：昵称输入 101 字符（上限 100）的编辑态

发散验证：昵称超限提交后同样无任何提示

后端 error 日志按 traceId 检索  0 命中 ，接口返回结构完整（含明确的 errmsg），可判定问题在 前端未解析并展示错误 ，非后端异常。

【对照：桌面端同一能力有提示】

桌面端「个性化设置 → 个人资料」输入超长昵称后保存，页面顶部会弹出红色提示「nickname 超过上限 100 字符」（该问题已于 #213243 修复）。移动端同一接口、同一错误码，却完全没有提示。

【初步定位建议】

移动端个性化设置（.lui-sheet）与「修改记忆」弹层在提交失败时未处理 errcode != 0 的分支：既未展示 errmsg，也未保留输入。建议与桌面端对齐错误处理逻辑。

## 根因
提供的嫌疑文件（runtime-chat-updater.ts、typings/ai-runtime-log.ts、generic/presets/common.ts、ai-runtime-log/index.vue 的 1005-1084 窗口）均不包含移动端「个性化设置」提交链路，没有任何一处调用 /api/api/runtime/ai/memory/update 或 nickname/occupation 的保存逻辑，因此无法定位到具体行。按现象推断，根因应在移动端个性化设置弹窗的提交处理函数：该函数在 await 保存接口后没有校验 res.errcode（或把非 0 结果当成功、把异常吞掉），无条件执行「关闭弹窗 + 清空表单」，且没有调用 toast/错误提示，所以超限时后端返回 errcode 100003 + data 文案时被静默丢弃。需要查看真正承载该提交的文件：移动端 LUI 个性化设置/记忆详情所在的 Vue 组件（提交 onConfirm / handleConfirm / saveMemory / saveProfile）与 aiApi 中 updateMemory、updateUserProfile 的封装实现，确认 errcode 判定与 toast 调用点。

## 说明
由于缺失包含提交链路的文件内容，无法给出可保证正确的最小补丁。正确的修法是在该提交函数中：先判断响应 errcode !== 0（或 reject 分支），此时用后端返回的 res.data（如「调整指令超过上限 2000 字符」）或国际化兜底文案弹出 toast，并 return，不关闭弹窗、不重置表单；仅在成功分支才关闭弹窗并刷新详情。影响面仅限个性化设置弹窗（记忆/昵称/职业）的失败态表现，成功路径与 UI 结构不变。

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
  "status": "ok",
  "phase": "before",
  "route": "/agent-workspace/",
  "route_source": "ticket",
  "final_url": "<已脱敏链接>",
  "page_title": "<内部标识>-Agent 工作区",
  "plan_note": "AI 根据工单描述编排的复现操作（选择器可能不准，逐步留痕）",
  "actions": [
    "goto /agent-workspace/",
    "wait",
    "click [aria-label*='个性化设置'], button:has-text('个性化设置')",
    "click [class*='memory']:has-text('记忆详情'), button:has-text('记忆详情')",
    "click button:has-text('修改记忆')",
    "wait_for textarea",
    "fill textarea",
    "click button:has-text('确定')",
    "wait",
    "wait"
  ],
  "actions_log": [
    {
      "op": "goto",
      "target": "/agent-workspace/",
      "ok": true
    },
    {
      "op": "wait",
      "target": "",
      "ok": true
    },
    {
      "op": "click",
      "target": "[aria-label*='个性化设置'], button:has-text('个性化设置')",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"[aria-label*='个性化设置'], button:has-text('个性化设置')\")\n"
    },
    {
      "op": "click",
      "target": "[class*='memory']:has-text('记忆详情'), button:has-text('记忆详情')",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"[class*='memory']:has-text('记忆详情'), button:has-text('记忆详情')\")\n"
    },
    {
      "op": "click",
      "target": "button:has-text('修改记忆')",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"button:has-text('修改记忆')\")\n"
    },
    {
      "op": "wait_for",
      "target": "textarea",
      "ok": false,
      "error": "Page.wait_for_selector: Timeout 5000ms exceeded.\nCall log:\n  - waiting for locator(\"textarea\") to be visible\n"
    },
    {
      "op": "fill",
      "target": "textarea",
      "ok": false,
      "error": "Page.fill: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"textarea\")\n"
    },
    {
      "op": "click",
      "target": "button:has-text('确定')",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"button:has-text('确定')\")\n"
    },
    {
      "op": "wait",
      "target": "",
      "ok": true
    },
    {
      "op": "wait",
      "target": "",
      "ok": true
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
1) 个性化设置提交函数统一走一个「校验 errcode → 失败 toast 且保持弹窗 → 成功关闭」的封装，禁止成功/失败共用一个 finally 关闭逻辑；2) 前端对昵称(100)、职业(256)、记忆(2000)增加输入时长度校验与计数器，超限直接禁用「确定」并给出即时红字，减少依赖后端报错；3) 补充单测/用例覆盖 errcode!==0 场景，断言弹窗未关闭且表单值保留；4) 代码评审检查点：凡「提交后关弹窗」的逻辑必须有显式失败分支。

## 关联信息
- 嫌疑文件: packages/<内部标识>-ai/chat-kit/runtime-chat/runtime-chat-updater.ts, packages/<内部标识>-ai/ai-platform/typings/ai-runtime-log.ts, packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/generic/presets/common.ts, packages/<内部标识>-platform/platform/src/feishu/h5-js-sdk-1.5.26.js, packages/<内部标识>-ai/ai-platform/components/ai-runtime-log/index.vue, packages/<内部标识>-form/runtime/components/form-detail.ts, packages/<内部标识>-ai/chat-kit/runtime-chat/agent-app/agent-app.mixin.ts
- 关键词: iPhone, traceId, 个性化设置 → 个人资料, 告诉AI要记住什么或忘记什么..., 调整指令超过上限 2000 字符, errcode, errmsg, f963b890ed6b4acd9947f98..., 记忆更新中，可能需要一点时间，请稍后查看, nickname 超过上限 100 字符, occupation 超过上限 256 字符, <内部标识>/<内部标识>
- 提案状态: invalid；commit: —
- 提案文件: `proposals/rec-b294bfae-20260922-185500.json`
