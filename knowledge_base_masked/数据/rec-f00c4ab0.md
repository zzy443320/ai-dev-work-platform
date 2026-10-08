---
defect_id: rec-f00c4ab0
title: "【兼容验证】列表不刷新"
category: 数据
priority: 
ai_mode: openai
proposal_id: rec-f00c4ab0-20260922-143747
status: invalid
gate_level: none
commit: 
created: 2026-09-22T14:37:47
updated: 2026-09-24T11:08:01
kb_version: 2
---

# 【兼容验证】列表不刷新

## 现象
提交后列表不刷新，agent-workspace/schedule 页面。

## 根因
提供的四个文件窗口内都不包含「提交成功后重新拉取列表」的代码路径：mobile/pages/schedule/index.vue 只给了 228-287 行（handleCardAction/closeHistory/handleHistoryLocate/openDetail/closeDetail/openCreate），没有编辑器提交回调与列表加载方法（如 loadTasks/fetchList）；lui/pages/schedule/index.vue（PC 端）未提供任何片段；api.ts 只给了 1-60 行的类型定义，看不到请求方法。因此无法定位是「提交回调未触发重新加载」还是「加载方法未 await / 缓存未失效」，不能臆造补丁。需要查看 mobile/pages/schedule/index.vue 中编辑器组件的 @success/@submit/@close 绑定处及其调用的列表加载方法，以及 lui/pages/schedule/index.vue 中对应的提交处理与列表刷新逻辑。

## 说明
当前窗口不足以确定最小改动点。按缺陷现象推测，问题应在「编辑器提交成功后未触发列表重新加载」这一环：在 mobile 与 PC 两个 index.vue 中，编辑器（schedule-editor 之类）提交成功的事件处理里缺少对列表加载方法的显式调用，或调用了但未 await 导致时序问题。补丁应只在该事件回调内追加一次列表加载调用，影响面限于 schedule 页面提交后的刷新时机，不改变接口与数据结构。

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
  "status": "skipped",
  "reason": "按请求跳过页面验证"
}
```

## 复现步骤
1. 打开 `/`
2. （工单未提供更细复现步骤，需补充）

## 预防措施
1) 提交成功回调中统一走同一个 refreshList()/loadTasks() 方法，禁止在多个分支内各自写加载逻辑；2) 为列表加载方法加单测/组件测试，断言 submit 成功后请求次数 +1；3) 在 MR 模板中要求：凡涉及增删改弹窗的页面，必须提供提交成功后的刷新断言或截图。

## 关联信息
- 嫌疑文件: packages/<内部标识>-ai/lui/mobile/pages/schedule/schedule-repeat.vue, packages/<内部标识>-ai/lui/pages/schedule/schedule-repeat.vue, packages/<内部标识>-ai/lui/pages/schedule/api.ts, packages/<内部标识>-ai/lui/mobile/pages/schedule/index.vue, packages/<内部标识>-ai/lui/pages/schedule/index.vue
- 关键词: agent-workspace/schedule, agent-workspace, schedule, agent, workspace, 兼容验证, 兼容, 容验, 验证, 列表不刷新, 列表, 表不
- 提案状态: invalid；commit: —
- 提案文件: `proposals/rec-f00c4ab0-20260922-143747.json`
