# ONES 前端研发助手

面向前端日常研发的一体化 AI 助手。核心安全契约：**AI 只产出「提案 / 产出物」，不直接修改目标仓库——只有人工点「采纳」才把改动写入工作区（不 commit、不 push），由你自己确认后提交。**

## 功能

| 模块 | 输入 | 产出 |
|---|---|---|
| 问答 | 一句提问 / 一段报错 | 带文件行号引用的回答；要改代码时附改动提案 |
| 缺陷修复 | ONES 工单 | 根因分析 + SEARCH/REPLACE 补丁提案 |
| 需求开发 | Figma 设计稿 + 需求描述 | 组件代码 + 实现计划 + checklist |
| 接口联调 | 接口文档（Swagger/Markdown/curl） | 对接方案 + 请求封装 + mock 数据 |
| 代码测试 | 目标源文件 | 可运行的单元测试文件 |
| 长任务作业 | 重构 / 迁移等大任务 | 多子 Agent 协作产出（计划 + 逐项改动 + 测试 + 复核） |

## 亮点

- **人工审批兜底**：所有改动先出提案，审 diff 后点采纳才落盘；写盘前有硬拦截（分支一致、工作区干净、补丁与当前内容重新匹配），失败可整体回滚。
- **SEARCH/REPLACE 补丁引擎**：模型输出逐字匹配原文的补丁块，杜绝"整文件覆写丢内容"。
- **三层验收闸门**：自定义命令 → package.json scripts → 语法兜底，未通过的提案默认置灰，强制采纳需显式确认风险。
- **长任务多 Agent 协作**：决策官 / 编码 / 测试 / 复核四个子 Agent 串行流水线，支持追问、纠偏、重规划、暂停、跳过、终止，全程 SSE 实时可见。
- **知识库沉淀**：一个缺陷一张卡，同类缺陷自动聚成「模式」，复发次数与可复用改法一目了然。
- **模型接入全配置化**：Anthropic / OpenAI / 中转站 / 本地 Ollama/vLLM，改配置就能接，不写死厂商、不用装 SDK。
- **用量统计**：每次真实调用自动记账，按天/任务类型/模型聚合，哪个工单最费 token 一眼可见。
- **界面操作**：Web UI 覆盖全部流程（审提案、看板、回放、配置），CLI 故意不提供绕过审阅的审批入口。

## 环境要求

- Python 3.10+
- 可选：Playwright Chromium（页面截图验证；不装也能跑，验证会标记 skipped）
- AI：任一 OpenAI/Anthropic 兼容端点（官方 API、中转站或本地推理均可），界面或配置文件里填地址与密钥
- ONES：接入真实工单需要 ONES 地址与令牌（没有也能先跑演示模式）

## 快速开始

```bash
# 1. 安装依赖
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/playwright install chromium   # 可选

# 2. 不接 ONES 先看效果：造一个 mock 目标仓库，跑一条演示工单
.venv/Scripts/python.exe tests/make_mock_repo.py
.venv/Scripts/python.exe run.py --demo --limit 1

# 3. 开 Web 界面审阅 diff → 采纳
.venv/Scripts/python.exe -m web.server      # → http://127.0.0.1:8765

# 4. 接自己的项目：复制配置模板，填仓库路径 / ONES / 模型 / 验收命令
cp config.yaml.example config.yaml
.venv/Scripts/python.exe run.py --config config.yaml --limit 3
```

- 没有 ONES 令牌：`--demo` 注入一条固定演示工单，走与真实工单相同的流水线。
- 没配模型密钥：走 mock 模式，界面会明确标出 `AI: mock`，只用于验证链路。
- 端口默认 8765，可用 `PORT` 环境变量覆盖。

## 测试

```bash
.venv/Scripts/python.exe tests/test_safety.py   # 后端安全场景（纯离线）
.venv/Scripts/python.exe tests/check_team.py    # 长任务作业离线逻辑
.venv/Scripts/python.exe tests/check_chat.py    # 问答离线逻辑
```

`tests/` 自包含：纯离线用例什么都不用准备；界面类用例自带临时实例与脱敏夹具，不依赖私有数据。

## 已知限制

- 单进程串行，同一时刻只有一个写仓库类作业在跑（问答只读仓库，可并行）。
- 流式响应大多不回 token 用量，统计里标「估算」的数字是字符折算，仅供量级参考。
- 长任务的介入指令在安全边界（每次模型调用前 / 每个工作项结束）生效，不是实时抢占。
