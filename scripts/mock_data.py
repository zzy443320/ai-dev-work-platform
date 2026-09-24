"""示例数据的**唯一**出口——只有显式要求演示时才会用到。

内置假工单（former ONES-1001~1004）曾因「隐式兜底」被移除：ONES 拉取一失败它们就
冒出来，混进提案 / 知识库 / 产出物，与真实工单无法区分。现在的规矩：

  * `SAMPLE_DEFECTS` 保持空列表——任何遗留调用路径都退化成「没有工单」并给出明确
    错误，绝不悄悄编造假数据；
  * 想不接 ONES 就看到完整效果，用 `DEMO_DEFECT` + CLI 的 `--demo`
    （工单号固定 `DEMO-1`、标题带「演示」，与真实工单一眼可辨）。

为什么还留一条演示工单：新克隆下来（没有 ONES 令牌、进不去企业内网）如果什么都跑不了，
「下载即可用」就不成立。它**只由 `--demo` 显式注入**，不参与任何自动兜底。
"""
SAMPLE_DEFECTS = []

SAMPLE_NOTICE = (
    "内置示例工单已移除：本项目只处理真实 ONES 工单，"
    "不会再生成任何演示数据（要看效果请显式使用 --demo）。"
)

# 显式演示工单：`python run.py --config config.test.yaml --demo --limit 1` 才会用到
DEMO_DEFECT = {
    "id": "DEMO-1",
    "title": "【演示】流程列表页打开时报 TypeError: Cannot read properties of undefined",
    "description": (
        "用户进入流程列表页面时报错：TypeError: Cannot read properties of undefined "
        "(reading 'name')。\n【复现步骤】1、打开流程列表页；2、接口返回的 "
        "processItem.template 为 null；3、页面报错。\n"
        "【期望】template 为空时展示「未配置流程模板」，而不是整块崩掉。"
    ),
    "priority": "P1",
    "status": "open",
}
