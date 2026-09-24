# -*- coding: utf-8 -*-
"""Test-only defect fixture.

生产代码里的内置示例工单（原 ONES-1001~1004）已移除：它们曾混入
提案 / 知识库 / 产出物，与真实数据无法区分。测试需要一条确定性的
工单时，从这里显式注入（进程内 monkeypatch 或 /api/run 的 defects 参数），
不再依赖任何全局样本。
"""

TEST_DEFECT = {
    "id": "TEST-1001",
    "title": "流程列表页打开时报 TypeError: Cannot read properties of undefined",
    "description": (
        "用户进入流程列表页面时报错:TypeError: Cannot read properties of undefined "
        "(reading 'name')。\n怀疑是 processItem.template 为 null 时未做校验。"
    ),
    "priority": "P1",
    "status": "open",
}
