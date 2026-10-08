---
defect_id: rec-ba9a11f2
title: "【迭代N】定时任务「不重复」模式时间被静默回退为08:00且日期只读，导致无法创建未来执行的一次性任务（保存成功但永不触发）"
category: 逻辑
priority: 
ai_mode: openai
proposal_id: rec-ba9a11f2-20260923-174859
status: proposed
gate_level: degraded
commit: 
created: 2026-09-23T17:48:59
updated: 2026-09-24T11:08:05
kb_version: 2
---

# 【迭代N】定时任务「不重复」模式时间被静默回退为08:00且日期只读，导致无法创建未来执行的一次性任务（保存成功但永不触发）

## 现象
【测试环境】

agent-workspace/schedule

【测试分支】

develop

【测试账号】

<已脱敏账号凭据>

【前置条件】

环境：test-develop，以 <内部标识> 登录 LUI（/agent-workspace/schedule）；

在定时任务页点「创建」→「手动创建」，把「重复」类型选为「不重复」。

【测试步骤】

将「重复」选为「不重复」，观察日期/时间控件的可编辑性；

在时间框键入一个真实时间（如 23:15），回读控件值确认写入生效；

按 Enter、再失焦，分别回读控件值；

改用浮层点选时/分后失焦，回读控件值；

点「确定」保存，查看接口返回的 timePlan。

【预期结果】

按用例：「人工创建任务时时间边界应正确校验，任务按设置的时间计划执行」。

即用户设置的执行时间应被保存，任务应在该时间触发。

【实际结果】

步骤 1：日期控件  readOnly=true  且固定为当天（2026-09-21），用户无法修改日期；

时间控件初始值固定为  08:00 （readOnly=false，看似可编辑）。

步骤 2：键入确实生效——回读  {"time":"23:15","timeRO":false} ，写入成功。

步骤 3： 按 Enter 后仍为 23:15（保留） ；但 失焦后回退为 08:00 ：

[选不重复后] {"time":"08:00","date":"2026-09-21","dateRO":true}

[键入后]       {"time":"23:15"}   ← 写入生效

[Enter 后]     {"time":"23:15"}   ← 保留

[失焦后]       {"time":"08:00"}   ←  被静默回退

步骤 4：走浮层面板点选「23」时、「16」分并失焦后，回读同样 回退为 08:00 ：

[浮层点选后]   {"time":"08:00"}   ←  被静默回退

即无论键入还是点选，用户设置的时间都会在失焦时被丢弃。

步骤 5：保存接口却 成功返回 ，把 08:00 落库：

POST /api/api/runtime/ai/schedule/create

→ {"errcode":0,"errmsg":"操作成功","data":{...,"status":"ENABLED",

"timePlan":{"triggerType":"ONCE","startTime":"2026-09-21 08:00"},

"nextFireTime":"2026-09-21 08:00:00","createdTime":"2026-09-21 22:16:11"}}

该 startTime 比创建时刻 早 14 小时 ，nextFireTime 是过去时间；

前端「确定」按钮 disabled=false， 全程没有任何校验提示或警告 。

任务卡片上直接显示「下次执行 --」，即该任务永远不会触发。

【结论与影响】

在当天 08:00 之后创建「不重复」任务时， 日期不可改 + 时间被回退为 08:00 ，

两者叠加导致用户 根本无法创建一个未来执行的一次性任务 ：任务保存成功（errcode:0）

但永远不会触发，且界面无任何提示——属静默的数据丢失。

该缺陷同时解释了环境中长期存在的「ONCE + startTime 已过期 + ENABLED」僵尸任务

（库中现存 6 个 ONCE 任务，其中 4 个 startTime 已过期仍为 ENABLED）。

测试后已删除本次创建的临时任务（紫色松鼠看星星），DB 复核任务总数 58（与测试前一致）、残留 0。

复现路径：agent-workspace/schedule → 创建 → 手动创建 → 重复选「不重复」→ 时间键入 23:15 → 失焦（回读变 08:00）→ 确定

【截图】

未提供：该问题关键证据为控件值的即时回读序列（键入23:15→Enter保留→失焦回退08:00）与创建接口返回的timePlan，界面截图只能体现最终08:00这一个静态结果、无法体现回退过程，原文已写入描述正文

## 根因
核心回退逻辑位于未提供的 packages/<内部标识>-ai/lui/pages/schedule/schedule-repeat.vue 的「noRepeat」分支：该分支把日期控件置为 readOnly 并把时间在失焦（含浮层点选后失焦）时重置回默认 08:00，因此用户键入的 23:15 被丢弃；而 packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue 的 canSubmit/submit 只做 name/instruction/date/time 的非空校验，未对 ONCE 计划的执行时间做「必须晚于当前时间」的校验，也没有任何提示，导致被回退成过去时间的 08:00 仍能成功提交（errcode:0，nextFireTime 为过去时间，任务永不触发）。

## 说明
本补丁只能堵住 schedule-editor.vue 侧的一半问题：提交前对 scheduleType==='noRepeat' 的 date+time 做未来性校验，过期即中断提交并给出提示，从而消除「保存成功但永不触发且无任何提示」的静默数据丢失。要彻底修复必须在 schedule-repeat.vue 的 noRepeat 分支去掉日期 readOnly 并在 blur/change 时保留用户输入的时间（该文件未在嫌疑窗口中提供，SEARCH 段无法逐字复制，故不在此编造补丁，需补充该文件后再改）。影响面：仅手动创建/编辑弹窗的 ONCE 计划提交路径，daily/weekly/custom 等其他重复类型与定时任务卡片、详情、接口层均不受影响；需要补充 i18n 文案 languages.aiPlatform.lui.schedule.onceTimeMustBeFuture。

## 影响文件
| 文件 | 补丁块 | +行 | -行 | 状态 | 告警 |
| --- | --- | --- | --- | --- | --- |
| `packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue` | 2 | 21 | 0 | ✅ 可应用 | — |

## 补丁（SEARCH/REPLACE）
```search-replace
<<<<<<< SEARCH packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue
  close(): void {
    this.$emit('close');
  }
=======
  close(): void {
    this.$emit('close');
  }

  /** 「不重复」任务的执行时间必须晚于当前时间，避免保存后永不触发。 */
  validateOnceTime(): boolean {
    const repeat = this.form.repeat;
    if (repeat.scheduleType !== 'noRepeat' || !repeat.date || !repeat.time) {
      return true;
    }
    const planTime = new Date(
      `${repeat.date}T${repeat.time}:00`.replace(/-/g, '/'),
    ).getTime();
    if (!Number.isNaN(planTime) && planTime <= Date.now()) {
      this.$message.error(
        this.$t(
          'languages.aiPlatform.lui.schedule.onceTimeMustBeFuture',
        ).toString(),
      );
      return false;
    }
    return true;
  }
>>>>>>> REPLACE

<<<<<<< SEARCH packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue
    if (this.submitted) return;
    (this.$refs.form as FormModel).validate((valid: boolean) => {
=======
    if (this.submitted) return;
    if (!this.validateOnceTime()) return;
    (this.$refs.form as FormModel).validate((valid: boolean) => {
>>>>>>> REPLACE
```

## 建议 diff
```diff
--- a/packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue
+++ b/packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue
@@ -341,9 +341,30 @@
     this.$emit('close');
   }
 
+  /** 「不重复」任务的执行时间必须晚于当前时间，避免保存后永不触发。 */
+  validateOnceTime(): boolean {
+    const repeat = this.form.repeat;
+    if (repeat.scheduleType !== 'noRepeat' || !repeat.date || !repeat.time) {
+      return true;
+    }
+    const planTime = new Date(
+      `${repeat.date}T${repeat.time}:00`.replace(/-/g, '/'),
+    ).getTime();
+    if (!Number.isNaN(planTime) && planTime <= Date.now()) {
+      this.$message.error(
+        this.$t(
+          'languages.aiPlatform.lui.schedule.onceTimeMustBeFuture',
+        ).toString(),
+      );
+      return false;
+    }
+    return true;
+  }
+
   submit(): void {
     // 防重入：避免保存成功后弹窗关闭动画期间，按钮可能仍可点击
     if (this.submitted) return;
+    if (!this.validateOnceTime()) return;
     (this.$refs.form as FormModel).validate((valid: boolean) => {
       if (valid) {
         this.submitted = true;

```

## 验收闸门
```json
{
  "level": "degraded",
  "ok": true,
  "checks": [
    {
      "name": "skip:packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue",
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
  "route": "/agent-workspace/schedule",
  "route_source": "ticket",
  "final_url": "<已脱敏链接>",
  "page_title": "<内部标识>-Agent 工作区",
  "plan_note": "AI 根据工单描述编排的复现操作（选择器可能不准，逐步留痕）",
  "actions": [
    "goto /agent-workspace/schedule",
    "wait_for button:has-text(\"创建\")",
    "click button:has-text(\"创建\")",
    "click li:has-text(\"手动创建\"), div[role=\"menuitem\"]:has-text(\"手动创建\"), button:has-text(\"手动创建\")",
    "click label:has-text(\"不重复\"), div:has-text(\"不重复\")",
    "fill input[placeholder*=\"时间\"], input[type=\"time\"]",
    "click body",
    "wait",
    "click button:has-text(\"确定\")"
  ],
  "actions_log": [
    {
      "op": "goto",
      "target": "/agent-workspace/schedule",
      "ok": true
    },
    {
      "op": "wait_for",
      "target": "button:has-text(\"创建\")",
      "ok": false,
      "error": "Page.wait_for_selector: Timeout 15000ms exceeded.\nCall log:\n  - waiting for locator(\"button:has-text(\\\"创建\\\")\") to be visible\n"
    },
    {
      "op": "click",
      "target": "button:has-text(\"创建\")",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"button:has-text(\\\"创建\\\")\")\n"
    },
    {
      "op": "click",
      "target": "li:has-text(\"手动创建\"), div[role=\"menuitem\"]:has-text(\"手动创建\"), button:has-text(\"手动创建\")",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"li:has-text(\\\"手动创建\\\"), div[role=\\\"menuitem\\\"]:has-text(\\\"手动创建\\\"), b"
    },
    {
      "op": "click",
      "target": "label:has-text(\"不重复\"), div:has-text(\"不重复\")",
      "ok": true
    },
    {
      "op": "fill",
      "target": "input[placeholder*=\"时间\"], input[type=\"time\"]",
      "ok": false,
      "error": "Page.fill: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"input[placeholder*=\\\"时间\\\"], input[type=\\\"time\\\"]\")\n"
    },
    {
      "op": "click",
      "target": "body",
      "ok": true
    },
    {
      "op": "wait",
      "target": "",
      "ok": true
    },
    {
      "op": "click",
      "target": "button:has-text(\"确定\")",
      "ok": false,
      "error": "Page.click: Timeout 10000ms exceeded.\nCall log:\n  - waiting for locator(\"button:has-text(\\\"确定\\\")\")\n"
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
1) 在 schedule-repeat.vue 增加 noRepeat 分支的单测：键入/浮层点选后 blur 不得重置 time，date 不得 readOnly；2) 为 schedule-editor 的 submit 增加「ONCE 时间必须晚于 now」的单测与伪造时钟用例；3) 创建接口入参做快照断言，禁止 startTime < createdTime 的请求发出；4) 后端/前端同时增加 ONCE+过期+ENABLED 的兜底校验与告警，避免僵尸任务落库。

## 关联信息
- 嫌疑文件: packages/<内部标识>-ai/lui/pages/schedule/schedule-editor.vue, packages/<内部标识>-ai/lui/mobile/pages/schedule/schedule-editor.vue, packages/<内部标识>-ai/lui/pages/schedule/schedule-card.vue, packages/<内部标识>-ai/lui/pages/schedule/schedule-detail.vue, packages/<内部标识>-ai/lui/pages/schedule/schedule-task-summary.vue, packages/<内部标识>-ai/lui/mobile/pages/schedule/schedule-detail.vue, packages/<内部标识>-ai/lui/mobile/pages/schedule/schedule-task-summary.vue, packages/<内部标识>-ai/lui/pages/schedule/schedule-delete-dialog.vue
- 关键词: timePlan, readOnly, timeRO, dateRO, triggerType, startTime, nextFireTime, createdTime, agent-workspace/schedule, api/api/runtime/ai/schedule/create, 2026-09-21, errcode
- 提案状态: proposed；commit: —
- 提案文件: `proposals/rec-ba9a11f2-20260923-174859.json`
