"""任务型模块的**参数元数据**（schema）—— 六个任务模块的参数在这里定义一次。

为什么要这一层
--------------
改版前，一个参数被拆在三个地方各写一份，谁改了谁不知道：

* 标签、说明文字、占位符 → 只活在 ``frontend/src/views/*.vue`` 的模板里；
* 数值范围 → 模板里写 ``:min="0" :max="5"``，``web/routers/config.py`` 里又硬编码
  一遍 clamp（``max_rounds 1-24`` 那种），两处可以不一致；
* 默认值 → 前端 composable 的 ``ref(...)`` 里，后端不知道。

结果就是「参数展示形态」根本没法统一改：形态是版式问题，但版式需要的信息
（这是必填吗、有没有单位、是不是没动过默认值、该归到哪一组）界面拿不到，
只能靠人肉在模板里摆。本模块把这些信息收成**一份数据**，前端按它渲染，
后端按它校验；改文案、调范围、加分组只动这一个文件。

边界（刻意不做的事）
--------------------
* 这里只描述**展示与合法范围**，不描述「提交到哪个接口、字段叫什么」。
  payload 组装仍然在各模块的 composable 与路由里 —— 把行为层也吸进 schema
  会让一次改版的爆炸半径覆盖到流水线，收益不成比例。
* 扩展能力页（技能 / MCP 表单）不在此列：那是 CRUD 表单而不是运行参数，
  它的字段会随 MCP 传输方式变化，形态另说。
* 配置页（``/api/settings``）的全局设定也还没并进来。第二步再并，
  并的时候 ``kind`` / ``min`` / ``max`` / ``group`` 这套字段名直接复用。

``kind`` 取值
-------------
``text`` / ``password`` / ``textarea`` / ``number`` / ``select`` / ``switch`` /
``segment`` / ``switch-group``。前端 ``components/ParamField.vue`` 按 kind 选控件，
新增 kind 时必须同时改渲染器，否则 ``validate()`` 会在自检用例里报出来。
"""

from __future__ import annotations

# 三档分组。core = 不填就跑不出想要的结果；common = 会明显改变产出；
# advanced = 大多数时候不用动（在参数轨里默认折叠）。
CORE = "core"
COMMON = "common"
ADVANCED = "advanced"
GROUPS = (CORE, COMMON, ADVANCED)

GROUP_LABELS = {CORE: "核心", COMMON: "常用", ADVANCED: "高级"}

#: 技术栈下拉项。Figma 转代码 / 接口联调 / 长任务作业共用同一份，与旧版
#: index.html:377 以及 frontend/src/composables/useTeam.js 的 TEAM_FRAMEWORKS
#: 对齐（迁移完成后以本表为唯一来源）。
#: ⚠ 需求开发（req）2026-10-20 起不再手选技术栈：由后端读仓库自动识别
#: （scripts/stack_detect.py），用户手选的错栈会误导整个流水线。
FRAMEWORKS = [
    "React + TypeScript", "Vue 3 + TypeScript", "React + JavaScript",
    "Vue 2 + JavaScript", "小程序原生", "uni-app",
]

#: 代码测试的框架下拉（含测试库组合，与后端 prompt 里的取值一致）
TEST_FRAMEWORKS = [
    "vitest", "vitest + Testing Library", "jest", "jest + Testing Library",
    "cypress 组件测试",
]


def _f(key: str, id_: str, label: str, kind: str, *, group: str = CORE,
       default=None, required: bool = False, **extra) -> dict:
    """一条参数的完整元数据。``extra`` 放 kind 专属项（placeholder/hint/rows/min/max…）。"""
    field = {"key": key, "id": id_, "label": label, "kind": kind, "group": group,
             "required": required}
    if default is not None:
        field["default"] = default
    field.update({k: v for k, v in extra.items() if v is not None})
    return field


# ------------------------------------------------------------------ 各模块参数
#
# ⚠️ id 沿用改版前的 DOM 契约（#opt-limit / #team-title / #req-figma-mode …）：
#    界面用例是按这些 id 点与填的（见 tests/check_vue_tasks_ui.py、
#    tests/check_layout_ui.py），换形态不换 id，用例才不需要跟着大改。
MODULES: dict[str, dict] = {
    "defect": {
        "title": "运行参数",
        "action": "运行参数",
        "hint": "工单范围与运行方式。点「运行 / 试运行」后主区就是实时日志。",
        "fields": [
            _f("mineOnly", "opt-mine", "只看我负责的", "switch",
               default=True, hint="只拉 ONES 里指派给我的工单"),
            _f("limit", "opt-limit", "最多处理", "number",
               default=4, min=1, max=20, step=1, unit="个",
               hint="一次流水线最多处理多少张工单"),
            _f("skipVerify", "opt-skip-verify", "跳过页面截图", "switch",
               group=COMMON, default=False,
               hint="复现环节不开浏览器：快，但少一份视觉证据"),
            _f("defectId", "opt-defect", "工单 UUID", "text",
               group=COMMON, default="",
               placeholder="填 ONES 工单的 UUID；留空则拉最近工单列表",
               hint="留空 = 拉取最近列表"),
        ],
    },
    "codetest": {
        "title": "测试参数",
        "action": "参数",
        "hint": "路径相对仓库根；「预览文件」只读，不改任何东西。",
        "fields": [
            _f("testFile", "test-file", "目标文件路径", "text", required=True,
               default="", placeholder="src/components/OrderList/index.tsx",
               hint="相对仓库根，后端只读它"),
            _f("testFramework", "test-framework", "测试框架", "select",
               default="vitest", options=TEST_FRAMEWORKS),
            _f("testFocus", "test-focus", "重点关注", "textarea",
               group=COMMON, default="", rows=4, optional=True,
               placeholder="例如：分页逻辑、空数据渲染、提交失败后的状态回滚"),
        ],
    },
    # 自测（2026-10-21）：开发完需求后的自测环节。三条链路各吃一份输入，
    # 共用一次浏览器采证，产出「逐条判定表」而不是代码补丁。
    # 参数刻意都标成可选：三条链路只要有一条有输入就能跑，全空时接口层才报错
    # （校验放在路由里，参数层只描述形态）。
    "selftest": {
        "title": "自测参数",
        "action": "自测参数",
        "hint": "三条链路至少给一条输入：工单里的测试用例、需求文档、Figma 链接。"
                "只读采证，不改仓库。",
        "fields": [
            _f("stIssue", "st-issue", "ONES 工单", "text",
               default="", optional=True,
               placeholder="100003 或工单 UUID",
               hint="从「测试情况」tab 拉关联用例；拉不到可改用手动粘贴"),
            _f("stUrl", "st-url", "本地环境地址", "text",
               default="", optional=True,
               placeholder="http://localhost:3000",
               hint="留空用配置里的页面地址；登录态沿用持久 profile"),
            _f("stRoutes", "st-routes", "检查路由", "text",
               default="/", placeholder="/order/list 或 /admin/#/xxx/yyy",
               hint="要填**真实页面**的完整路径（含子路径与 # 后的哈希路由）；"
                    "只填 / 时常会采到登录页或空白壳"),
            _f("stLanes", "st-lanes", "检查项", "switch-group",
               group=COMMON,
               default={"case": True, "req": True, "figma": True},
               options=[{"key": "case", "id": "st-lane-case", "label": "测试用例"},
                        {"key": "req", "id": "st-lane-req", "label": "需求一致性"},
                        {"key": "figma", "id": "st-lane-figma", "label": "设计稿一致性"}]),
            _f("stCases", "st-cases", "测试用例（手动粘贴）", "textarea",
               group=COMMON, default="", rows=6, optional=True,
               placeholder="一条一行，或按「用例名 / 步骤 / 预期」分段粘贴。"
                           "填了就不再从 ONES 拉，避免两批用例混在一起",
               hint="优先级高于 ONES：ONES 的测试接口各部署形状不同，拉不到时用它兜底"),
            _f("stReqDoc", "st-doc", "需求文档", "textarea",
               group=COMMON, default="", rows=8, optional=True,
               placeholder="粘贴需求单正文（或验收标准），也可以点「上传需求文档」直接传 md / txt / pdf。"
                           "会先拆成可在页面上核对的验收点",
               hint="上传的 PDF / txt 与这里手写的内容会合并（手写的在前）；"
                    "只写「功能正常」这类无法判定的句子会让整条链路退化成无法判断"),
            _f("stFigma", "st-figma", "Figma 设计稿链接", "text",
               group=COMMON, default="", optional=True,
               placeholder="https://www.figma.com/design/xxxx/文件名?node-id=12-34",
               hint="取稿需要 Figma Token（配置页）。比对字号/字重/颜色/宽度，不比位置"),
            _f("stLimit", "st-limit", "最多检查项", "number",
               group=ADVANCED, default=12, min=1, max=20, step=1, unit="项",
               hint="每条链路各自的条数上限；越大越慢，也越容易撞模型上下文"),
        ],
    },
    "apidebug": {
        "title": "联调参数",
        "action": "参数",
        "hint": "接口文档是这份表单里唯一真正需要宽度的输入，其余都是可选项。",
        "fields": [
            _f("apiDoc", "api-doc", "接口文档", "textarea", required=True,
               default="", rows=10,
               placeholder='{"paths": {"/api/v1/list": {...}}} 或直接粘贴后端给的文档',
               hint="Swagger/OpenAPI JSON、Markdown、curl 示例均可"),
            _f("apiBase", "api-base", "后端 Base URL", "text",
               group=COMMON, default="", optional=True,
               placeholder="http://10.0.0.1:8080"),
            _f("apiFramework", "api-framework", "技术栈", "select",
               group=COMMON, default=FRAMEWORKS[0], options=FRAMEWORKS),
            _f("apiContext", "api-context", "现有请求封装", "textarea",
               group=COMMON, default="", rows=6, optional=True,
               hint="粘贴相关代码，生成的调用要和它兼容"),
            _f("apiNotes", "api-notes", "其他约定", "text",
               group=ADVANCED, default="", optional=True,
               placeholder="鉴权方式、错误码约定、埋点要求等"),
        ],
    },
    # 需求开发（2026-10-19 拆出来；2026-10-20 收敛成 Trae 式输入）：
    # 核心输入只剩三样 —— 需求内容 / Figma 链接（可选）/ 自由文本要求。
    # 技术栈由 AI 读仓库自动识别（scripts/stack_detect.py），不再让用户手选；
    # 需求来源并入自由文本。设计稿取稿在运行时自动做，不再依赖隔壁 reqdev。
    "req": {
        "title": "需求参数",
        "action": "需求参数",
        "hint": "需求文档决定判定质量；技术栈由 AI 读仓库自动识别，无需选择。"
                "影响范围留空则由 AI 按需求自行圈定。",
        "fields": [
            _f("reqDoc", "rq-doc", "需求文档 / 需求原文", "textarea",
               default="", rows=10, optional=True,
               placeholder="粘贴需求单正文，或点右上「上传需求文档」直接传 "
                           "md / txt / pdf：要做什么、给谁用、在什么场景下用",
               hint="也支持上传附件（md / txt / pdf，PDF 自动抽取正文）；"
                    "两者都会作为需求依据，只改接口字段/数据映射的需求同样适用"),
            _f("reqFigma", "rq-figma", "Figma 设计稿链接", "text",
               default="", optional=True,
               placeholder="https://www.figma.com/design/xxxx/文件名?node-id=12-34",
               hint="可选。填了会在运行时自动拉取设计稿结构作为视觉依据；"
                    "拉取失败不阻断运行，会记录在自检的「需要确认」里"),
            _f("reqNotes", "rq-notes", "具体要求 / 注意事项", "textarea",
               default="", rows=4, optional=True,
               placeholder="交互细节、不许动的接口、兼容性要求、需求来源、"
                           "想参考的既有页面……想到什么写什么，留空也行"),
            _f("reqDeliver", "rq-deliver", "交付方式", "select",
               group=ADVANCED, default="auto",
               options=[{"value": "auto", "label": "自动完成（能改就直接改）"},
                        {"value": "triage", "label": "只出方案（不动代码）"},
                        {"value": "code", "label": "强制出代码（缺设计稿也先实现）"}],
               hint="自动完成会走「分诊 → 定位代码 → 生成改动 → 自检」四步，产出可采纳补丁"),
            _f("reqScope", "rq-scope", "影响范围", "text",
               group=ADVANCED, default="", optional=True,
               placeholder="src/views/order/** , src/api/**",
               hint="glob，逗号分隔；留空由 AI 判断"),
            _f("reqCodeContext", "rq-context", "现有代码参考", "textarea",
               group=ADVANCED, default="", rows=5, optional=True,
               hint="粘贴相关页面/请求封装，判定与方案会贴着它写"),
        ],
    },
    "reqdev": {
        "title": "生成参数",
        "action": "生成参数",
        "hint": "Figma 取稿方式、需求描述与代码风格约定。设计稿转代码只是需求开发的一条路径。",
        "fields": [
            _f("figmaUrl", "req-figma-url", "Figma 设计稿链接", "text",
               default="", optional=True,
               placeholder="https://www.figma.com/design/xxxx/文件名?node-id=12-34"),
            _f("figmaMode", "req-figma-mode", "取稿通道", "segment",
               default="token",
               options=[{"value": "token", "label": "Token"},
                        {"value": "browser", "label": "浏览器打开"}],
               hint="官方接口只认 Personal Access Token；浏览器通道拿不到图层结构"),
            _f("figmaToken", "req-figma-token", "Figma Token", "password",
               group=COMMON, default="", optional=True, autocomplete="off",
               placeholder="留空则用设置里的",
               # 取稿通道切到「浏览器打开」时这一格没有意义，收起来
               visible_when={"key": "figmaMode", "not": "browser"}),
            _f("reqDesc", "req-desc", "需求描述", "textarea", required=True,
               default="", rows=5,
               placeholder="这个页面/组件要做什么：交互、数据来源、状态与边界情况"),
            _f("reqContext", "req-context", "现有代码参考", "textarea",
               group=COMMON, default="", rows=5, optional=True,
               hint="粘贴相关组件/封装，让生成的代码风格一致"),
            _f("reqFramework", "req-framework", "技术栈", "select",
               group=COMMON, default=FRAMEWORKS[0], options=FRAMEWORKS),
            _f("reqNotes", "req-notes", "其他约定", "text",
               group=ADVANCED, default="", optional=True,
               placeholder="命名规范、目录位置、UI 组件库等"),
        ],
    },
    "team": {
        "title": "作业参数",
        "action": "作业参数",
        "hint": "任务越大越要把「改成什么样」和「哪些不许动」写清楚。",
        "fields": [
            _f("title", "team-title", "任务标题", "text", required=True,
               default="", placeholder="例如：订单列表页从旧架构迁移到新架构",
               hint="会出现在作业记录与产出物标题里"),
            _f("task", "team-task", "任务描述", "textarea", required=True,
               default="", rows=6,
               placeholder="要改什么、改成什么样、哪些必须保持不变（对外接口 / 埋点 / 兼容性）。"
                           "例如：把 src/views 下的 12 个列表页统一迁移到新表格组件，"
                           "接口调用改为 src/api/v2，页面路由与参数保持不变。",
               hint="越大越模糊的任务越要写清目标与不做什么"),
            _f("scope", "team-scope", "改造范围", "text",
               group=COMMON, default="", optional=True,
               placeholder="src/views/order/** , src/api/**",
               hint="glob，逗号分隔；留空由决策官自行判断"),
            _f("framework", "team-framework", "技术栈", "select",
               group=COMMON, default=FRAMEWORKS[0], options=FRAMEWORKS),
            _f("roleOn", "team-role", "参与角色", "switch-group",
               group=COMMON,
               default={"planner": True, "coder": True, "tester": True, "reviewer": True},
               options=[{"key": "planner", "id": "team-role-planner", "label": "决策官",
                         "locked": True, "note": "没有决策官就无法拆解任务，始终参与"},
                        {"key": "coder", "id": "team-role-coder", "label": "编码工程师"},
                        {"key": "tester", "id": "team-role-tester", "label": "测试工程师"},
                        {"key": "reviewer", "id": "team-role-reviewer", "label": "复核官"}]),
            _f("rounds", "team-rounds", "最多返工轮次", "number",
               group=ADVANCED, default=2, min=0, max=5, step=1, unit="轮",
               hint="复核官发现问题后允许整体返工的上限"),
            _f("notes", "team-notes", "其他约定", "text",
               group=ADVANCED, default="", optional=True,
               placeholder="目录规范、必须复用的工具函数、禁止引入的依赖等"),
        ],
    },
}

# ------------------------------------------------------------------ 分档规则
#: 参数条数超过这个值，一行放不下、卡片会把过程区往下顶，改用常驻参数轨
STRIP_MAX_FIELDS = 6
#: 行数达到这个数的文本域算「长文本」（接口文档 10 行 / 需求描述 5 行），
#: 在 strip 里只能两行截断 + 展开，体验不如直接在轨里给宽度
LONG_TEXTAREA_ROWS = 8


def layout_of(module: str) -> str:
    """``strip`` = 就地控件条（参数常驻主区顶部，一格一参数）；
    ``rail`` = 常驻参数轨（布局第三列，分组折叠 + 独立滚动）。

    规则要**可解释**：不是每个模块手挑一个形态，而是「字段多不多、有没有长文本」
    两个客观条件决定，模块加了参数形态会自动跟着变。
    """
    fields = MODULES[module]["fields"]
    if len(fields) > STRIP_MAX_FIELDS:
        return "rail"
    if any(f["kind"] == "textarea" and int(f.get("rows") or 0) >= LONG_TEXTAREA_ROWS
           for f in fields):
        return "rail"
    return "strip"


def schema_payload() -> dict:
    """给前端的完整 schema（``GET /api/params/schema`` 的返回体）。

    每条参数原样透出（含 default / hint / min / max），前端渲染器据此出控件；
    ``layout`` 由分档规则算出来，前端不再自己判断。
    """
    out = {}
    for name, mod in MODULES.items():
        out[name] = {
            "title": mod["title"],
            "action": mod["action"],
            "hint": mod.get("hint", ""),
            "layout": layout_of(name),
            "groups": GROUP_LABELS,
            "fields": [dict(f) for f in mod["fields"]],
        }
    return out


# ------------------------------------------------------------------ 自检
_KINDS = {"text", "password", "textarea", "number", "select", "switch",
          "segment", "switch-group"}


def validate() -> list[str]:
    """返回问题清单（空 = 通过）。给 ``tests/check_params_schema.py`` 当断言用。

    这里查的是**元数据自己会不会骗人**：范围写反、默认值越界、id 重复、
    kind 没有对应渲染器 —— 这些错了，界面上就会以「填了却跑不对」的形式还回来。
    """
    problems: list[str] = []
    seen_ids: set[str] = set()
    for name, mod in MODULES.items():
        keys = {f["key"] for f in mod["fields"]}
        if not mod.get("title"):
            problems.append(f"{name}: 缺 title")
        if not any(f["group"] == CORE for f in mod["fields"]):
            problems.append(f"{name}: 没有任何 core 组参数（参数轨会只剩「常用」）")
        for f in mod["fields"]:
            tag = f"{name}.{f['key']}"
            if f["kind"] not in _KINDS:
                problems.append(f"{tag}: kind={f['kind']} 没有对应渲染器")
            if f["group"] not in GROUPS:
                problems.append(f"{tag}: group={f['group']} 不是 {GROUPS}")
            if f["id"] in seen_ids and f["kind"] != "switch-group":
                problems.append(f"{tag}: id={f['id']} 与其它参数重复")
            seen_ids.add(f["id"])
            if f["kind"] in ("select", "segment", "switch-group"):
                opts = f.get("options") or []
                if not opts:
                    problems.append(f"{tag}: {f['kind']} 没有 options")
                vals = {o if isinstance(o, str) else (o.get("value") or o.get("key"))
                        for o in opts}
                d = f.get("default")
                if isinstance(d, dict):  # switch-group：默认值的键必须是选项键子集
                    bad = set(d) - vals
                    if bad:
                        problems.append(f"{tag}: default 含未知角色 {sorted(bad)}")
                elif d is not None and not isinstance(d, bool) and d not in vals:
                    problems.append(f"{tag}: default={d!r} 不在 options 里")
            if f["kind"] == "number":
                lo, hi = f.get("min"), f.get("max")
                if lo is not None and hi is not None and lo > hi:
                    problems.append(f"{tag}: min={lo} > max={hi}")
                d = f.get("default")
                if isinstance(d, (int, float)):
                    if lo is not None and d < lo:
                        problems.append(f"{tag}: default={d} < min={lo}")
                    if hi is not None and d > hi:
                        problems.append(f"{tag}: default={d} > max={hi}")
            vw = f.get("visible_when")
            if vw:
                if vw.get("key") not in keys:
                    problems.append(f"{tag}: visible_when 引用了不存在的参数 {vw.get('key')}")
                if "switch-group" in [x["kind"] for x in mod["fields"]] and f["kind"] == "switch-group":
                    pass  # 角色组自己就是多控件，不参与可见性联动
            if f["required"] and f.get("optional"):
                problems.append(f"{tag}: 同时标了 required 与 optional")
            if f["required"] and f["group"] != CORE:
                problems.append(f"{tag}: 必填项不在 core 组，检查单/轨折叠会把它藏起来")
    return problems
