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

`tests/` 下的用例是**脚本式**的（`check()` 打印结果 + `sys.exit(1)`），本身不是 pytest 用例。
pytest 只当收编器：`tests/suite_pytest.py` 用子进程跑筛过的安全子集并按退出码判定，
收集范围由 `pyproject.toml` 的 `python_files` 收窄到一个文件（不这么做的话，默认收集会把
`test_*.py` 在导入阶段全部跑完并抛 SystemExit）。

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest                 # 安全子集，约 1 分钟，纯离线
.venv/Scripts/python.exe -m pytest -k origin       # 只跑同源闸门
```

想手工跑单个用例也完全可以（不依赖 pytest）：

```bash
.venv/Scripts/python.exe tests/test_safety.py      # 采纳/撤销/闸门/漂移 六场景
.venv/Scripts/python.exe tests/check_origin_guard.py  # 本机同源闸门
.venv/Scripts/python.exe tests/check_write_guard.py   # 写盘护栏
.venv/Scripts/python.exe tests/check_kb_export.py     # 知识库脱敏与泄密自检
.venv/Scripts/python.exe tests/check_vue_all.py       # 界面总入口（见下方前置条件）
```

**`pytest` 子集不含界面用例**：`check_vue_*.py` / `test_browser_*.py` 需要 Playwright 与
真实浏览器，整套 5–10 分钟，所以没并进去。跑它们的前置条件是三条：
`playwright install chromium`、先 `npm run build`（否则 `GET /` 返回 503）、
以及 `test_browser_*.py` 还需要服务已起在 8765 并准备好 mock 仓库。
这些用例自包含：纯离线的什么都不用准备，界面类自带临时实例与脱敏夹具。
会真写目标仓库的浏览器用例，第一行先过 `tests/server_guard.py` 的 `require_repo()`
（比对 `/api/health` 报的仓库，不符就在任何写操作之前中止）；
`tests/repo_guard.py` 是另一件事，只服务 `check_locate_regression*.py` 的私有仓依赖跳过。

## 前端（Vite + Vue 3）

界面已从「无构建步骤的原生 JS」**整体迁到 Vite + Vue 3**。旧的 `index.html` / `app.js` /
迁移期的 `/v2` 别名都已删除，`GET /` 直接返回 Vue 产物 —— **只有一份实现**。

```bash
cd frontend
npm install
npm run build        # = clean + vite build；产物落到 web/static/{v2.html,vue/}
npm run dev          # 开发模式（接口自动转发到 127.0.0.1:8765）
```

- 入口文件名仍是 `v2.html`（历史名，避免动后端与既有用例的选择器）；`GET /` 由后端直接返回它。
- **构建必须走 `npm run build`**（先 `clean` 再 build）。产物是带 hash 的文件名，
  直接 `vite build` 会在 `web/static/vue/` 里堆下旧 hash，`check_vue_migration.py` 会因此报错。
- 构建产物**不入库**（见 .gitignore）；只 clone 后端会拿到 503 提示「前端尚未构建」，
  跑一次 `npm run build` 即可。
- Vue 版直接复用 `web/static/style.css`（手写、共享、`clean` 脚本刻意不碰它）。
- 自检：`tests/check_vue_all.py` 是总入口 —— 拉起临时实例后跑完 Vue 系列用例 +
  仍对唯一实现成立的既有界面用例，确认零回归。

### 迁移已完成的约定（供后续维护参考）

1. 视图组件在 `frontend/src/views/<X>Pane.vue`，挂到 `App.vue` 的 `#pane-<x>`。
2. **class / id 一律沿用旧版**（`#usage-cards`、`.panel-head` 等）—— style.css 与既有 Playwright 用例才能直接命中。
3. 跨页签复用的能力在 composable：折叠状态 `useFold`（localStorage 键 `panel-fold-state`）、
   提示条 `useToast` —— 与旧版**同键同格式**。
4. 格式化函数统一放 `frontend/src/utils/format.js`。
5. 图表继续手绘 SVG（内网离线可用），不引图表库。
6. 会写目标仓库的浏览器用例，第一行先过 `tests/server_guard.py` 的 `require_repo()`
   闸门，跳过路径零副作用（见上文「测试」）。

## 知识库的备份与分享

`knowledge_base/` 是这条流水线唯一会随时间增值的资产，但它含真实工单数据、**不入库**
（见 .gitignore）——误删或换机器就归零。三条命令兜底：

```bash
.venv/Scripts/python.exe run.py --backup-kb    # 整份打成带时间戳的 zip 放 kb_backups/（也不入库）
.venv/Scripts/python.exe run.py --export-kb    # 脱敏导出到 knowledge_base_masked/（可提交）
.venv/Scripts/python.exe run.py --audit-kb     # 比对原件确认副本零残留（有残留退出码 1）
```

脱敏按规则洗内网链接与裸域名、工单记录号、绝对路径、账号凭据、邮箱与迭代号；两类词表放在
`knowledge_base/` 下（都不入库）：`mask_terms.txt` 点名要额外挡的内部标识，`keep_terms.txt`
捞回被形状规则误伤的代码线索。**导出后一定要跑 `--audit-kb`**：规则是启发式的，
「≥12 位大小写+数字混合」这种形状分不开工单号与 `getUserItems2`，所以默认保守多洗、
由白名单回捞，漏没漏只能靠比对原件知道。

## 已知限制

- 单进程串行，同一时刻只有一个写仓库类作业在跑（问答只读仓库，可并行）。
- 流式响应大多不回 token 用量，统计里标「估算」的数字是字符折算，仅供量级参考。
- 长任务的介入指令在安全边界（每次模型调用前 / 每个工作项结束）生效，不是实时抢占。
