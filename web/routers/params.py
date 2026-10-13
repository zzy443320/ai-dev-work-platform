"""参数元数据端点：把 ``web/params_schema.py`` 那一份定义透出给前端渲染器。

只有一个只读 GET。前端启动时拉一次（见 ``frontend/src/composables/useParamSchema.js``），
五个任务模块的参数控件、分组、范围、默认值全部由它驱动 —— 这样「改文案 / 调范围」
只动 Python 一处，不需要重新理解一遍 Vue 模板。

``problems`` 一并返回：schema 自己写错了（默认值越界、id 撞车、kind 没有渲染器）
在界面上表现为「填了却跑不对」，所以宁可在这里明着带出来，让自检用例与
控制台都能看见，而不是静默渲染出一个骗人的表单。
"""

from fastapi import APIRouter

from web.params_schema import schema_payload, validate

router = APIRouter()


@router.get("/api/params/schema")
async def params_schema():
    return {"ok": True, "modules": schema_payload(), "problems": validate()}
