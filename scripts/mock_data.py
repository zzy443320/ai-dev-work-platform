"""Legacy sample-data slot.

The built-in fake samples (former ONES-1001~1004) have been removed on
purpose: they leaked into proposals / knowledge base / artifacts and were
indistinguishable from real work at a glance. This project now only ever
produces data derived from real ONES tickets.

`SAMPLE_DEFECTS` is kept as an empty list so that any legacy call path
degrades into "no defects" (with a clear error) instead of silently
emitting fake tickets.
"""
SAMPLE_DEFECTS = []

SAMPLE_NOTICE = (
    "内置示例工单已移除：本项目只处理真实 ONES 工单，"
    "不会再生成任何演示数据。"
)
