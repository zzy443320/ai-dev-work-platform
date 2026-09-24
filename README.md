# ONES 前端研发助手（提案/产出物 + 人工审批）

面向前端日常研发的一体化 AI 助手，覆盖日常几类任务，全部遵循同一条安全契约：
**AI 只产出「提案 / 产出物」，任何模式下都不直接修改目标仓库 —— 只有人工点「采纳」
才把改动写入工作区（不 commit、不 push），由你自己确认后提交。**
前面四类是有固定输入形态的任务（工单 / 设计稿 / 接口文档 / 目标文件），
最后一种「问答」刻意不套流水线：日常很多时候只是问一句、或者顺手改点代码。

| 模块 | 输入 | 产出 |
|---|---|---|
| 问答 | 一句提问 / 一段报错 / 「帮我改一下」 | 带 `文件路径:行号` 引用回答；涉及改动时附「改动提案」产出物 |
| 缺陷修复 | ONES 工单 | 根因分析 + SEARCH/REPLACE 补丁提案 + 闸门/截图 |
| 需求开发 | Figma 设计稿链接 + 需求描述 | 组件代码产出物（文件 + 实现计划 + checklist） |
| 接口联调 | 接口文档（Swagger/Markdown/curl） | 对接方案 + 请求封装代码 + mock 数据 + 联调清单 |
| 代码测试 | 目标源文件路径 | 可运行的单元测试文件 + 用例清单 |
| 长任务作业 | 大任务描述（重构 / 组件升级迁移）+ 改造范围 | 多子 Agent 协作产出物（计划 + 逐项改动 + 测试 + 复核结论） |

```
ONES ──▶ ①拉取 ──▶ ②定位+AI分析 ──▶ ③生成提案 ──▶ ④闸门/截图 ──▶ ⑤沉淀知识库
                                       │
                                       └── 人工审阅 diff ──▶ 采纳 ──▶ 写工作区（不 commit）
提问 ──▶ AI 只读检索仓库（列目录/读文件/正则）──▶ 带行号引用的回答 ──▶〔要改代码时〕改动提案 ──▶ 产出物 ──▶ 人工采纳 ──▶ 写工作区（不 commit）
Figma ──▶ 拉取设计稿结构 ──▶ AI 生成代码 ──▶ 产出物 ──▶ 人工采纳 ──▶ 写工作区（不 commit）
接口文档 ──▶ AI 联调方案 ──▶ 产出物 ──▶ 人工采纳 ──▶ 写工作区（不 commit）
源文件 ──▶ AI 生成单测 ──▶ 产出物 ──▶ 人工采纳 ──▶ 写工作区（不 commit）
大任务 ──▶ 决策官拆工作项 ──▶ 编码逐项实现 ──▶ 测试补用例 ──▶ 复核收口 ──▶ 产出物 ──▶ 人工采纳 ──▶ 写工作区（不 commit）
              ▲ 全过程中，人可随时追问 / 纠偏 / 重规划 / 暂停 / 跳过 / 终止
```

## 缺陷修复流水线为什么是这个形态

早期版本把模型返回的 `fix_suggestion` 文本当成**整个文件的新内容**直接覆写源文件并 commit。
模型被要求返回"代码片段"，代码却按"整文件"写入，结果是文件其余内容全部丢失。现在改成：

| 旧行为 | 现在的行为 |
|---|---|
| 模型自由文本 → 整文件覆写 | 模型必须输出 SEARCH/REPLACE 块，逐字匹配原文才生效 |
| 跑完就 commit | 只生成提案，人工采纳才 commit |
| 无论改了什么，一律 `goto("/")` 截图当"验证通过" | 按工单路由截图 + console 错误采集，够不着就明说"未验证" |
| 失败只回滚文件，commit 留着 | 分支不符 / 工作区脏 / 提案漂移一律拒绝，写盘后闸门失败则文件与 commit 一起回滚 |
| 重跑导致 INDEX.md 与统计重复膨胀 | INDEX 与 `_stats.json` 由卡片派生，重建幂等 |
| 知识库只有「一缺陷一卡」，同类缺陷沉淀不出共性 | 卡片之上派生**模式层**：同类缺陷聚成一个模式，复发次数与已采纳的复用改法一眼可见 |

## 快速开始

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
playwright install chromium          # 一次性，约 150 MB；不装也能跑，页面验证会标记 skipped

cp config.yaml.example config.yaml   # 填 repo 路径与 gate 命令
.venv/Scripts/python.exe run.py --config config.yaml --mock --limit 4 --skip-verify
.venv/Scripts/python.exe -m web.server   # → http://127.0.0.1:8765
```

没有 `ANTHROPIC_API_KEY` 时走 mock 模式，界面与健康条会明确标出 `AI: mock`——此时根因文字来自
内置模板，只有 SEARCH/REPLACE 是按真实文件生成的占位补丁，用来验证链路，不代表真实分析结论。

## 模型接入：全部由配置决定，不写死任何厂商

传输层在 `scripts/ai_providers.py`，只用 `requests` 直连，不依赖任何厂商 SDK——因此网关返回结构
跟官方不一样时，改配置就能适配，不用改代码、不用装包。

| 配置项 | 作用 |
|---|---|
| `provider` | `anthropic`（Messages）/ `openai`（Chat Completions）/ `custom`（自建或异构网关） |
| `base_url` | 中转站、企业网关、本地 Ollama/vLLM 的地址；官方接入可留空 |
| `endpoint` / `models_path` | 请求路径与模型列表路径，默认按协议填好，可逐字改 |
| `content_path` | **从响应 JSON 里取正文的点号路径**，数字表示数组下标。网关把结果放在 `data.reply` 就填 `data.reply` |
| `auth_style` / `auth_header` | `bearer` / `x-api-key` / 自定义请求头 / query 参数 / 不带凭据（IP 白名单） |
| `max_tokens_field` | 有的端点要求 `max_completion_tokens` |
| `system_role` | 有的网关把 `system` 改名成 `developer` |
| `extra_headers` / `extra_body` | 租户头、网关私有参数（`enable_thinking` 之类） |
| `json_mode` | 是否发 `response_format: {type: json_object}` |
| `proxy` / `verify_ssl` / `timeout` | 代理、自签证书、超时 |

界面上「大模型」分组里可以直接改每一项，并配两个按钮：

- **只校验配置** —— 不发请求，检查必填项与 URL 合法性，并回显将要发出的完整请求（密钥掩码）。
- **测试连接** —— 真实打一次极短请求，回显耗时、模型答复、token 用量。

不通的时候会明确说是哪一类问题：HTTP 状态码、响应不是 JSON（常见于被登录页/风控页拦截）、
还是 `content_path` 取不到字段，并附上响应体片段——中转站排错基本一眼能定位。

`GET /api/ai/providers` 提供 5 个模板（Anthropic 官方 / OpenAI 官方 / 中转站 / 本地推理 / 完全自定义）
作为**起点**，套完每一项仍可逐条修改；模板不会锁死任何字段。

## 三层验收闸门（可信度递减，界面用颜色区分）

1. **explicit** — `config.yaml` 的 `gate.commands` 或界面「验收闸门」里写的命令，逐条真实执行。最可信。
2. **package_json** — 没配命令时，从 `package.json` 的 scripts 里找 `type-check|typecheck|lint|test|build`。
3. **degraded** — 仓库没有 package.json 或没有可识别脚本时，只做括号/引号闭合、`node --check`、
   `ast.parse`。**它只证明代码没被写坏，不证明类型和测试通过**，界面会红字提示。

闸门未通过的提案会标成 `gate_failed`，普通采纳按钮置灰，必须勾选"我已知悉风险"走强制采纳，
且请求里要带 `confirm_risk: true`。

## 采纳前的硬拦截

`CodeFixer.preflight()` 在写盘前检查，任一不满足直接拒绝（提案保持可复审，修好后能重试）：

- 目标路径是 git 仓库（否则无法安全回滚）
- 当前分支 == `repo.branch`（**不会自行切分支**；`branch` 留空则跳过此项）
- 工作区干净（否则 AI commit 会把你的 WIP 一起卷进去）
- 采纳时按**磁盘当前内容**重新匹配补丁，diff 与提案里保存的不一致即判定"提案已失效"

## 长任务作业：多子 Agent 协作 + 人工随时介入

重构、组件升级迁移这类活，单轮对话做不完：工作面大、要拆、要有人盯方向。
这一页把任务派给四个**各有职责**的子 Agent，并把「过程可见」和「随时能插话」做成一等公民。

| 子 Agent | 职责 | 输出契约 |
|---|---|---|
| 🧭 决策官 | 拆工作项、定方案与顺序、定验收标准，中途纠偏重规划 | `items` / `risks` / `checklist` |
| ⌨️ 编码工程师 | 按方案逐项写代码，返回可落盘的完整文件内容 | `files` / `notes` |
| 🧪 测试工程师 | 为每项产出补测试，指出未覆盖的边界与回归风险 | `cases` / `gaps` |
| 🔍 复核官 | 通盘审查一致性、遗漏与风险，给出结论与必须修清单 | `verdict` / `must_fix` |

执行是 **plan → 逐项（编码 → 测试）→ 复核 → 有限轮返工** 的串行流水线；
复核判 `block` 时按 `最多返工轮次`（默认 2）重跑问题项，直到通过或轮次耗尽。
决策官可由界面关掉其余角色，但**它自身始终参与**（没有它就没有工作项）。

### 人怎么介入（这是这个模块的重点）

模型调用是**不可中途打断**的同步请求——硬塞中断只会得到一堆半截输出。所以介入做在
**安全边界**上：每次模型调用前、每个工作项结束时，子 Agent 会签收累积的人工指令。

| 动作 | 语义 | 生效时机 |
|---|---|---|
| 追问 / 纠偏 | 给指定角色（或全部角色）补一句指令 | 该角色下一步开始时注入提示词，标记为**最高优先级** |
| 要求重规划 | 让决策官按你的意见重排工作项 | 下一个工作项边界，重新出 plan |
| 跳过当前项 | 这一项不做（记为 `skipped`），继续往下 | 当前项结束后 |
| 暂停 / 继续 | 停在下个安全边界等你 | 暂停生效后不消费新工作项 |
| 终止 | 立刻收口，已完成的产出仍打包交付 | 立即（不再开新的模型调用） |

界面上每一步都**如实标注**：指令投递后显示「待生效（当前步骤结束后）」，被签收才改成
「已由 XX 收到」；暂停按钮旁写明「在当前步骤结束后的安全边界生效」。跑完了再投递会明确
返回 409，不会假装送达。

### 看板与回放

- **子 Agent 看板**：每个角色一张卡，实时显示它在做什么、计划进度、它产出的文件，
  以及它**自己的逐字思考与输出**（决策与编码分开看，才能判断方向对不对）。
- **实时时间线**：按时间顺序记录全部动作与阶段结论，可按角色筛选、可放大。
  （逐字输出在卡片里，不写入时间线，避免两份重复。）
- **历史作业**：跑完的记录会落盘 `team_runs/<id>.json`，点开可只读回放计划、轨迹与介入记录
  （不含逐字输出——体量太大，不落盘）。

安全契约与其它模块一致：**全程不写目标仓库**。结果汇总成一份 `team` 类型产出物，
人工在「产出物」里审 diff 后点采纳，才写工作区（不 commit、不 push）。

## 问答（第 2 个页签）

不套任何流水线：**直接问，不用先凑齐工单 / 设计稿 / 接口文档**。

- 开着「允许查仓库」时，AI 会自己调用三个**只读**工具去找证据：`repo_list`（列目录）、
  `repo_read`（带行号读文件）、`repo_grep`（正则搜索）。回答里引用代码必须带
  `文件路径:行号`，并且只允许引用工具真实返回过的内容——拿不到就说拿不到。
- 关掉「允许查仓库」就退化成纯聊天，AI 会明说自己看不到仓库，请你把相关代码贴过来。
- 开着「可出改动提案」时，只要你说的是「帮我改 / 加个字段 / 顺便重构」，它会在回答末尾
  附一段改动提案，自动落成**产出物**（类型 `chat`），与其它任务共用「审 diff → 采纳」链路。
  回答里那句「我改好了」永远不会发生：**它没有写文件的权限**。
- 会话一问一答落盘在 `chat_sessions/`，首句提问自动当标题；可切换 / 清空 / 删除，刷新不丢。
  一个会话一个 JSON，直接看、直接删都行。
- 流式输出：边想边出（思考过程可折叠查看），每次工具调用也会实时显示在气泡里。

多轮怎么走：模型需要查仓库时，那一轮只输出一个 `{"action":"call","tool":…,"arguments":{…}}`
JSON；工具结果回灌给它，最多 5 轮；查到够了就给正式回答。轮次用尽仍在请求工具时不会死循环，
会明确告诉你「模型没给出正式回答，可重试」。

用量归属：一次问答算一次 `问答` 类型的调用（`kind=chat`），会计入「统计」页签。

## 用量统计（第一个页签）

打开就是它。每次**真实**模型调用都会自动记一笔账，所以「哪个工单最费 token」「这周比上周多花了多少」
都是账本直接聚合出来的，不是抽样估算，也不用额外埋点。

| 区域 | 看什么 |
|---|---|
| 汇总卡 | 区间内 / 今天 / 本周 / 本月 / 累计：token 合计、调用次数、均值、输入输出拆分 |
| 消耗趋势 | 按天 / 按周 / 按月的堆叠柱（输入 + 输出），悬停看当天明细；没有调用的日子也留刻度，断档一眼可见 |
| 按任务类型 | 缺陷修复 / 长任务作业 / 需求开发 / 接口联调 / 代码测试 各占多少 |
| 按模型 | 环形占比，换过模型就知道钱花在哪 |
| 单任务消耗 | 排行表：哪个工单 / 哪次作业最费 token（长任务作业还会按子 Agent 标注阶段） |
| 最近调用 | 逐条明细：类型 / 阶段 / 模型 / token / 耗时 / 成功失败 |

**口径必须说清楚（界面里也写了）**：

- 网关返回了用量 → 就是真值；**流式响应大多不回用量**（本工具的流式调用占多数），
  此时按字符折算（中文约 1 字 1 token、其余约 3.5 字符 1 token）并打「估算」角标。
  估算值是**量级参考，不是账单**——界面上两者从不混为一谈。
- 调用失败也照记（prompt 确实发出去了、确实烧了 token），但均值只除以成功次数。
- **Mock 调用不入账**：mock 不消耗 token，所以面板上的调用次数就是真实调用次数。
- 账本是 append-only 的 `usage/usage.jsonl`，想核对/清理直接看这个文件。

## 命令行

| 参数 | 含义 |
|---|---|
| `--config PATH` | 配置文件，默认 `config.yaml` |
| `--mock` | 用内置样本代替 ONES |
| `--defect-id ID` / `--limit N` | 只处理某条 / 限制条数 |
| `--skip-verify` | 跳过 Playwright 截图 |
| `--list-proposals` | 列出提案及状态 |
| `--show PROPOSAL_ID` | 终端里打印某个提案的 diff、错误与闸门结果 |
| `--dry-run` | 已废弃，仅保留兼容；任何模式都不写代码 |

审批只在 Web UI 完成，CLI 故意不提供 `--approve`，避免绕过 diff 审阅这一步。

## HTTP API

| 端点 | 说明 |
|---|---|
| `GET /api/health` | 仓库/分支/脏区、闸门层级与命令、AI 模式、待审数量 |
| `GET/POST /api/settings` | 仓库、模型（含中转站全量参数）、ONES、闸门命令、被测应用地址（密钥掩码回显） |
| `GET /api/ai/providers` | 5 个接入模板与鉴权方式候选，仅作填充起点 |
| `POST /api/ai/test` | 用**表单里未保存的**配置试连；`{"dry": true}` 只校验不发请求 |
| `POST /api/ai/models` | 拉取该端点支持的模型列表 |
| `GET /api/chat/sessions` | 问答会话列表（按更新时间倒序，含条数与预览） |
| `POST /api/chat/sessions` | 新建空会话（界面上的「新会话」其实只在首次提问时才落盘） |
| `GET /api/chat/sessions/{id}` | 某会话的完整消息（含每轮的引用工具与产出物 id） |
| `POST /api/chat/sessions/{id}/clear` \| `/delete` | 清空消息（保留会话）/ 删除会话 |
| `POST /api/chat/stream` | **一问一答，SSE 流式**：`stage` / `tool`（只读检索）/ `ai_delta`（思考与正文）/ `done`（回答 + 产出物）/ `fatal`；`{session_id, message, use_repo, allow_patch}` |
| `POST /api/run` | 跑流水线，**只产出提案**，返回 `wrote_any_files: false` |
| `GET /api/proposals[?status=]` | 提案摘要列表 |
| `GET /api/proposals/{id}` | 提案详情：diff、SEARCH/REPLACE、闸门、截图、预检 |
| `POST /api/proposals/{id}/approve` | **唯一会写仓库的端点**；`{force_gate, confirm_risk, note}` |
| `POST /api/proposals/{id}/reject` | 标记拒绝，不动文件 |
| `POST /api/proposals/{id}/undo` | 回滚该提案的 commit（`reset --hard`，HEAD 必须是该 AI commit） |
| `GET /api/stats` `/api/cards` `/api/card/{cat}/{id}` | 知识库派生统计、缺陷卡片 |
| `GET /api/patterns` `/api/pattern/{id}` | 知识库的**模式层**：同类缺陷的共性沉淀（复发次数、复用改法） |
| `GET /api/changelog` | 更新日志（解析项目根 `CHANGELOG.md`，只读；界面右上角「更新」入口的数据源） |
| `GET /api/team/roles` | 四个子 Agent 的角色定义（名称、职责、输出契约），前端看板的填充来源 |
| `POST /api/team/stream` | 跑长任务作业，SSE 实时流：`run_id` / `agent_status` / `agent_delta`（逐字） / `plan` / `item_status` / `item_files` / `review` / `intervention` / `intervention_ack` / `artifact` / `run_finish` / `done` |
| `GET /api/team/runs` | 历史作业摘要列表（含 `running` 标记，由内存总线实时回填） |
| `GET /api/team/runs/{id}` | 某次作业的完整记录：计划、各角色轨迹、事件流、人工介入（只读回放） |
| `POST /api/team/runs/{id}/intervene` | 人工介入（**不写仓库**）：`{kind, agent, text, item}`；作业已结束时明确 409 |
| `POST /api/team/runs/{id}/abort` | 终止（等价于 `kind=stop`，保留已完成部分的交付） |
| `GET /api/artifacts?scope=team` | 长任务作业的产出物，与其余三类共用「审 diff → 采纳」链路 |

提案状态机：`pending` / `gate_failed` / `invalid`（没生成可用补丁）→ `applied` / `apply_failed` / `rejected`。

长任务作业状态机：`planning` → `running` ⇄ `paused` → `done` / `failed` / `aborted`；
工作项状态：`todo` → `doing` → `done` / `failed` / `skipped`。

## 模块

| 文件 | 作用 |
|---|---|
| `scripts/ones_fetcher.py` | ONES OpenAPI，拉列表/详情 |
| `scripts/chat.py` | 问答：会话落盘 + 目标仓库只读检索（3 个工具）+ 多轮循环 + 改动提案抽取 |
| `scripts/analyzer.py` | 关键词抽取 → `git grep -n` 定位 → 按锚点开窗读文件 → 拼 prompt |
| `scripts/ai_providers.py` | 配置驱动的传输层：三种协议、任意 base_url/路径/鉴权/响应取值路径 |
| `scripts/ai_model.py` | 提示词→结构化结果的契约（`complete`）与文本模式（`complete_text`，问答用），以及诚实标注的 mock；账本记录与推理截断重试共用 `_call_raw` |
| `scripts/patch_engine.py` | SEARCH/REPLACE 解析与匹配（精确 → 空白容错），产出内存态补丁 + unified diff |
| `scripts/gate.py` | 三层验收闸门 |
| `scripts/fixer.py` | `preview()` 不写盘；`apply()` 仅从已批准提案执行，失败回滚文件与 commit |
| `scripts/verifier.py` | 按工单路由截图、采集 console/pageerror、前后像素 diff |
| `scripts/proposal.py` | 提案存储与状态机 |
| `scripts/kb.py` | 缺陷卡：一个工单一张；INDEX 与统计由卡片派生重建 |
| `scripts/kb_patterns.py` | 模式卡：把同类缺陷聚成「模式」，沉淀共性根因与可复用改法 |
| `scripts/changelog.py` | 更新日志解析：`CHANGELOG.md` → 结构化条目（容错优先，格式见文件头说明） |
| `scripts/team_roles.py` | 四个子 Agent 的角色定义（系统提示 + JSON 输出契约 + 人工指令注入） |
| `scripts/team_bus.py` | 人工介入总线：投递 → 安全边界签收；暂停/终止/跳过/重规划都在这里收敛 |
| `scripts/team_store.py` | 作业记录落盘（`team_runs/<id>.json`）：只存关键事件，逐字输出不落盘 |
| `scripts/team_run.py` | 编排器：plan → 逐项编解码 → 复核 → 有限轮返工 → 汇总产出物 |
| `scripts/pipeline.py` | 编排 + `approve/reject/undo` |
| `web/server.py` | FastAPI 与静态资源 |

## 测试

```bash
.venv/Scripts/python.exe tests/test_safety.py            # 6 个后端安全场景，用临时 git 仓库
.venv/Scripts/python.exe tests/test_relay_transport.py   # 假中转站：非标准路径/鉴权/响应结构跑通全链路
.venv/Scripts/python.exe tests/test_browser_e2e.py       # UI 采纳 → 写工作区 → 撤销复原（需 mock 仓库实例）
.venv/Scripts/python.exe tests/test_browser_guards.py    # gate_failed 强制采纳勾选、脏区横幅
.venv/Scripts/python.exe tests/test_browser_ai_config.py # 在界面上配好一个非标准中转站并验证

# 长任务作业：离线逻辑 / HTTP 层 / 界面三层各一份，改这块三个都要跑
.venv/Scripts/python.exe tests/check_team.py                          # 48 项：编排、总线、返工、产出物、不写仓库
.venv/Scripts/python.exe tests/check_team_api.py http://127.0.0.1:8765 # 34 项：SSE 收尾、介入投递与 ack、记录回看
.venv/Scripts/python.exe tests/check_team_ui.py                        # 界面接线：看板、跑通一次、控件回收、只读回放

# 用量统计：离线聚合 / 界面各一份（界面那份自带临时账本 + 临时服务实例，不碰真实数据）
.venv/Scripts/python.exe tests/check_usage.py        # 59 项：估算、账本容错、上下文归属、分桶、聚合、真实调用链
.venv/Scripts/python.exe tests/check_usage_ui.py     # 50 项：默认页签、卡片与 API 一致、各类图表、粒度/区间切换与持久化

# 问答：离线逻辑 / 界面各一份（界面那份自带临时设置 + 临时服务实例，设置/会话/产出物/账本/仓库全在临时目录）
.venv/Scripts/python.exe tests/check_chat.py         # 66 项：会话落盘、路径越界拒绝、三个只读工具、工具调用解析、提案抽取、多轮循环、mock
.venv/Scripts/python.exe tests/check_chat_ui.py      # 37 项：页签位置、空态、流式回答、会话切换/清空/删除、改动提案 → 产出物 → 弹窗、开关记忆、Enter 发送
```

后三个需要 `web.server` 已在 8765 端口运行、`playwright install chromium` 已装、以及
`config.test.yaml` 指向的 mock 仓库处于 `init` 干净状态。三个脚本自带清理，浏览器测试会临时
把 AI 切成 Mock（见 `tests/settings_state.py`），不受环境里真实密钥配置影响。
`check_team_ui.py` 与 `tests/settings_state.py` 一样把地址写死为 8765，临时实例跑不了它。

⚠ **`test_browser_e2e.py` / `test_browser_guards.py` 会真的点「采纳 / 强制采纳」，也就是真的写目标仓库。**
它们因此带一道硬闸门（`tests/server_guard.py`）：先比对服务配置的仓库路径，不是你期望的那个就打印
"服务指向 A、期望 B"并直接跳过（退出码 2），**不会**执行任何写操作。所以别拿它们去跑指向真实项目的
实例——那样测试补丁会落进真实工作区，而且断言还会全红（它们只看 mock 仓库）。

这两道闸门加在**切 Mock 模式之前**，所以「跳过」是零副作用的：不写提案、不碰设置。早期版本先切 Mock
再判仓库，进程被外部掐断时清理代码不执行，工具就会留在 Mock 模式、验收命令还被换成一条必然失败的
lint——跑完这类用例，建议顺手看一眼 `/api/health` 的 `ai_mode` 与「扩展」页里的闸门命令。

界面用例点原生 `<select>` 一律用 `tests/ui_select.py` 的 `pick_select()`，不要用 Playwright 的
`select_option()`：原生下拉已被自绘组件隐藏，后者必然超时。

**测试隔离靠环境变量**：`web/server.py` 的全部数据目录与配置文件都能被覆盖——`SETTINGS_FILE`、
`CHAT_DIR`、`ARTIFACT_DIR`、`USAGE_DIR`、`REPO_PATH`。自检用例起的临时实例会把这五个全部指向
临时目录，所以它们**读不到你的真实配置、也写不进你的真实目录**（`check_chat_ui.py` 就是这么做的，
可以照抄它的 `start_server()`）。给 `web/server.py` 新增落盘目录时，请沿用这个约定加一个环境变量开关。

## 产物目录

```
proposals/          提案 JSON（含 diff、闸门、决策记录）——已 gitignore
artifacts/          需求开发/接口联调/代码测试/长任务作业/问答的产出物——已 gitignore
chat_sessions/      问答会话（一个会话一份 JSON，含每轮引用的工具与产出物 id）——已 gitignore
team_runs/          长任务作业记录（计划、各角色轨迹、人工介入；不含逐字输出）——已 gitignore
screenshots/        before/after 截图——已 gitignore
knowledge_base/     知识库两层：<分类>/缺陷卡 + _patterns/模式卡 + INDEX.md + _stats.json——已 gitignore（知识库沉淀的是你自己项目的缺陷数据，属私有业务数据，请勿公开）
ui_settings.json    界面配置，含密钥——已 gitignore，切勿提交；不创建也能启动（自动生成默认配置），可参考 ui_settings.example.json
```

## 已知限制

- mock 模式的 SEARCH/REPLACE 只对"未做空值校验"这一类缺陷生成真实可套用补丁，其余分类返回空补丁（提案记为 `invalid`），这是刻意的——宁可不改，也不要猜。
- degraded 闸门的括号/引号检查不解析正则字面量，极端情况下可能误报；它只是最后一道兜底，不能替代真实构建。
- 像素 diff 需要 `before` 基线，而基线只能在采纳时才有意义，因此首次采纳的对比常是 `no_baseline`；重跑同一条工单即可拿到基线。
- 单进程串行，`/api/run`、`/api/tasks/run`、`/api/team/stream` 与审批共用一把运行锁，避免并发写同一个仓库；同一时刻只能有一个作业在跑。**问答不占这把锁**（它只读仓库，与流水线可以并行），但问答自己也有一个占用标记，同一时刻只允许一条提问在跑，避免重复提交把会话写乱。
- 长任务作业的介入**不在模型调用中途生效**：模型一次请求一旦发出就无法打断，指令在下一个安全边界（每次模型调用前 / 每个工作项结束时）签收。界面已如实标注，不要把它当成实时抢占。
- 长任务作业的逐字思考/输出只在 SSE 流里，不落盘；关掉页面后只能从 `team_runs/` 回放非逐字的部分。
- `team_runs/` 里的记录会一直累积，目前没有自动清理，定期手动删除即可。
- 知识库里混着两条**测试用假工单**（`COMPAT-TEST-1`、`STREAM-TEST-1`，标题带【兼容验证】/【流式验证】），是早期 `test_relay_transport.py` / `check_live_stream.py` 把提案写进真实 `proposals/` 与 `knowledge_base/` 留下的。它们**不能直接删**：`tests/check_kb_patterns.py` 直接对着真实知识库断言「同一处缺陷的不同工单（含验证用假工单）归入同一模式」，删了这条断言就红。要彻底清掉，得先让该用例自己造 fixture、不再依赖生产数据。
- 手工清理知识库残留时，`INDEX.md`、`_stats.json`、`_patterns/` 都是**派生视图**：删掉提案 JSON 与缺陷卡后必须跑一次 `python scripts/rebuild_kb_cards.py` 重刷，否则索引里还留着已删工单；无人引用的模式卡会在重刷时被自动剪掉。
- `test_browser_ai_config.py` 会对本地假中转站发**真实 HTTP 请求**（就是它要验证的能力），所以它的连通性测试会如实进账本（`kind=ai_test`，界面显示「连通性测试」）。看到模型名是测试用的 `gw-claude-pro` 不用奇怪，那是用例留下的两条记录。账本是 append-only 的，不做事后篡改。
- 用量统计里标了「估算」的数字来自字符折算，只能看量级；要让统计更准，就用会回 usage 的模型/网关（非流式调用通常都会回）。
- `usage/usage.jsonl` 一行一次调用、会持续累积（一个月几百次调用也就几百行，体量可忽略），目前没有自动清理与归档，直接删文件即清零。
- 问答里**只读工具返回的文件与行号是真的，但模型写在回答里的行号不做二次校验**，可能抄错。要严格核对就看气泡里「查了仓库 N 次」展开后的原始工具结果（工具名 + 参数 + 返回字符数），那才是事实来源。
- 问答的轮次上限是 5 轮：需要反复检索的超大问题可能查不全，此时回答会明确说明「模型仍在请求工具」，而不是硬编一个答案。
- 问答的逐字输出只在 SSE 流里，不落盘；刷新后会话里留下的是最终回答 + 「查了仓库 N 次」的摘要，看不到逐字回放（与长任务作业同一取舍）。
- 会话历史送进模型时有上限（最近 24 条、约 24000 字符，单条 6000 字符），很长的会话里早期细节会被截掉——需要它记住的东西建议在当轮复述一遍。
- `chat_sessions/` 会一直累积，没有自动清理，直接删文件即可（界面上的「删除会话」也是删文件）。
