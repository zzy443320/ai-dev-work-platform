# 更新日志

> 记录每次更新**改了什么、怎么改的、什么时候改的、影响是什么**。
> 界面右上角的「更新」入口读的就是这个文件，改完保存、刷新页面即可看到。

<!--
写一条新日志的格式（按时间顺序 **追加到文件末尾**）：

    ## 2026-09-24 10:30 · 新增 · 一句话说清这次改了什么
    - 内容：用户视角能感知到的变化
    - 做法：具体怎么实现的，踩过什么坑
    - 文件：web/server.py、web/static/app.js
    - 影响：需重启后端 / 刷新页面即生效 …

头部三段用 `·` 分隔：日期（必填）、时间（可省）、标签（取自 新增/改进/修复/优化/性能/重构/文档/安全）。
正文四段键名固定，中英文冒号都认；一段写不下就换行缩进两格继续写。
分点写法：带圈序号（如"① 前端渲染…；② 说明条…"）界面会自动各占一行，连着写没关系；
但正文里不要出现「①-⑳」这类字面序号区间或单独的序号举例（会被误拆成碎行），用「带圈序号」等文字表述代替。
若要写示例，请放进代码围栏或 HTML 注释（像这段一样），不会被解析成日志条目。
-->

## 2026-09-20 18:00 · 新增 · 流水线 MVP 落地

- 内容：缺陷修复五阶段闭环首次跑通——拉取 ONES 工单 → AI 定位嫌疑文件 → 生成 SEARCH/REPLACE 提案 → 闸门校验 → 知识库沉淀。
- 做法：按阶段拆成独立模块（`scripts/`），AI 集成做成可插拔：有 Key 走真实模型，没 Key 走内置关键词 mock，便于离线开发。修复阶段只做本地提交、绝不 push；dry-run 模式跳过全部写入。
- 文件：run.py、scripts/analyzer.py、scripts/fixer.py、scripts/pipeline.py、scripts/kb.py
- 影响：首次落地，仓库路径参数化，不写死任何目标仓库。

## 2026-09-21 10:20 · 修复 · 启动脚本双击一闪而过

- 内容：双击 bat 启动脚本窗口闪退，服务起不来。
- 做法：bat 里写了中文注释并带 `chcp 65001`，在 GBK 代码页下解析失败。改为纯 ASCII 内容、去掉 chcp，重命名为 `start_web.bat`；桌面启动器改为自包含脚本（自己 cd 到项目目录并用 venv 解释器启动）。
- 文件：start_web.bat
- 影响：约定——Windows bat 内容必须纯 ASCII，中文注释会导致解析失败。

## 2026-09-21 11:40 · 改进 · 大模型配置抽成独立弹窗

- 内容：侧边栏不再塞整块模型配置，只留一张摘要卡（模式 / 协议 / 模型 / 地址 / Key 状态），点开才是完整配置。
- 做法：新增独立弹窗与摘要渲染函数，摘要数据复用已缓存的服务健康信息；弹窗底部提供「测试连接 / 只校验配置 / 保存配置」三个动作。
- 文件：web/static/index.html、web/static/app.js、web/static/style.css
- 影响：纯前端，刷新页面即生效。

## 2026-09-21 13:05 · 修复 · 下拉框选项在暗色主题下几乎不可读

- 内容：暗色主题下原生下拉框展开后是白底 + 浅色文字，选项看不清。
- 做法：先试 `color-scheme` 与给 `option` 设背景变量，但浏览器对 `option` 样式支持有限，弹层始终无法与主题统一。最终改为自绘下拉组件：隐藏原生控件，插入触发器按钮 + 全局共享面板（固定定位挂在 body，避免被弹窗滚动容器裁剪），选值后回写原生控件并派发 change 事件以兼容既有逻辑。点击选项、点外部、Esc、缩放、滚动均可关闭。
- 文件：web/static/app.js、web/static/style.css
- 影响：页面全部原生下拉框外观统一由自绘组件接管。

## 2026-09-21 14:10 · 修复 · 拉取列表后模型名选不出来

- 内容：点「拉取列表」拿到可用模型后，输入框里选不中。
- 做法：原生 datalist 在各浏览器表现不一致，直接废弃，改为自绘建议面板（复用下拉组件样式）：拉取成功、输入框聚焦、继续输入时弹出，按关键字过滤，点击填入。
- 文件：web/static/app.js
- 影响：同时修掉「测试结果框关不掉」的问题（标题栏补关闭按钮）。

## 2026-09-21 15:00 · 改进 · 采纳语义改为「只写工作区、不再提交」

- 内容：采纳提案不再产生本地提交，改为直接写工作区文件；撤销按备份恢复。
- 做法：去掉采纳流程里的 `git add/commit`，改为返回各文件原始内容作为备份；撤销时先校验文件当前内容与当初写入的一致，被人工改过则拒绝覆盖并提示；脏工作区不再阻断采纳，只做标记；前端所有「本地 commit」表述同步清理。
- 文件：scripts/fixer.py、scripts/pipeline.py、web/static/app.js
- 影响：安全契约变更——工具永不提交，只写工作区。此前以提交方式采纳的旧提案，撤销需手工 `git revert`。

## 2026-09-21 15:40 · 新增 · 「试运行」能力

- 内容：不必卡在审批环节，也能单独试跑「拉取工单 + AI 定位」，先看这套工单能不能被定位到。
- 做法：新增只读探针——只拉取与分析，不建提案、不跑闸门、不写任何文件；工单拉取失败直接报错，不静默回退到 mock（否则会让人误以为真实链路通了）。接口复用运行锁防并发。
- 文件：scripts/pipeline.py、web/server.py、web/static/app.js
- 影响：排查「到底是拉取坏了还是定位坏了」有了独立入口。

## 2026-09-21 16:20 · 新增 · 「只看我负责的」过滤

- 内容：默认只拉负责人是自己的工单，不再被整个团队的工单淹没。
- 做法：服务端按当前用户过滤 + 客户端兜底；当前用户身份从登录接口或用户信息接口获取；若返回数据里根本没有负责人字段，明确报错而不是静默放开成全部。前端运行面板加开关，默认勾选。
- 文件：scripts/ones_fetcher.py、web/server.py、web/static/index.html
- 影响：运行面板新增一个开关。

## 2026-09-21 17:30 · 修复 · ONES 工单拉取接口全废，改用新通道

- 内容：原方案用的两个工单接口一个返回格式错误、一个 404，工单一律拉不到。
- 做法：逐条实测排掉所有 REST 路径后，改用团队任务查询通道。该通道服务端不支持过滤参数，一次返回整个团队数万条（约 6–15 秒 / 15–30MB），因此改为全部客户端过滤：项目 → 负责人 → 工单编号 → 天数 → 排序 → 限流。详情不再单独请求，而是从列表结果里按 编号 / UUID / 团队-编号 匹配。
- 文件：scripts/ones_fetcher.py
- 影响：拉取耗时与流量显著上升，但这是当时唯一可用的通道；超时阈值相应放宽。

## 2026-09-21 17:50 · 新增 · 升级为「ONES 前端研发助手」并新增三大任务模块

- 内容：从「缺陷自动修复」升级为研发助手，新增三个页签：需求开发（Figma 转代码）、接口联调、代码测试。
- 做法：新增 Figma 拉取模块（只读 + 结构树简化）、三个任务的 AI 编排模块（结构化契约 + mock 兜底）、产出物存储与写回模块（写工作区带备份、可撤销、不提交）。服务端新增仓库文件只读预览、Figma 拉取、任务执行、产出物审批等接口；前端加页签导航与产出物列表/详情弹窗。
- 文件：scripts/figma_fetcher.py、scripts/task_modules.py、scripts/artifact.py、web/server.py、web/static/index.html
- 影响：产出物采纳同样只写工作区、不提交、可撤销；闸门未通过需强制确认才写入。

## 2026-09-22 10:15 · 修复 · 「无法生成补丁」——定位管道失效（第一轮）

- 内容：填入工单能拉到信息，但结果永远是「无法生成补丁」。
- 做法：判定为定位管道 bug 而非刻意护栏。修正关键词分层与候选文件排序，让真正的路径线索进入检索关键词；同时把「定位为空」与「模型拒出补丁」两类失败在提示文案上分开——原来两者共用一句「无法生成补丁」，掩盖了不同病因。
- 文件：scripts/analyzer.py
- 影响：同类问题后续还会暴露更深层原因，见 09-23 的六连修。

## 2026-09-22 11:06 · 改进 · 运行结果加「放大查看」

- 内容：运行日志区域太小，长内容看不全。
- 做法：结果区加放大按钮，弹出大窗口查看完整日志。
- 文件：web/static/index.html、web/static/app.js、web/static/style.css
- 影响：纯前端，刷新页面即生效。

## 2026-09-22 12:15 · 修复 · 页面验收总报错、修复前截图是空的

- 内容：验收结果一律 error，且「修复前」截图采集失败，看起来像页面有 JS 报错。
- 做法：真因不是页面报错，而是请求处理函数里直接调用了同步版浏览器自动化——在异步上下文中阻塞了事件循环，导致整个服务卡住、截图自然拿不到。改为把重活丢到线程池执行。
- 文件：web/server.py
- 影响：所有涉及浏览器自动化的接口都不再阻塞事件循环。教训——异步处理函数里绝不能同步跑浏览器自动化。

## 2026-09-22 12:29 · 改进 · 页面验收重做为「复刻操作再比对」

- 内容：原来是打开页面就截图（经常截到白屏），根本反映不出缺陷有没有修好。
- 做法：改为结构化复现步骤 → 由模型编排成可执行动作序列 → 验收时打开对应路由、**重放同一套动作**再截图比对；并把「白屏」单列为不通过（文本内容过少即判定），不再当成正常页面。动作序列随提案一起保存，便于回溯到底点过什么。
- 文件：scripts/verifier.py、web/server.py
- 影响：验收结果从「截图存在与否」变为「操作后的页面状态」，可信度大幅提升。

## 2026-09-22 12:57 · 修复 · 知识库卡片打开后排版混乱

- 内容：卡片正文里代码块、列表、表格混在一起显示错乱。
- 做法：后端统一卡片正文结构，前端 Markdown 渲染改为块级解析（优先识别代码围栏），双端一起修。
- 文件：scripts/kb.py、web/static/app.js
- 影响：约定——改卡片渲染必须同时确认双端结构一致。

## 2026-09-22 13:08 · 修复 · 步骤条与真实进度对不上、板块之间没有间距

- 内容：①顶部步骤条显示的推进顺序与实际执行不一致；②各面板紧贴在一起，视觉上糊成一片。
- 做法：步骤条改为按真实阶段事件推进，不再按固定顺序自走；补齐面板间距变量。
- 文件：web/static/app.js、web/static/style.css
- 影响：纯前端，刷新页面即生效。

## 2026-09-22 13:15 · 改进 · 运行面板上移 + 三个面板可折叠

- 内容：①运行流水线面板移到「待审批提案」上方（动手的地方应该在前面）；②待审批提案、分类统计、知识库三个面板可折叠。
- 做法：折叠状态存本地，刷新后自动恢复。
- 文件：web/static/index.html、web/static/app.js、web/static/style.css
- 影响：折叠状态按面板记忆，换浏览器会重置。

## 2026-09-22 13:40 · 新增 · 实时看大模型的思考与输出

- 内容：运行过程中可以实时看到模型在想什么、走到哪个阶段、正在输出什么，不再跑完才一次性出现。
- 做法：打通全链路流式——网关的流式响应经队列中转后以流式协议推给浏览器；事件分为阶段、工单、模型增量、完成、致命错误五类；前端用流式读取逐块渲染，界面分三区（阶段行 / 思考 / 输出）。加心跳保活，所有异常路径都会复位「运行中」状态，避免卡死。
- 文件：scripts/ai_model.py、scripts/pipeline.py、web/server.py、web/static/app.js、web/static/style.css
- 影响：事件名是前后端契约，后续改动不得重命名。

## 2026-09-22 14:08 · 修复 · 试运行报参数错误

- 内容：流式改造后点「试运行」直接报「多了一个未知的关键字参数」。
- 做法：根因是同一文件被并行编辑时互相覆盖，导致函数体用了签名里没收的参数。补齐签名。
- 文件：scripts/pipeline.py
- 影响：教训——同一文件的多次编辑必须串行；遇到「函数体用了签名没收的参数」，先怀疑是编辑冲突。

## 2026-09-22 15:00 · 改进 · 结果区域空态就占满，不再跳动

- 内容：结果区域原来是出结果后才变大，页面会突然拉伸。
- 做法：空态即固定高度并给占位文案。
- 文件：web/static/style.css
- 影响：纯前端，刷新页面即生效。

## 2026-09-22 15:34 · 修复 · 新工单又「无法生成补丁」（定位算法四连修）

- 内容：换一条新工单，又出现定位不到、无法生成补丁。
- 做法：查出四个叠加原因并逐一修掉——关键词表在截断前没有去重（重复项吃满名额，真正的路径线索进不了检索）、路径提取没有限定 ASCII 模式（中文说明和账号名被当成路径）、目录判定只看末段（被 URL 里的动作词抢走，真目录失去优先级）、校验类语境的排序权重压在了命中得分之前（导致数百个无关表单文件碾压真正的嫌疑文件）。
- 文件：scripts/analyzer.py
- 影响：定位命中率明显提升；后续仍有余量，见 09-23 的彻底修复。

## 2026-09-22 16:41 · 改进 · 模型输出时自动滚动到最新

- 内容：模型边输出页面边滚，用户往上翻看历史时会被强行拽回底部。
- 做法：改为「仅当用户没手动上滚时才跟随」，用两层距离阈值判断是否贴底。
- 文件：web/static/app.js
- 影响：纯前端，刷新页面即生效。

## 2026-09-22 16:52 · 修复 · 知识库卡片「现象」有的分点清晰、有的挤成一团

- 内容：同一批卡片里，有的现象分点整齐，有的全挤在一行。
- 做法：两处叠加问题——现象文本的分点归一化 + 渲染端换行处理，双端一起修。
- 文件：scripts/kb.py、web/static/app.js
- 影响：历史卡片重建后一并生效。

## 2026-09-22 17:12 · 新增 · Figma 免 Token 通道 + 验收支持账号密码登录

- 内容：①没有 Figma 访问令牌也能用需求开发（转代码）；②页面验收遇到需要登录的应用，可以配账号密码自动登录。
- 做法：Figma 官方接口只认个人访问令牌、没有账号密码登录方式，所以无令牌时切换为「浏览器打开」通道（持久化浏览器配置，直接打开设计稿链接，代价是拿不到图层结构）。验收登录只在动作序列本身像登录时才认领（前两次填表命中账号/密码选择器 + 登录页语境），登录态持久化保存；不主动猜测登录页，避免误把普通表单当登录。
- 文件：scripts/figma_fetcher.py、scripts/verifier.py、web/server.py、web/static/index.html
- 影响：设置页新增「页面验证登录」的账号密码配置；Figma 无令牌时功能降级但仍可用。

## 2026-09-23 09:30 · 修复 · 定位算法六连修（彻底修复）

- 内容：反复出现「输入其他工单也定位不到」，同一类缺陷在不同工单上时好时坏。
- 做法：逐层实测定位到六个叠加缺陷并全部修掉——
  关键词截断前未去重，重复项吃满名额，真正的路径线索从未进入检索表；
  路径抽取未限定 ASCII 模式，中文短语与账号名被当成路径占满名额；
  目录判定只看末段，被 URL 里的动作词抢走，真目录失去最高优先级；
  校验类语境的排序权重压在命中得分之前，数百个无关文件碾压真凶；
  候选池提前丢弃已命名文件（第 3 个之后的全部消失）；
  定位窗口停在模板行，而真正要改的处理函数在窗口之外，模型只能判定「窗口不足」。
- 做法：同时新增三项能力——**历史先例加权**（同一文件/目录曾被采纳过则大幅提权，同模块反复出缺陷时是最强信号）、**二次定位重试**（模型拒答时，按它自己点名的文件重新开窗再问一次，把「宁缺勿猜」变成闭环）、**推理截断自愈**（推理模型把输出预算烧光时自动翻倍重试一次）。
- 文件：scripts/analyzer.py、scripts/ai_model.py、scripts/pipeline.py、tests/check_locate_regression.py
- 影响：新增定位回归测试 13 项，此后改定位算法必须跑。

## 2026-09-23 11:05 · 修复 · 路由型工单定位漂移（第二轮）

- 内容：工单里写的页面定位（形如 `admin/#/demo-skill-log`）解析出的目录候选被一条「必须含斜杠」的校验整批丢弃，导致定位漂移到毫不相关的页面。
- 做法：路由候选改为直通、不做斜杠校验（这才是页面定位的正确来源）；目录匹配支持连字符段的尾缀形态（`runtime-log` 能匹配到真实目录 `runtime-log`）；目录兜底只认真实路径、不让路由词参与位置推断（曾把另一个工单的真目录挤出优先级）；过滤测试账号、分支名、32 位追踪码、导航面包屑等噪音；定位窗口锚点跳过导入区，并按多关键词共现密度选位；检索加入未跟踪文件（新写还没提交的组件也能被搜到）；模型点名的裸文件名现在能解析成仓库里的真实路径，并排除依赖目录里的旧拷贝。
- 文件：scripts/analyzer.py、tests/check_locate_regression2.py、scripts/replay_locate.py
- 影响：新增路由型回归测试 10 项；新增离线重放工具，排查定位失败不必再消耗模型调用。

## 2026-09-23 12:20 · 修复 · 模型正确拒绝时被当成工具故障

- 内容：某工单的根因确实在后端（前端各页签共用同一套构造逻辑、没有任何分支差异），模型拒出补丁是**正确**行为，但界面显示红色「未生成可应用补丁」，看起来像工具又坏了；而且重试轮得到的结论被整体丢弃，界面停留在第一轮过时的判断上。
- 做法：重试轮即使没出补丁也采纳其根因结论（它看过强制开窗的文件，比第一轮新鲜）；强制开窗的新文件提到提示词最前（注意力优先）；给模型一条显式出路——根因在后端时输出结构化结论而不是模糊拒绝；「非前端缺陷」的判定从「依赖模型措辞」改为**服务端兜底打分**（显式结论 2 分、后端/接口/存储等线索 1 分，再叠加接口路径佐证，累计 2 分判定；有补丁时一律不判，只顺带提一次「后端」的前端缺陷也不会误判）；界面改为黄色提示「建议转后端/接口排查」并列出证据链。
- 文件：scripts/analyzer.py、web/server.py、web/static/app.js、web/static/style.css
- 影响：结果行与提案详情都能区分「根因不在前端」和「窗口不足」两种结论；历史提案按文本兜底同样生效。

## 2026-09-23 15:40 · 新增 · 知识库升级为两层结构（同类缺陷聚合）

- 内容：原来一个工单一张卡、卡片之间没有横向关联，看不出「这两条其实是同一个病」。现在在缺陷卡之上加一层「模式卡」，专门沉淀同类缺陷的共性根因与处理方式。
- 做法：新增纯规则聚类（不调模型——重建必须可复现，「复发 N 次」才有意义）：症状族词表 × 模块前缀；族内若有 ≥2 例落在同一模块，单独拆成子模式以保留「同模块反复出缺陷」的信号，跨模块的同类症状绝不拆散（否则全变成只出现一次的碎片）。模式卡里区分「✅ 已验证」（有已采纳案例支撑）与「⚠️ 待验证」（仅分析结论），不允许把未验证的判断写成结论。知识库面板加「模式 / 缺陷卡片」双层切换，模式卡里的工单编号可点回缺陷卡。
- 文件：scripts/kb_patterns.py、scripts/kb.py、web/server.py、web/static/index.html、web/static/app.js、tests/check_kb_patterns.py
- 影响：当前 10 张缺陷卡聚成 7 个模式、其中 2 个已复发；模式卡随知识库重建自动派生且幂等（旧模式文件会被清理）。

## 2026-09-23 16:30 · 新增 · 右上角「更新日志」入口

- 内容：右上角新增一个「更新」入口，点开可看到每次改动**改了什么、怎么改的、改动时间、影响是什么**；有新日志未读时入口会显示一个小圆点，并支持按标签（新增/改进/修复…）筛选。
- 做法：以项目根 `CHANGELOG.md` 为唯一数据源（界面上的记录与仓库文本一字不差，不做模型二次归纳）。新增解析模块把日志切成结构化条目——头部按日期/时间/标签解析（顺序任意、可缺省），正文支持 内容/做法/文件/影响/备注 五段及常见同义词，缩进续行并入上一字段，代码围栏与 HTML 注释一律跳过。服务端提供只读接口，按日期分组、新到旧返回。前端弹窗以时间线展示，文件列表渲染成代码小标签，命中「重启」字样的影响会高亮提醒；未读判断用内容寻址的条目 id 存本地，不依赖顺序。
- 文件：CHANGELOG.md、scripts/changelog.py、web/server.py、web/static/index.html、web/static/app.js、web/static/style.css
- 影响：静态文件刷新页面即生效；读日志走新接口，后端需重启 `python -m web.server`（未重启时入口会提示）。

## 2026-09-24 09:45 · 新增 · 长任务作业：多子 Agent 协作 + 人工随时介入

- 内容：新增第五个页签「长任务作业」，专门跑重构、组件升级迁移这类工作量大、执行时间长、覆盖面广的活。任务被派给四个各有职责的子 Agent——🧭 决策官拆工作项定方案、⌨️ 编码工程师逐项实现、🧪 测试工程师补用例找回归、🔍 复核官收口给结论。界面右侧是实时看板，每个角色一张卡，显示它此刻在做什么、计划进度、产出的文件，以及**它自己的逐字思考与输出**；发现某个子 Agent 方向跑偏，可以随时追问 / 纠偏 / 要求重规划 / 暂停 / 跳过当前项 / 终止。跑完结果汇总成一份产出物，仍走「人工审 diff → 采纳才写工作区」这条老链路，全程不碰目标仓库。
- 做法：①**介入模型按「安全边界」设计**——模型调用是不可中途打断的同步请求，硬中断只会拿到半截输出，所以指令投递后不立即生效，而是在每次模型调用前、每个工作项结束时由对应角色签收（`team_bus.checkpoint`），界面如实标注「待生效（当前步骤结束后）」，签收后才改成「已由 XX 收到」，跑完再投递直接 409，不假装送达；②编排是 plan → 逐项（编码 → 测试）→ 复核 → 有限轮返工（复核判 block 时按「最多返工轮次」重跑问题项），决策官始终参与（否则没有工作项）；③逐字输出只走 SSE、**不落盘**，只有关键事件进 `team_runs/<id>.json`，避免写放大；④踩坑：服务端每次请求新建一个 store 实例，实例级锁挡不住「HTTP 介入线程」与「worker 线程」的并发写，人工介入会被静默覆盖——改成类级共享锁才修好（同时给事件回调补上异常日志，否则这类丢数据的问题会被悄悄吞掉）；⑤踩坑：总线 `post()` 一开始忘了把消息塞进收件箱，介入全被静默丢弃，离线自检里才暴露出来；⑥SSE 结尾补了 `done` 事件（与 `/api/run`、`/api/probe` 对齐），否则前端会永远挂在连接上；⑦产出物复用 `ArtifactStore`，新增 `team` 类型，写入路径没有新增任何分支。
- 文件：scripts/team_roles.py、scripts/team_bus.py、scripts/team_store.py、scripts/team_run.py、scripts/artifact.py、web/server.py、web/static/index.html、web/static/app.js、web/static/style.css、tests/check_team.py、tests/check_team_api.py、tests/check_team_ui.py、.gitignore
- 影响：后端需重启 `python -m web.server`；新增运行时目录 `team_runs/`（已 gitignore，会累积，需定期手动清理）；新增三层自检——离线 48 项、HTTP 层 34 项、界面接线 1 个 Playwright 脚本，改这块三个都要跑。

## 2026-09-24 10:45 · 新增 · 统计面板：token 用量可视化（第一个页签）

- 内容：新增第一个页签「统计」，把 token 消耗直接画出来——顶部五张汇总卡（区间内 / 今天 / 本周 / 本月 / 累计，各自给出调用次数、均值、输入输出拆分）；主图是按天 / 按周 / 按月的消耗趋势（输入与输出堆叠柱，悬停看当天明细，没有调用的日期也留刻度，断档一目了然）；下面依次是「按任务类型」横向占比条、「按模型」环形图、「单任务消耗」排行表（哪个工单 / 哪次作业最费 token，一眼看到），最后是最近调用的逐条明细。粒度与区间（7 天 / 30 天 / 90 天 / 1 年）可切，选择记在本地，刷新保持。
- 做法：①**账本先行**——新增 append-only 的 `usage/usage.jsonl`，每次**真实**模型调用追加一行（模型、模式、输入/输出/推理 token、耗时、成功与否、工具调用次数）。不搞采样、不搞内存累计，面板上每个数字都是账本直接聚合出来的，可对账、可回溯；②**归属靠 contextvars，且在工作线程内部设置**——"这次调用属于哪个任务"必须在工作线程里进栈（`pipeline.run` / `run_task` / 团队编排本身就跑在线程池的工作线程里），不在异步 handler 里设，否则线程池是否传播上下文全看运气。团队作业额外按角色（决策官 / 编码 / 测试 / 复核）标注，所以能看出"哪个子 Agent 最费 token"；③**如实标注估算**——网关回了 usage 就是真值；流式响应大多不回 usage，此时按字符折算（CJK 约 1 字 1 token、其余约 3.5 字符 1 token）并置 `estimated=True`，界面打「估算」角标、说明区写清这是量级参考不是账单；流式请求顺带打开 `stream_options.include_usage`，网关不认（400/415/422）就自动降级并不再重试；④**mock 调用不入账**——mock 不消耗 token，记进去只会污染调用次数与均值；⑤失败的调用照记（prompt 真的发出去并烧了 token），但均值只除以成功次数；⑥图表全部手绘 SVG（堆叠柱、横向条、环形弧线），不引任何图表库——内网离线环境不能有构建步骤，也不能依赖 CDN；⑦统计是**旁路能力**：账本坏行、坏文件、目录不可写一律跳过，绝不能让一次 AI 调用因为它白跑。
- 文件：scripts/usage.py、scripts/ai_providers.py、scripts/ai_model.py、scripts/pipeline.py、scripts/analyzer.py、scripts/verifier.py、scripts/team_run.py、web/server.py、web/static/index.html、web/static/app.js、web/static/style.css、tests/check_usage.py、tests/check_usage_ui.py
- 影响：后端需重启 `python -m web.server`（静态文件刷新即生效）；新增运行时目录 `usage/`（已 gitignore，一行一次调用、会持续累积，需定期清理）；**默认落地页签从「缺陷修复」改成「统计」**，原先假设"打开就是缺陷修复页"的界面自检（面板折叠 / 分页器 / 放大弹窗 / 结果区 / 提案端到端 / 采纳闸门 / AI 配置）都已补上显式切页签——以后改默认页签要连它们一起跑；新增两层自检——离线聚合 59 项、界面 49 项（自带临时账本 + 临时服务实例，不污染真实数据）。

## 2026-09-24 11:10 · 修复 · 界面自检：会给目标仓库写文件的用例加硬闸门

- 内容：跑 `test_browser_e2e.py` / `test_browser_guards.py` 时，如果 8765 上跑的服务指向的不是测试用 mock 仓库，这两个用例会**把测试补丁真的写进那个真实仓库**——而它们的断言又只看 mock 仓库，所以现象是"一堆断言全红"，看起来像用例坏了，实际是动了不该动的文件。现在改成：进主线之前先比对服务配置的仓库路径，不符就打印对比信息并直接中止，不执行任何写操作。
- 做法：新增 `tests/server_guard.py` 的 `require_repo()`（读 `/api/health` 的 repo 字段，归一化反斜杠/大小写后比对），两个用例在 `main()` 第一行调用，不符返回 2（跳过语义，与失败区分）。顺带修掉四个让这些用例长期假红或误伤配置的独立问题：① 09-21 引入的自绘下拉把原生 `<select>` 藏了（`display:none`），`page.select_option()` 必然 30 秒超时，新增 `tests/ui_select.py` 的 `pick_select()`（写 value + 派发 change + 刷新触发器文案，等价于点自绘下拉那一项），替换 5 处调用；② 09-21 把大模型配置改成独立弹窗后，`test_browser_ai_config.py` 没跟着补「点开弹窗」这一步，而它从第 3 步起全是"需要可见"的操作，于是永远卡在 `uncheck` 上——补上 `#ai-summary-group button` 的点击；③ `check_pager.py` 里写死"真实提案数=5"（提案目录已自然增长到 28 条）改为动态断言，并把固定 `sleep` 换成"等提案卡片出现"的确定性等待；④ `test_browser_guards.py` 的清理是**清空**闸门命令而不是还原，跑一次就会把真实配置的验收命令抹掉（本次就把一条必然失败的 lint 命令留在了配置里、连带把工具留在 Mock 模式），改成进函数时先记下原值、退出时还原，并顺手把被污染的配置恢复回原始状态。
- 文件：tests/server_guard.py、tests/ui_select.py、tests/test_browser_e2e.py、tests/test_browser_guards.py、tests/test_browser_ai_config.py、tests/check_pager.py
- 影响：这两个用例现在**必须**用指向 mock 仓库的实例跑（README「测试」一节已写明），跑错实例时会明确告诉你"服务指向 A、期望 B"并跳过，而不是悄悄写别人的仓库；`test_browser_ai_config.py` 改用 `pick_select()` 并补上开弹窗步骤后，从"卡在第一步"恢复到能跑完整条链路。教训：**用例改完必须真跑一遍**——`test_browser_e2e` 从"卡在 select_option"到"跑完整条链路"，正是这次真跑才暴露了它其实会写真实仓库。

## 2026-09-24 11:30 · 修复 · 写仓库自检：跳过时不再碰全局配置；清掉误产的测试残留

- 内容：上一轮给两个「会写目标仓库」的用例加了硬闸门，但闸门加在了 `main()` 里、而切 Mock 模式发生在 `main()` 之前——于是「明明跳过了，工具的模型模式却被改成 Mock、验收命令也被换成了必然失败的 lint」这种残留又出现了一次（进程被外部掐断时清理代码不执行）。现在把仓库判定提到最前面：服务没指向 mock 仓库就立刻退出，**全程一个设置字段都不改**。同时把之前误写进真实仓库那轮留下的测试残留从知识库里清干净。
- 做法：①两个用例的 `__main__` 改为先 `require_repo()` 再 `force_mock()`，跳过路径不再产生任何副作用（同时保留 `main()` 内的第二道判定做纵深防御）；②清理误产残留：删掉 4 份 `TEST-1001` 提案与对应缺陷卡（删除前整份备份到 `.workbuddy/backup/20260924-test-residue/`），再跑 `scripts/rebuild_kb_cards.py` 重刷派生视图——INDEX / _stats / 模式卡的 `_meta.json` 一并重建，只有这一例支撑的「校验缺失」模式卡被自动剪掉（8 个模式卡 → 7 个，剩 `validate_missing_schedule`），全库不再有任何文件提及该工单；③顺手确认：09-22 那批 `COMPAT-TEST-1` / `STREAM-TEST-1` 假工单**不能照删**——`check_kb_patterns.py` 直接对着真实知识库断言「同一处缺陷的不同工单（含验证用假工单）归入同一模式」，删了这条用例就红，属于「用例依赖生产数据」的既有毛病，本次原样保留并在 README 里记一笔。
- 文件：tests/test_browser_e2e.py、tests/test_browser_guards.py、knowledge_base/INDEX.md、knowledge_base/_stats.json、knowledge_base/_patterns/_meta.json、CHANGELOG.md
- 影响：跑错实例的代价从「悄悄动别人仓库 + 改坏全局配置」降为「打印一行对比信息后退出」；知识库回到只剩真实缺陷卡（11 张）与 6 个模式；回归全绿——模式聚类 17 项、用量离线 59 项、用量界面 49 项、更新日志 29 项。

## 2026-09-24 11:45 · 修复 · 待审批提案列表改为按时间倒序

- 内容：「待审批提案」面板里的卡片顺序看着忽新忽旧——刚跑出来 8 分钟、闸门未过的提案排在第 9 位，前面压着 6 条两天前的旧提案。现在整个面板一律按时间倒序（更新时间优先、创建时间兜底），最新的永远在最前面。
- 做法：原来的排序是**先按状态分组**（待审 → 闸门未过 → 无补丁 → 已定论），时间只是同组内的次序——于是只要有一条 `gate_failed`，它再新也会被所有 `pending` 压在下面；状态徽标本就显示在卡片上，状态顺序并不需要靠排序来表达。改为时间做主排序，状态只在时间完全相同时当稳定兜底。`tests/check_pager.py` 补了两条回归断言：真实数据下「DOM 前 8 = 按时间序的前 8」，注入数据下「同状态严格按时间倒序」。
- 文件：web/static/app.js、tests/check_pager.py
- 影响：静态文件刷新页面即生效；「产出物」面板仍是状态分组优先的旧排序，如需同样改成时间序说一声即可。

## 2026-09-24 12:15 · 新增 · 问答页签：不套流水线，随便问或顺手改代码

- 内容：新增「问答」页签（第 2 个，排在「统计」之后）。日常很多时候既不是修缺陷也不是做需求——就是问一句「这个配置在哪改」「这个报错什么原因」，或者顺手让 AI 改点代码，之前的四个页签都要先凑齐固定输入（工单 / 设计稿 / 接口文档 / 目标文件）才肯干活，硬套反而更慢。现在这个页签：直接提问 → AI 自己到仓库里列目录 / 读文件 / 正则搜索找证据 → 用 Markdown 回答并带上 `文件路径:行号` 引用；如果要的是改代码，它会在回答末尾附一段改动提案，落成「产出物」，**采纳才会写工作区**。会话按一问一答落盘在 `chat_sessions/`，可切换、清空、删除，刷新不丢。
- 做法：①新增 `scripts/chat.py`：`ChatStore`（会话落盘，首句提问自动当标题）、`RepoReader`（目标仓库**只读**视图，路径一律过 `safe_rel_path` 判定，越界直接拒绝；`git ls-files` 拿文件清单、拿不到就带黑名单遍历兜底；`repo_list` / `repo_read` / `repo_grep` 三个工具，二进制跳过、单文件大小上限、命中数上限）、`run_chat`（多轮 agent 循环，模型需要查仓库时只输出一个 `{"action":"call",...}` JSON，查够了再给正式回答）。②`AIModel` 新增文本模式 `complete_text()`：问答的回答是自由 Markdown，套原来的 JSON 契约会被 `_extract_json` 截到 3000 字符、正文里的代码块还容易被误判成候选；顺手把「账本记录 + 推理截断自动重试」抽成 `_call_raw()`，让 JSON 模式与文本模式共用同一条路径，用量口径不走偏。③`/api/chat/stream` 走 SSE 复用既有事件风格（stage / tool / ai_delta / done / fatal），增量按 60 字符或换行合并后再发；问答有**自己的占用标记**，与缺陷流水线互不干扰（可以一边跑流水线一边问问题），为此给 `_queue_stream` 加了 `state` 参数——原来它固定复位 `_RUN_STATE`，两条流程共用一个标记会互相误放行。④产出物面板按现有机制挂上 `chat` 类型，改动提案的详情 / diff / 采纳 / 撤销全部复用，没有新增写入路径。
- 坑：**增量缓冲必须记住 kind**——模型先吐 reasoning 再吐 content，按 kind 分批发出；最初收尾时统一按 `content` 发送，最后一截思考就被当成正文渲染进回答了。**`#amodal`（产出物详情）一直没被登记进全局 Esc 处理**，按 Esc 关不掉、弹窗还会一直挡住后续点击（自检里表现为后续点击全部超时），已改为走 `closeAModal()` 并顺带清状态。**`task_modules.py` 漏 import `MAX_TOOL_TEXT`**，MCP 工具一被调用就 `NameError`，一并补上。测试隔离升级：`SETTINGS_FILE` / `ARTIFACT_DIR` 也支持环境变量覆盖，自检实例现在把**设置 / 会话 / 产出物 / 账本 / 仓库**全部指向临时目录，从根上不会读你的真实配置、也不会往真实目录写东西。
- 文件：scripts/chat.py（新）、scripts/ai_model.py、scripts/task_modules.py、scripts/usage.py、scripts/artifact.py、web/server.py、web/static/index.html、web/static/app.js、web/static/style.css、tests/check_chat.py（新，66 项）、tests/check_chat_ui.py（新，37 项）
- 影响：需重启后端（新增接口），静态文件刷新页面即生效。安全边界不变：问答只读仓库，唯一的写入路径仍然是「采纳」；mock 模式下问答会明确标注 mock、绝不假装是真结论。回归全绿：问答离线 66 项、问答界面 37 项、用量离线 59 项、用量界面 50 项、模式聚类 17 项、更新日志 29 项，以及提案分页 / 面板折叠 / 知识库渲染 / 阶段条 / 产出物弹窗 / 长任务界面 / 实时运行界面全部通过。


## 2026-09-24 14:45 · 优化 · 更新日志与说明条排版：分点另起一行，挤在一起的地方全部加间隔
- 内容：更新日志弹窗里「做法/补充」写成带圈序号连排时全部挤成一段；问答/需求开发等页签底部的说明条多句话贴在一起，表单 label 和输入区之间也几乎没有间隔。
- 做法：① 前端渲染 `chBlock()` 在带圈序号处拆行，序号行悬挂缩进（换行后与正文对齐），分点行距加大；② 长说明条（问答/试运行说明/Figma 提示/产出说明/页面验证登录等 7 处）按句加 `<br>` 分行，行高 1.6→1.75；③ 需求开发/接口联调/代码测试三个单列表单的字段、预览区、按钮行之间补 12px 留白，问答输入区与说明条之间加间隔。
- 文件：web/static/app.js web/static/style.css web/static/index.html tests/check_layout_ui.py（新）
- 影响：纯静态展示层，刷新页面即生效，不用重启后端。新增 `tests/check_layout_ui.py`（8 项：分点拆行、说明条分行、表单留白，附三张截图），`check_changelog(.py/_ui.py)` 回归全绿。


## 2026-09-24 15:05 · 优化 · 全项目排版复查：长任务作业与统计说明条补齐间隔
- 内容：长任务作业页签的说明条四句话挤成一段、「技术栈」标签被折成「技术/栈」两行、表单块之间没间隔；统计页说明条分段之间也没有缝。
- 做法：① 说明条按句分行扩到长任务作业与两处 panel-note，行内代码两侧加缝；② `.field-inline` 标签禁止折行；③ 任务表单留白规则重写成「相邻块统一 12px、紧跟标题和自带下边距的按钮行不叠加」，并把长任务作业纳入；④ 统计说明条分段间距加到 5px；⑤ CHANGELOG 书写约定新增：正文别写字面序号区间（会被自动拆行误拆成碎行），已修掉两条这类写法。
- 文件：web/static/index.html web/static/style.css web/static/app.js CHANGELOG.md tests/check_layout_ui.py
- 影响：纯静态展示层，刷新页面即生效。`check_layout_ui` 扩到 12 项（新增统计分段间隔/长任务分行/标签不折行/长任务表单间隔），12/12 通过；`check_changelog(.py/_ui.py)` 回归全绿。

## 2026-09-24 17:05 · 改进 · 让「下载下来就能直接跑」：mock 仓库自举、知识库用例脱敏、CLI 演示工单

- 内容：把「别人克隆这个仓库能不能用」这条线补齐。三处硬伤：① 四个界面用例依赖本机写死的 mock 仓库路径和一个固定提交号，换台机器必然找不到仓库、断言必然红；② 三个知识库用例直接对**真实知识库**断言真实工单号，新克隆没有那份数据，而且为了保住这些断言，生产知识库里两条测试用假工单一直不敢删；③ 没配 ONES 时流水线拉不到任何工单，但 README 的「快速开始」写着 `--mock` 能跑出东西，实际上只会得到 0 条。现在：mock 目标仓库由 `tests/mock_repo.py` 一键生成（`tests/make_mock_repo.py --set-server` 顺手把服务指过去）；知识库用例改用 `tests/kb_fixture.py` 的脱敏夹具并自带临时实例；新增 `run.py --demo`，不接 ONES 也能把「提案 → 审 diff → 采纳」完整走一遍。
- 做法：① 收掉测试里的本机绝对路径（截图目录、relay 临时仓库一律从 `__file__` 推导），mock 仓库路径只由 `tests/mock_repo.py` 解析（`MOCK_REPO` 优先，默认与 `web/server.py` 的 `DEFAULT_REPO` 同规则），`test_browser_guards` 不再比对写死的提交号，改成「仍停在唯一那次 init 提交」的语义；② 新增 `tests/temp_server.py`：把设置文件与七个数据目录全指到临时目录、端口随机、退出即清理，界面用例照抄十几行即可完全隔离；③ `web/server.py` 把六个数据目录的环境变量开关补齐（原先只有产出物与会话目录有），端口支持 `PORT`；④ `scripts/mock_data.py` 新增 `DEMO_DEFECT`，`SAMPLE_DEFECTS` 保持空列表——演示数据**只由 `--demo` 显式注入**，拉取失败绝不回退假数据；⑤ `.gitattributes` 统一换行符（文本一律 LF 入库，bat 检出为 CRLF）。
- 文件：web/server.py、run.py、scripts/mock_data.py、tests/mock_repo.py（新）、tests/make_mock_repo.py（新）、tests/kb_fixture.py（新）、tests/temp_server.py（新）、tests/fixtures/mock_repo/（新）、tests/check_kb_patterns.py、tests/check_kb_render.py、tests/check_kb_patterns_ui.py、tests/test_browser_e2e.py、tests/test_browser_guards.py、tests/check_pager.py、tests/check_expand_modal.py、tests/test_relay_transport.py、config.test.yaml、README.md、.gitattributes（新）
- 影响：需重启后端（目录解析与端口都有改动），静态文件刷新页面即生效。知识库用例不再依赖生产数据，两条历史遗留假工单（`COMPAT-TEST-1` / `STREAM-TEST-1`）现在可按 README「已知限制」里的步骤放心清掉，模式聚类断言改为对夹具生效。回归：知识库模式聚类 18/18、知识库渲染 PASS、双层视图 PASS；另做了一次真实新克隆演练（冷启动、空知识库下的全部只读端点、mock 仓库自举、演示工单跑通、八个页签无报错）全部通过。

## 2026-09-24 17:20 · 文档 · 快速开始步骤顺序修正：指向 mock 仓库的命令要等服务起来

- 内容：README「快速开始」第 2 步把两条命令写反了——先跑 `tests/make_mock_repo.py --set-server` 再启动服务。照抄的人会看到 `[skip] 8765 上没有可用的服务`，却以为仓库已经指过去了。
- 做法：`--set-server` 本质是 `POST /api/settings`，必须服务先跑着；把「启动服务」提到前面，并注明另开一个终端执行配置命令。脚本本身的行为是对的（连不上就明确打印 skip 提示并退出 0），只有文档顺序有误导。
- 文件：README.md、CHANGELOG.md
- 影响：纯文档，不改代码。刷新页面即可看到新的更新日志条目。

## 2026-09-24 18:05 · 安全 · 发布树脱敏与裁剪：业务标识换占位，定位回归用例移出

- 内容：把「公开仓库里不该出现的东西」清干净。上一轮已确认凭据、个人标识、本机路径零命中，这轮补上业务标识：真实工单号 → `DEMO-*` / 序号占位；公司产品的包路径、内部路由与迭代号 → 中性的 `demo` / `演示` 占位（涉及包目录与路由段，此处不复述原文）。共 13 个文件 56 处，全部落在注释、文档字符串与测试夹具里，不碰任何功能代码。
- 做法：① `tests/check_locate_regression*.py` 与 `tests/fixtures/proposals/` 与真实前端单仓**结构性绑定**（要读真仓某个日程组件的具体文件、并断言其中某个处理函数的实现），产品路径没法替换——换掉之后这两条用例在本机 13/13、14/14 就不再是同一个断言了。所以它们**移出发布树**（`.gitignore` 拦截 + `git rm --cached`），文件仍留在本地磁盘照常可跑。② 顺手修掉 `tests/repo_guard.py` 的模块 docstring 未闭合（上一轮编辑吃掉了结尾的 `"""`），它会让导入这道闸门的用例直接 SyntaxError。③ README 补一句「发布出去的 `tests/` 自包含」，并说明上面两个文件为何不在其中。
- 文件：scripts/analyzer.py、scripts/chat.py、scripts/kb_patterns.py、scripts/ones_fetcher.py、scripts/pipeline.py、scripts/verifier.py、tests/check_expand_modal.py、tests/check_live_stream.py、tests/check_page_login.py、tests/check_verify_page.py、tests/kb_fixture.py、tests/repo_guard.py、web/static/app.js、.gitignore、README.md、CHANGELOG.md
- 影响：纯注释/夹具/文档，功能零变化。发布出去的 86 个文件里，凭据、个人标识、本机绝对路径、业务包路径与真实工单号均为零命中；在无 `ui_settings.json`/`proposals/`/`.venv` 的归档目录里离线用例全绿、服务能冷启动。回归：73 个 Python 文件全量 `py_compile` 通过；离线用例全绿（含本地保留的定位回归 13/13 与 14/14）。

## 2026-09-28 10:20 · 重构 · 前端 Vue 3 迁移收口：只留一份实现，步骤条回归一并修掉

- 内容：前端从「原生 HTML + app.js 与 Vue 版并存」收口为**唯一实现**——`GET /` 直接返回 Vue 构建产物，旧版 `index.html`（76KB）/ `app.js`（197KB）/ 迁移期调试桥 `debugBridge.js` 与 `/v2` 别名全部删除。迁移期靠调试桥把 Vue 内部状态伪装成旧版全局函数的做法随之作废。收口过程中查出一处**用户可见的真回归并修好**：缺陷页签顶部五步流程条（`#stage-flow`）在迁移时被写成静态标记，`useDefect` 里整套步骤状态机（`stagesRunning` / `stagesFromRunResults` / `stagesFromProbeResults`）算出来却没接到 DOM 上，导致五步永远不带 `active/done/warn/fail/off`，`style.css` 里 `.stage.done`/`.stage.fail` 等状态样式形同虚设。现已用 `:class="stageStates[i]"` 绑定，跑完一轮后实测为 `['done','warn','fail','fail','done']`，与后端返回结果一致。
- 做法：① `web/server.py` 的 `GET /` 改指 `VUE_ENTRY`（`v2.html`，缺失时返回 503「前端尚未构建」），删掉 `/v2` 路由；`App.vue` 把 `stageStates` 绑到五个 `.stage`，与 `DefectPane` 共享同一套模块级单例状态。② 界面用例去双跑化：`check_vue_settings_ui.py`、`check_vue_extensions_ui.py` 原先「旧版 `/` 与 `/v2` 各跑一遍」的循环收成单跑，前缀条件分支里的 `__setRunResult` 调试桥改成走真实链路（包一层 `window.fetch`，只给 `/api/run/stream` 的 POST 注入 mock 工单）。③ `check_vue_all.py` 的 LEGACY 清单只保留不依赖旧版全局函数的 `check_panel_fold.py` / `check_layout_ui.py`，其余六条（`check_stages_ui` / `check_live_ui` / `check_changelog_ui` / `check_expand_modal` / `check_kb_patterns_ui` / `check_kb_render`）实测均为旧版全局 `ReferenceError`，已在注释里逐条注明覆盖去向并退役。④ `tests/check_vue_defect_ui.py` 补第 4b 步「步骤条与真实结果联动」断言守住这处接线。⑤ 记一条构建纪律：产物是带 hash 的文件名，必须走 `npm run build`（先 `clean` 再 build），直接 `vite build` 会在 `web/static/vue/` 堆下旧 hash，被 `check_vue_migration.py` 的「bundle 恰好 1 个」断言拦住。
- 文件：web/server.py、frontend/src/App.vue、frontend/src/views/DefectPane.vue、frontend/src/views/TeamPane.vue、frontend/src/composables/useProposalModal.js、frontend/src/components/ProposalModal.vue、frontend/vite.config.js、frontend/scripts/clean.mjs、scripts/chat.py、tests/check_vue_settings_ui.py、tests/check_vue_extensions_ui.py、tests/check_vue_defect_ui.py、tests/check_vue_migration.py、tests/check_vue_stats_ui.py、tests/check_vue_chat_ui.py、tests/check_vue_team_ui.py、tests/check_vue_tasks_ui.py、tests/check_vue_shell_ui.py、tests/check_vue_all.py、README.md、CHANGELOG.md；删除 web/static/index.html、web/static/app.js、frontend/src/api/debugBridge.js
- 影响：需重启后端（路由有改动），并跑一次 `npm run build` 生成产物（`GET /` 在产物缺失时返回 503）。回归：`tests/check_vue_all.py` 14 个用例非零退出 0 个——Vue 系列八条全绿（含新增的步骤条断言），`check_panel_fold.py` / `check_layout_ui.py` / `check_chat.py`(88) / `check_usage.py`(59) / `check_usage_ui.py` 全绿；前端构建 68 个模块通过。

## 2026-09-28 18:40 · 修复 · 问答输入框塌缩与位置，静态资源缓存策略

- 内容：① 问答页签输入框在部分浏览器里塌缩成窄条白块（样式未生效）；② 输入区挪进聊天容器（`.chat-box`）内部底部，与消息区视觉一体；③ 治根：`/static` 增加缓存策略，改样式后刷新即生效，不再吃浏览器启发式缓存。
- 做法：① 塌缩根因是浏览器缓存了没有 `.chat-drop` 规则的旧 style.css（拖拽上传是后加的包裹层），本地 Playwright 实测样式本就正确——故治缓存而非改样式：`/static/vue/`（带 hash 的构建产物）返回 `max-age=31536000, immutable`，其余（style.css、v2.html）返回 `no-cache` 每次协商缓存；② 模板上把 `.chat-input` 移入 `.chat-box`（`.chat-live` 状态行之后），CSS 间距从面板级 margin 改为容器内 `margin: 10px 12px 12px`。
- 文件：web/server.py、frontend/src/views/ChatPane.vue、web/static/style.css、scripts/changelog.py（docstring 示例路径更新）
- 影响：需重启后端（中间件有改动）+ 刷新页面；已构建 68 模块（新 bundle `app.2ahB7EtX.js`）。回归：实测 `/static/style.css` → no-cache、`/static/vue/*.js` → immutable；`chat-input` 在 `.chat-box` 内（`contains` 为真）；`tests/check_vue_all.py` 14 个用例非零退出 0 个。

## 2026-09-29 10:10 · 改进 · 统计页图表升级 ECharts，带动效与交互

- 内容：① 「消耗趋势」堆叠柱与「按模型」环形图改由 ECharts 绘制：柱子依次长出的入场动画、切粒度/区间时的平滑过渡、hover 高亮 + 跟随光标的富 tooltip（替代原来的 SVG \<title\>）、环形图 hover 扇区放大；② 图例变为可点击——趋势图可隐藏输入/输出任一系列，环形图可临时隐藏某个模型；③ 新增图表通用壳组件，后续加图直接复用。
- 做法：按需引入 echarts/core（Bar/Pie + Grid/Tooltip + SVGRenderer），并以 defineAsyncComponent 异步加载——ECharts 单独成一个 chunk（505KB，gzip 178KB），不撑大首屏 app.js（292KB，gzip 105KB）。ECharts 不认 CSS 变量，新增 utils/chartTheme.js 在渲染时把 --c-in/--c-out/--border 等解析成具体色值，主题切换即重算重绘；空槽用低透明度底色保留「有记录但 0 token」的存在感。tooltip 文案改为组件先算成 rows[].tip 再由 formatter 原样吐出（文案只有一处来源）；环形图中心总量改 HTML 覆盖层（.usage-donut-total 保留，配色跟主题）。
- 文件：frontend/src/components/EChart.vue（新）、utils/chartTheme.js（新）、UsageTrendChart.vue、UsageDonut.vue、views/StatsPane.vue、web/static/style.css、frontend/package.json（+echarts@6）
- 影响：刷新页面即生效（构建产物已随仓库提交，用户侧无需装依赖；重新构建需要 npm i）。回归：tests/check_vue_stats_ui.py 与 check_usage_ui.py 的图表断言改为读图表宿主节点契约（__probe / __ec.getOption()），语义等价（空桶在、tooltip 文案、轴刻度、扇区数=模型数）；顺手修掉 stats 用例写死日期（09-28）跨天必挂的问题；tests/check_vue_all.py 14 个用例非零退出 0 个。

## 2026-09-29 10:30 · 重构 · 界面组件全量迁移 Element Plus，新增五套可切换配色风格

- 内容：① 自研 UI 原语全部换成 Element Plus——按钮、输入框、下拉、开关、折叠面板、标签、表格、分页、步骤条、六个弹窗、空态、页签、消息提示；② 顶栏新增「风格」下拉：靛青 / 海蓝 / 松石 / 琥珀 / 玫瑰五套强调色，与明暗模式正交组合，localStorage 各自记忆，切换时按钮、页签、滑块、焦点环、图表高亮整套跟着变；③ 构建产物把 element-plus / vue 拆成独立 chunk：入口 app.js 从 572KB 降到 235KB（gzip 180→80KB），element-plus 单独成块（gzip 304KB），业务改动不再打穿组件库缓存。
- 做法：unplugin-auto-import + unplugin-vue-components 自动按需引入（模板零 import，命令式 ElMessage 也由插件注入）；新增 styles/theme.css 把 Element 变量桥接到项目变量（明暗双写 [data-theme] 与 html.dark，只设一个会出现「面板暗、按钮亮」）；五套风格各只存「主色 + 次色」，6 个派生色按 Element 官方混色比例在运行时算，改一个主色整套生效。踩过的坑：① el-dialog 内容体首次打开前不渲染，弹窗外要套常驻 div 承载稳定 id 与 hidden 语义，但 overlay 是 position:fixed 不撑父盒、壳的盒子恒为 0 高——用例判「弹窗打开」要看壳内的 .el-dialog，不能判壳可见；② el-select 把透传的 id 绑在内层 input 上（点它会被后缀图标判「pointer events 被拦截」直到超时），且选项不渲染 value，要按 input 的 aria-controls 指向的 listbox 定位选项；③ el-pagination 的页大小必须是常量，用「当前页切片长度」推导会让末页总页数与「下一页」禁用态全乱；④ el-tag 是原子行内级盒子，innerText 会在它前面插一个换行（截图核实视觉仍在同一行，textContent 也紧邻）；⑤ el-table 的 prop 列不会渲染「%」这类后缀，占比列要显式写插槽。
- 文件：frontend/vite.config.js、main.js、styles/theme.css（新）、composables/useTheme.js、useToast.js、App.vue、components/ 下 18 个组件（弹窗/表格/分页/卡片/消息条）、views/ 下全部 9 个页签、frontend/package.json（+element-plus@2.14.6、@element-plus/icons-vue、unplugin-auto-import、unplugin-vue-components）、web/static/style.css、tests/ui_select.py、tests/check_vue_migration.py、check_vue_shell_ui.py、check_vue_defect_ui.py、check_vue_stats_ui.py、check_usage_ui.py、check_layout_ui.py、tests/test_browser_e2e.py、test_browser_guards.py、test_browser_ai_config.py
- 影响：刷新页面即生效（构建产物随仓库走，用户侧无需装依赖；本机重新构建需先 npm i）。回归：tests/check_vue_all.py 14 个用例非零退出 0 个、check_layout_ui 12/12；五套风格与明暗模式实机截图核实（_tmp_shots/style_*.png）。两个退役用例（check_chat_ui / check_page_login_ui / check_pager）不在执行清单里，仍引用旧版 .tab / .pg-num 选择器，未随本次迁移更新。

## 2026-09-29 · 修复 · 输入框样式统一：暗色不再糊成黑块，聚焦有主色光环
- 内容：全部输入框（侧栏配置、长任务表单、大模型弹窗、问答输入区、筛选下拉等）描边统一走 --input-border：暗色从几乎不可见的 9% 白提到 17%（悬停 30%），亮色 20%；聚焦时加一圈主色软光环；原生输入框/下拉与 Element 控件的圆角统一为 10px；暗色输入底色微抬一档（5%→7%）。
- 做法：新增 --input-border / --input-border-hover 两个主题令牌（style.css 明暗两块各一份），theme.css 里 el-input/el-textarea/el-select 的描边、悬停、聚焦全部改读令牌；原生 .field input/select、.field-inline input、.filter-wrap select、.chat-input textarea 同步对齐。顺手修了一个真 bug：GET / 之前没有 Cache-Control，浏览器启发式缓存旧入口 HTML 后会引用已被构建清掉的旧 hash 产物（表现为「改了样式看不到/界面缺一块」），现在入口与 style.css 一样永远 no-cache——若仍见旧样式请 Ctrl+F5 强刷一次。
- 文件：web/static/style.css、frontend/src/styles/theme.css、web/server.py
- 影响：样式刷新页面即生效；server.py 的缓存头改动需重启后端。回归：tests/check_vue_all.py 14 个用例非零退出 0 个；明暗双主题输入框/聚焦态/弹窗/问答/需求页实机截图核实（_tmp_shots/in2_*.png、chk_*.png）。

## 2026-09-29 12:20 · 修复 · 最后 4 个原生下拉迁到 el-select；el-select 宽度契约（选中值不再被裁）
- 内容：长任务作业的「技术栈 / 只看 / 发给」与缺陷页的「筛选」4 个原生 <select> 全部换成 el-select（至此应用里不再有原生下拉，CsSelect.vue 死代码一并删除）；修掉 el-select 选中值不显示的宽度问题——顶栏风格选择器被压到 40px（只剩半个字）、表单里的下拉没有宽度契约。
- 做法：el-option 全部补 data-value 供用例确定性选值；#team-filter 改 :model-value + @update:model-value；宽度契约三条——.field-inline .el-select（170-240px 弹性）、.filter-wrap .el-select（150px）、顶栏 .top-actions > .el-select.style-picker（98px，**必须抬权重到 0-3-0**：style.css 在 element-plus.css 之前加载，Element 基础规则 .el-select{width:var(--el-select-width)}（默认100%）同为 0-1-0 但靠后，同权重写 width 会被盖掉，这是这次最贵的坑）；.field/.field-inline 的原生 input 规则补 :not(.el-select__input) 防止「框里套框」复发（el-select 内层也有原生 input 且 id 绑在它上面）。
- 文件：frontend/src/views/TeamPane.vue、DefectPane.vue、web/static/style.css、tests/ui_select.py（新增 el_select_values 助手）、tests/check_vue_defect_ui.py、tests/check_vue_team_ui.py、删 frontend/src/components/CsSelect.vue
- 影响：刷新页面即生效。回归：check_vue_all.py 14 个用例非零退出 0 个；技术栈下拉选值回显、风格选择器 98px、筛选联动（applied→空态→回全部）实机探针核实。
## 2026-09-30 09:35 · 修复 · 问答 repo_grep 大仓静默截断：只找到 $t() 引用、找不到语言包定义（类问题修复）
- 内容：真实案例——用户让问答改一条 i18n 文案，AI 断言「全仓只有 3 处引用、没有定义」，实际定义在语言包目录里的某个 `<模块>.zh-CN.ts`（另两份 zh-TW/en-US 也要一起改）。根因：目标仓 8000+ 个文件，语言包文件排在 `git ls-files` 序的 6000 截断线之后，而 `RepoReader.grep()` 在第 6000 个文件处静默截断——模型拿到的是「文件列表前缀的命中」却当成了全仓结论。按类问题修，不只补这一个 case。
- 做法：① 引擎层——`repo_grep` 优先走 `git grep -nI --untracked`（无扫描上限、跳二进制、遵守 .gitignore，正则先试 -P 再试 -E，均不兼容或非 git 仓才退 Python 兜底扫描；glob 转 pathspec：`.ts`→`*.ts`、`src/**`→`:(glob)src/**`，命令加 `-c core.quotePath=false` 防中文路径转义）；② 诚实化——Python 兜底截断时必须报「仓库共 N 个文件、只覆盖文件列表前缀、不要当成全仓结论」（上限提成常量 `GREP_SCAN_CAP`）；③ 提示词层——工具规则新增「找定义而不是只找到引用」一节：`$t('a.b.c')` 是引用，定义在 locales/i18n/lang 目录、点分 key 末段才是文件内字段名、多语言每份都要改；常量/配置同理；带截断提示的结果不得当全仓结论。
- 文件：scripts/chat.py（TOOL_SPECS、TOOL_RULES、GREP_SCAN_CAP、RepoReader._glob_pathspecs/_git_grep/_scan_grep/grep）、tests/check_chat.py（[3b] 节 9 条回归 + [6] 节提示词断言）
- 影响：需重启后端生效。真实仓验证：`grep personalizationSaved` 由 3 条（只有引用）变为 6 条（3 引用 + zh-CN/zh-TW/en-US 3 份定义）；glob 过滤、无命中、兜底截断提示均实测。回归：tests/check_chat.py 100/100。

## 2026-09-30 11:20 · 改进 · 问答页签：对话区吃满高度，产出物 / 使用说明改浮窗

- 内容：① 对话区不再下方留一大条空白——高度按视口算，窗口越高对话区越高（高分屏最多多出约 250px 可视区）；② 使用说明从常驻右栏搬进浮窗：标题栏多了「使用说明」按钮，点开是居中浮窗，关掉不占地方；③ 产出物同样改成右侧浮窗抽屉：标题栏「产出物」按钮带数量角标，点开从右侧滑入，卡片终于放得下了；④ 右栏取消后对话区变宽，会话历史仍可收起成一条竖排按钮；⑤ 标题栏常驻一句「只读仓库 · 改动要人工采纳才写入」，边界不再只藏在说明里。
- 做法：`.chat-grid` 从三栏收敛成两栏，高度由 `clamp(560px, calc(100vh - 250px), 980px)` 改为 `clamp(560px, calc(100vh - 174px), 2200px)`——174px 是在 1440x920 / 1920x1080 / 2560x1400 三档实测出来的固定占位（顶栏 76 + 页签条 41 + 留白 43），旧值的 250px 预留远超实际占位、980px 上限又卡住了高分屏。两个浮层都用 position:fixed 挂在问答页签内：页签切走时 el-tab-pane 本身 display:none，浮层跟着隐藏不会串页签；关着时用 display:none 而不是缩到屏幕外，免得隐藏文案混进页面可见文本；打开动效靠 CSS animation（display:none 切回可见会重新触发）。产出物面板新增 emptyHint 参数，其他页签的空态文案保持不变。
- 文件：frontend/src/views/ChatPane.vue、frontend/src/composables/useChatRails.js、frontend/src/components/ArtifactsPanel.vue、web/static/style.css、tests/check_vue_chat_ui.py（新增 6 条浮层断言）
- 影响：纯前端，刷新页面即生效。回归：check_vue_chat_ui.py 35 项全过（含浮层开关、Esc/点遮罩关闭、切页签不串），check_vue_shell_ui.py 全过；明暗双主题实机截图核实。

## 2026-09-30 11:40 · 修复 · 问答页占满一屏后不该能滚 + 左导航固定 + 浮窗留出内边距

- 内容：① 问答页「填满一屏」之后页面仍能往下滚，改成真正的一屏装下；② 侧边工作台布局里左侧菜单不再跟着页面滚走，吸顶停在顶栏下方；③ 产出物浮窗的内容不再贴着边框，留出统一内边距；④ 三种布局、多档窗口尺寸（含矮窗口与窄窗口）都验过，页面既不空留也不多滚动条。
- 做法：先做真实几何探针再定常量，别凭感觉调数字。滚动的真凶不是 `.chat-grid` 本身：右栏配置表单固有 2272px，作为 `.layout` 这一行 grid 的内容把行高顶到 2272，实测问答页 `docH` 恒为 2412、与视口高度无关（可滚 1012~1612px）；其次旧公式的 174px 只算到「网格底边贴视口底边」，漏了 body 的 48px 下留白，所以永远能再滚 48px。改法：把「一屏」的三段留白抽成 `--fit-chat` / `--fit-chat-side` / `--fit-col` 三个变量（在 1280x800 / 1440x920 / 1920x1080 / 2560x1400 四档实测完全一致），网格高度与右栏限高共用；右栏改成 `position: sticky` + 限高内滚（限高正好与网格底边对齐），`.layout-side` 的抽屉态补 `max-height: none` 免得被截短；左导航吸顶从 `top: 20px` 改成 88px（顶栏 76 + 呼吸 12）并限高，旧值会让导航滚上去躲进顶栏里；浮窗内边距加在 `.chat-drawer` 本体上，标题栏/分隔线/滚动条一起内缩。窄屏单列回退不再用 `height: auto`（否则窄窗口下方会白留一大条），改为单列 + `auto minmax(0,1fr)` 两行，仍走一屏公式。
- 文件：web/static/style.css、tests/check_vue_chat_ui.py（新增 7 条几何断言）
- 影响：纯前端，刷新页面即生效。回归：check_vue_chat_ui.py 42 项全过，check_vue_settings_ui.py 47/47、check_vue_tasks_ui.py 38/38、check_vue_stats_ui.py 与 check_vue_shell_ui.py 全过、check_layout_ui.py 12/12；实机量过三布局 × 六档视口 `overflow` 全为 0。注意：窗口极窄（<1100px）且用三栏经典布局时，右栏配置会折到主列下方，那种堆叠排版本来就是可滚的。

## 2026-09-30 14:25 · 优化 · 缺陷修复步骤条收进模块内；长任务作业 / 需求开发 / 接口联调 / 代码测试 / 扩展能力五个模块布局改版

- 内容：① 缺陷修复的五步流水线步骤条从「顶栏下方的整页横条」移进模块内部（`#pane-defect` 顶部），侧边工作台布局里左侧菜单不再被它压下去；② 需求开发、接口联调、代码测试三个页签从「单列铺满」改成宽屏两栏：大文本区（设计稿结构 / 接口文档 / 文件预览）占左、参数占右，预览区不再是空态时一条 40px 的深色细条；③ 长任务作业页从六个面板单列直落约 2270px 压到约 1670px——作业表单收成「左参数右任务描述」，工作项与实时时间线、人工介入与历史作业两两并排，看板四张角色卡从 3+1 排成整行四张；④ 扩展能力的「技能」与「MCP 服务」并排；⑤ 模块里的说明条从带框小卡片降级成左侧引线式脚注，浅色主题下说明文字改用 `--text-dim` 保证对比度。
- 做法：先用探针量几何再改，不凭手感。步骤条用 `DefectPane` 的具名插槽承载：`el-steps` 仍写在 `App.vue`（`id="stage-flow"` 字面量与 `.stage` 状态类都是界面用例的契约），`v-show` 留在 `el-steps` 自己身上——`check_vue_shell_ui` 读的就是它自身的 computed display，外面再包一层会让「非缺陷页签不显示流程条」失效。两栏布局按各页契约分两种实现：需求开发与长任务作业的 `.field` 必须是面板的**直接子元素**（`check_layout_ui` 的 `:scope > .panel > .field` 断言），所以给面板本体套 CSS Grid 并逐个子元素写死 `grid-area`（自动排布的游标从第 1 行开始扫，会把裸在外面的 `.field` 塞进标题行右侧的空格）；接口联调与代码测试没有这条约束，直接用包一层的 `.mod-main` / `.mod-side`。双栏默认走 `@media (min-width: 1200px)`，窄窗口落回单列。两栏顶端原本差 12px——紧跟 `.panel-head` 的子块被那条「相邻子块 12px」通配规则清成了 0 外边距，补回来时选择器要写到两级 id 才压得过它。看板网格 `auto-fit` 的最小宽从 290px 降到 200px：1130px 宽下原值只切 3 列，四张卡排成 3+1，降下来后空轨道被折叠、四张卡平分一行。
- 文件：frontend/src/App.vue、frontend/src/views/DefectPane.vue、ReqdevPane.vue、ApiDebugPane.vue、CodeTestPane.vue、TeamPane.vue、web/static/style.css、CHANGELOG.md
- 影响：纯前端，`web/static/style.css` 运行时直读（CSS 改动刷新即生效），Vue 模板改动用 `npm run build` 产出新产物。回归：`tests/check_vue_all.py` 14 个用例非零退出 0 个（含 LEGACY 的 `check_panel_fold` / `check_layout_ui`）；七档视口（1600/1400/1280/1200/1100/1024/900）页面与各 pane 横向溢出全为 0，双栏在 1200px 仍成立、1100px 及以下回落单列。

## 2026-09-30 15:10 · 改进 · 顶部导航布局：页签搬进顶栏（标题右侧），装不下时两端出箭头

- 内容：① 切到「顶部导航」时，页签不再单独占主列顶上的一行——搬进顶栏、紧贴「ONES 前端研发助手」右侧，主列直接从内容开始（白省一行约 56px 高度）；② 9 个页签在窄窗口装不下时改为横向滚动，两端浮出左右箭头，且只在真的还能往那个方向滚时才出现（停在最左就没有左箭头）；③ 切页签后活动页签会自动滚进可视区，不会被浮动箭头压住；④ 三栏经典与侧边工作台布局一个字没变。
- 做法：① 顶栏里那套页签是自己渲染的一组按钮，不是把 Element 的页签头搬上去（el-tabs 不提供头部插槽位置），点击仍走同一个 `switchTab`，`#tabs` / `#pane-*` / `window.switchTab` 三个既有契约原样保留；`top` 布局下只把主列的 `#tabs > .el-tabs__header` 藏掉——pane 内容在 `.el-tabs__content` 里，整表藏会把内容藏空（与 side 布局同一条注意）。② 箭头做成绝对定位的浮层、**不占布局宽度**：占位的话「箭头出现 → 条变窄 → 更该出现」会单向自锁，反向又在阈值附近反复翻，ResizeObserver 会被自己触发成死循环；浮层化之后显隐不改变任何盒模型，回路从根上没了。③ 溢出判定只用 `scrollWidth > clientWidth` 与 `scrollLeft` 的两端位置，滑动方向那一枚天然自动隐藏；切页签 / 换布局后等一次 `nextTick` 重量并把活动页签带回可视区（限方向内缩 30px 避开箭头）。④ 视觉沿用主列页签那一套「凹槽 + 浮起胶囊」与既有变量，没有另起配色；两端用容器伪元素做渐隐，被切掉的半个页签读起来是「后面还有」而不是「字被切了」。
- 文件：frontend/src/App.vue、web/static/style.css、tests/check_vue_topbar_tabs.py（新，28 项）、tests/check_vue_all.py（清单 +1）
- 影响：纯前端，`web/static/style.css` 运行时直读、Vue 模板改动用 `npm run build`（新产物 `app.DLANzzq3.js`）。回归：新增用例 28/28；`check_vue_shell_ui` / `check_vue_migration` / `check_vue_chat_ui`（含三布局「问答页一屏装下」）全过；1440 / 1760 / 1100 三档视口页面横向溢出均为 0；`check_vue_all.py` 执行清单由 14 条增至 15 条。

## 2026-09-30 16:40 · 改进 · 删掉三栏经典布局；问答与配置的高度改成「链式自适应」

- 内容：① 整体布局从三种减为两种——只留「侧边工作台」（新默认）与「顶部导航」，三栏经典连同它的常驻右栏、竖排展开条一起删除；配置栏在两种布局下统一成右侧抽屉（顶栏齿轮开合，遮罩点击与 Esc 都能关，从抽屉跳「配置」页签时自动收抽屉）。② 问答模块不再用写死的高度：占满主列剩余空间，视口多高就多高，矮到 360px 地板以下才整页滚。③ 配置模块跟其它模块一致——按内容自然流、整页滚动，不再有只有这一页才出现的内层滚动条。
- 做法：① 高度不再靠「100vh − 实测常量」。旧实现把顶栏/页签条/留白量成 `--fit-chat: 176px`、`--fit-chat-side: 121px`、`--fit-col: 108px` 三个常量去减，换布局就对不上——顶部导航把页签条藏了却仍按 176 减，实测问答网格下方白留 55px。改成一条真实的高度链：`#app`(flex 列, min-height 100vh) → `.topbar`(flex:none) → `main.layout`(flex:1, grid 行 1fr) → `.col-main`(行 1fr) → `#tabs` → `.el-tabs__content`(行 1fr) → `.tabpane.active` → 面板。两个坑必须记住：a) 轨道只写 `1fr`（= minmax(auto,1fr)）不能写 `minmax(0,1fr)`，auto 最小值才会把内容固有高度往上传、让长页面正常顶长出滚动条；b) Element 给 `.el-tabs__content` 自带的 `overflow:hidden` 会让自动最小尺寸归零、长内容直接被裁掉，必须显式改回 `visible`。另外 `.tabpane.active` 补 `align-content: start`，压住 grid 默认的 stretch——否则 pane 被拉到一屏高之后多余空间会摊给每条 auto 行，内容不长的模块里每张卡片都被拉高、中间全是空白。② 三栏的常驻右栏之所以要单独限高内滚，是因为它和问答网格必须底边对齐；抽屉不参与页面高度之后这组约束整体消失，三个常量和 `.col-side` 的 sticky 一起删。③ 窄屏（<1100）侧边导航收成顶部横排带并转 `static`：sticky 的活动范围是它自己的 grid 区域，行高 auto 时区域和自身一样高，吸了也挪不动。④ 两种布局都由自己的导航承担切页签，`.el-tabs__header` 恒为 `display:none`，但 DOM 保留——`#tabs` / `#pane-*` / `.el-tabs__item` 是一批界面用例的选择器契约。
- 文件：frontend/src/App.vue、frontend/src/composables/useLayout.js、useConfigDrawer.js（新，替代 useSidebar.js 已删）、frontend/src/views/SettingsPanel.vue、ConfigPane.vue、ChatPane.vue、web/static/style.css、tests/check_vue_chat_ui.py、check_vue_settings_ui.py、check_vue_shell_ui.py、check_vue_topbar_tabs.py
- 影响：纯前端，`npm run build` 后刷新即生效。老用户 localStorage 里存的 `ui-layout=tri` 会被识别为废弃值并回落到侧边工作台。回归：`check_vue_chat_ui` 全过（含两布局「一屏装下 + 网格占满剩余高度」与抽屉开合/内滚/Esc 四组新断言）、`check_vue_settings_ui` 56/56（含「配置面板内没有第二条滚动条」）、`check_vue_shell_ui` 15/15、`check_vue_topbar_tabs` 33/33；另用探针量过 7 档视口 × 2 布局 × 9 个模块：问答页 1280×800 至 2560×1400 页面溢出均为 0 且网格底边贴内容区底边，全模块无内容被裁、无横向溢出。

## 2026-09-30 17:13 · 修复 · 全模块响应式体检：双栏断点改按主列宽度判定等五项

- 内容：① 需求开发 / 接口联调 / 代码测试 / 长任务作业 / 扩展能力这五个模块的双栏，现在看的是**主列实际多宽**而不是屏幕多宽——1280 笔记本用侧边工作台时不再被劈成两个 ~480px 的窄栏，而是自动落回单列；② 问答与弹窗正文里的 markdown 表格有了自己的横向滚动容器，宽表不再把整条消息流撑出一条横向滚动条；③ 产出物详情弹窗宽度补齐（原来是 Element 默认的 50%，1600 视口只有 800px 看不了 diff），与另外四个弹窗同口径；④ 长任务角色卡的「职责」文案被两行截断后可以悬停读全文（原来没有任何找回途径）；⑤ 720 宽时缺陷修复筛选行溢出 5px、页面多一条横向滚动条，已消除。
- 做法：① 双栏阈值原来是 `@media (min-width: 1200px)`，但侧边工作台会先吃掉左导航 212 + 间距 18 + 页面留白 56 = 286px，按视口判定的话 1200~1460 这一段全都「断点说够宽、实际不够」。改成给 `.col-main` 加 `container-type: inline-size; container-name: main`，两处断点换成 `@container main (min-width: 1120px)`——侧边布局要视口 ≥1406 才双栏，顶部布局 ≥1176 即可。上容器查询前先实测确认过一件事：`contain: layout` 在 Chromium 下**不会**把 `position: fixed` 的后代改成元素内定位（问答页的产出物抽屉与遮罩仍以视口为基准，`right` 间距 28、遮罩宽 1440），否则这批浮层会全部错位——这条务必留意，换成 `contain: paint` 就会踩雷。② 表格滚动容器由 markdown 渲染器直接套一层 `.md-scroll`（`format.js` 手写的渲染器，不引第三方库），比 `table { display: block }` 稳——后者会让表格按可用宽度重新收缩，反而不溢出也不滚。消息流本身有 `overflow-wrap: anywhere` 兜着，真正会溢出的是没有这条规则的弹窗正文，一处改动两边都覆盖。③ 窄屏那条 `@media (max-width: 860px)` 只放宽「行内标签可折 + 长字段独占一行 + 输入框 min-width 归零」，不动 `.run-form` 已有的 flex-wrap。
- 文件：web/static/style.css、frontend/src/utils/format.js、frontend/src/components/ArtifactModal.vue、TeamAgentCard.vue、tests/check_layout_ui.py（+2 项双栏断点用例）、tests/check_vue_tasks_ui.py（+1 项弹窗宽度）、tests/_tmp_resp_audit.py（新，体检探针）
- 影响：纯前端，`npm run build` 后刷新即生效（新产物 `app.Dkpqied9.js`）。容器查询要求 Chrome 105+ / Safari 16+ / Firefox 110+，本项目跑在桌面浏览器与 Playwright Chromium 上，满足。回归：`check_vue_all.py` 15 个用例全过（`check_layout_ui` 由 12 项增至 14 项）；高度链探针 7 档视口 × 2 布局 × 9 模块仍 ALL-OK（问答页零溢出、无内容被裁）；响应式体检复量 6 档宽度 × 2 布局，横向溢出仅剩 0 处（此前 720 宽缺陷页 1 处）。

## 2026-09-30 17:33 · 修复 · 大屏下模块面板与产出物面板之间的大空洞（高度链过度生效）

- 内容：需求开发 / 接口联调 / 代码测试这类模块里，「表单面板」和下面的「产出物面板」之间空出一整块空白，屏幕越大越夸张——2560×1400 实测 603px、2000×890 实测 93px、1440×900 也有 103px，看上去就像模块内部间距失控。现在两块面板恒定 18px 相邻，多余空间留在页面最下方（与其它模块一致的自然流）。
- 做法：这是上一条「高度链」改造留下的副作用，根因两条，都得记住。① `.col-main` 的第一行写成了 `grid-template-rows: 1fr`，本意是让问答页吃满剩余高度，但它对**所有**页签生效——`#tabs` 被拉到整屏高，而产出物面板 `#artifact-panel` 在 `.col-main` 里是 `#tabs` 的**兄弟节点**（不是 pane 的后代），于是被推到那一行的最底部，中间全是 `#tabs` 自己的空高度。`.tabpane.active` 上的 `align-content: start` 只把空洞从「卡片之间」挪到「pane 内容下方」，并没有消掉它。改法是把「占满」变成显式名单：`tabs.js` 新增 `FILL_TABS = ['chat']`，App.vue 据此给 `.col-main` 挂 `pane-fill` 类；基础态干脆**不写** `grid-template-rows`（隐式行按内容高 + `align-content: start`），只有 `.pane-fill` 才给 `1fr`。② `#artifact-panel` 自带 `margin-top: 18px`，和 `.col-main` 的 `gap: 18px` 叠成 36px，去掉。
- 文件：frontend/src/components/tabs.js、frontend/src/App.vue、web/static/style.css、tests/check_layout_ui.py（+2 项：大屏无空洞、问答页仍占满）
- 影响：纯前端，`npm run build` 后刷新即生效（新产物 `app.BTnUuet0.js`）。⚠️ 以后再加「占满型」模块只要往 `FILL_TABS` 里加名字；反过来，任何给 `.col-main` / `#tabs` 整行加 `1fr` 的改动都会把这个空洞带回来，`check_layout_ui` 的那条断言就是拦这个的。回归：`check_vue_all.py` 15 个用例全过（`check_layout_ui` 由 14 项增至 16 项）；高度链探针 7 档视口 × 2 布局 × 9 模块仍 ALL-OK，问答页在 2560×1400 下依旧占满 1262px、页面零溢出。

## 2026-10-01 19:30 · 改进 · 六个任务模块改版：输入进右侧参数抽屉，主区只放过程与产物

- 内容：缺陷修复 / 长任务作业 / 需求开发 / 接口联调 / 代码测试 / 扩展能力这六个模块，主体内容改成只展示**大模型的处理过程与最终产物**（运行日志、设计稿结构、文件预览、子 Agent 看板、时间线、提案与产出物列表），一次性输入（工单范围、作业参数、Figma 链接与需求描述、接口文档、目标文件路径、技能与 MCP 表单）整体搬进右侧**参数抽屉**，主区顶部留一条「参数摘要 + 修改参数」入口。① 抽屉**默认打开**（第一次进模块时主区还没有产物，此时要填的就是参数），点「运行 / 生成 / 开始作业 / 拉取设计稿」后自动收起，让过程和产物占满整宽；② 摘要条把关键参数摊成 chip，没填的项起告警色，不开抽屉也能看清这次要跑什么、也看得出漏了什么；③ 长任务的「暂停 / 继续 / 重规划 / 跳过 / 终止」刻意留在主区（是对着看板用的运行中控制，不是输入参数），且空闲时整行隐藏；④ 扩展能力的两块列表成为主体，注册表单只在点「新增 / 编辑」时以抽屉出现。
- 做法：① 新增 `useModuleParams`（模块级单例）+ `ParamDrawer`（抽屉外壳）+ `ParamBar`（摘要条）三件套。抽屉 key 支持 `页签名` 或 `页签名:槽位`（扩展能力 = `extensions:skill` / `extensions:mcp`），**同页签内互斥**——它们占的是同一个右侧位置；外壳只按 `anyOpenFor(页签)` 判断要不要让位，一个页签里有几个抽屉、叫什么名字都不需要改外壳。② **抽屉默认打开，所以必须做成非模态**：模态浮层默认打开等于常年盖住半屏。宽屏下不铺遮罩，改由 `.col-main.params-open { padding-right: 436px }` 让出空间，过程与产物始终可见；视口 ≤1100px 让不出这 436px 才退回「遮罩 + 浮层」的模态形态（点遮罩可关）。③ 显隐走项目既有的 `.hidden` 契约而不是配置抽屉那套 `visibility + transform`：关着的抽屉不该留在可访问性树里抢 Tab 序，而打开时的滑入动画靠 `animation` 在 `display:none → 显示` 时天然重播，效果一样。④ 扩展能力沿用 `useExtensions` 里已有的 `skillFormVisible` / `mcpFormVisible` 作为唯一事实来源，加**双向 watcher** 与抽屉同步——只连单向会卡死：用户直接关抽屉时表单仍是 visible，下次点「新增」值没变化、watcher 不触发，抽屉再也打不开。⑤ 顺手修掉改版暴露的两处：主列被抽屉让位后窄了 436px，看板四张角色卡里的角色名被右侧阶段徽标挤成一字一行（「决/策/官」竖排），给 `.team-agent-name` 补 `flex: none` 并把让位责任交给可省略号截断的徽标；产出物空态与长任务空态里「填**左侧**信息点」的指路文案改成指向参数抽屉。⑥ 上一轮为「左输入 / 右参数」双栏写的那套样式（`#req-panel` / `#api-panel` / `#test-panel` / `#team-run-panel` 的 grid-area、`.mod-main` / `.mod-side` / `.grow-area` / `.team-params`）整体删除——输入不在主区了，留着就是没人认领的死样式；容器查询现在只服务长任务的 12 栏谱系与扩展能力的两块并排。
- 文件：frontend/src/composables/useModuleParams.js、frontend/src/components/ParamDrawer.vue、ParamBar.vue、frontend/src/views/{DefectPane,TeamPane,ReqdevPane,ApiDebugPane,CodeTestPane,ExtensionsPane}.vue、frontend/src/components/ArtifactsPanel.vue、web/static/style.css、tests/check_layout_ui.py、tests/check_vue_extensions_ui.py
- 影响：纯前端，`npm run build` 后刷新即生效（新产物 `app.Czx7_4X7.js`）。⚠️ 两个契约变了：① 保存/取消按钮从 `#skill-form` 内部搬进抽屉动作条，用例不再用 `#skill-form button.btn-primary` 这种结构选择器，改用新增的 `#btn-skill-save` / `#btn-mcp-save`；② 长任务的干预按钮组空闲时带 `.hidden`，用例要在跑起来之后才点它们。`#skill-form` / `#mcp-form` 的 `hidden` 类契约原样保留。回归：`check_vue_all.py` 15 个用例全过（`check_layout_ui` 新增「主区无输入字段 / 抽屉默认打开 / 让位 436px / 干预按钮留在主区且空闲隐藏」等 6 项，`check_vue_extensions_ui` 45/45）；探针实测 1440 / 2560 / 1000 三档宽度 × 六个模块：无横向溢出、无内容被裁，让位在宽屏 436px、≤1100 归 0 并出遮罩。

## 2026-10-08 16:00 · 新增 · 缺陷修复改成 agentic 闭环：补丁在仓库外的沙箱里真跑验收，读真实报错后自己改下一版

- 内容：缺陷修复不再是「捞六个文件窗口 → 让模型一次吐出补丁 → 跑一个和补丁无关的静态闸门」。现在修复阶段是一个闭环 agent：模型自己用 `repo_grep` / `repo_read` 查代码、交候选补丁，系统把补丁落到**仓库外的临时副本**里真跑验收命令（lint / tsc / 测试），把**真实输出**连同一份「相比基线转绿了哪些、新弄坏了哪些」回灌给它继续改，直到跑绿或认输。默认预算 8 轮 / 6 分钟，同一验证结果连续出现 3 次即判定不收敛、停止烧 token 转人工。提案里带完整修复轨迹（每轮的文件、退出码、输出原文），界面用四种结论徽标区分「沙箱已验证 / 全绿但基线本就绿 / 未经验证 / 未收敛」。安全契约不变：人工审批仍是唯一写仓库入口。
- 做法：① 新增 `scripts/sandbox.py`。后端两条：有 git 用 `git worktree add --detach` + 把**工作区未提交改动/未跟踪文件**覆盖上去（否则补丁的 SEARCH 基准不是用户当下看到的代码；未跟踪**目录** git 只报一条 `src/deep/`，要自己摊成文件），非 git 或建不出来时退回按 `git ls-files` 逐文件拷贝。`node_modules` 不拷，改链接（Windows 用 `mklink /J` junction，普通权限即可；链接失败要在说明里点明「缺依赖型失败与补丁无关」，否则模型会照着假报错去改代码）。命令执行两条硬规矩：白名单 + **输出重定向到临时文件而不是管道**（`capture_output=True` 超时只杀直接子进程，npm→node→tsc 的孙进程仍握着管道，`communicate` 永远等不到 EOF——这是本项目踩过的 80 分钟挂死）。超时杀整棵进程树。② 新增 `scripts/fix_agent.py`。工具协议沿用问答那套（中转站不一定支持原生 function calling），但**交补丁不走 JSON**——直接输出 SEARCH/REPLACE 块由 `patch_engine.parse_blocks` 识别，省掉让模型把代码转义进 JSON 这一步（`chat._as_tool_call` 的 3000 字符上限也撑不住真实补丁）。每轮验证结果压成「错误指纹」（ANSI 清掉、数字归一为 N、盘符统一），这是判断「卡在同一个错误上」的唯一依据；`max_stall` 到点即停。最终结论取**历史最好的一次**而不是最后一次：预算耗尽前跑绿过的补丁不能因为最后一轮更差就说成失败。③ 接线：`pipeline._process_one` 里定位阶段降级为「种子线索」（结论 + 嫌疑文件 + 未验证的候选补丁都进 prompt，但明确标注以模型自己查到的为准），页面截图与 console 报错**前置**到动补丁之前，成为循环唯一能拿到的运行时证据；`verifier.describe()` 新增多模态截图判读。④ 顺手修掉一个原先说不出口的问题：旧闸门的 layer-1 命令是 `cwd=目标仓库` 跑的，而补丁此时只在内存里——所谓「闸门通过」证明的是「仓库现状通过」，与这份补丁改了什么无关。现在拿得到沙箱结果时闸门直接采用沙箱（`gate.level = "sandbox"`），拿不到才退回旧路径。⑤ 沙箱不撒谎：建不出来 → `sandbox_unavailable`；没有可跑命令 → `unverified` 并去 `node_modules/.bin` 里找出可配的检查器写成建议（云枢那批没有 type-check/lint 脚本的多包仓就是这种情况，缺的只是把 `npx vue-tsc --noEmit` 填进 gate.commands）。
- 文件：scripts/sandbox.py、scripts/fix_agent.py（均新增）、scripts/pipeline.py、scripts/proposal.py、scripts/verifier.py、web/state.py、web/routers/{config,pipeline}.py、frontend/src/{utils/labels.js,utils/proposalRender.js,composables/useDefect.js,composables/useSettings.js,components/ProposalCard.vue,components/ProposalModal.vue,views/ConfigPane.vue}、web/static/style.css、config.yaml.example、README.md、tests/check_fix_agent.py（新增，11 组）
- 影响：需重启后端 + 刷新页面。① agentic 循环按工单计费，token 是原来的数倍（8 轮上限，跑绿即提前收口），配置 `agent.enabled: false` 可整体退回旧链路，Mock 模式下本来就不会进循环；② 提案 JSON 多了 `agent` 段（轨迹可能上百 KB），老提案没有该字段时界面显示「来自旧的一次成型链路」；③ 新增 `SANDBOX_DIR` / `AGENT_ENABLED` 环境变量，沙箱副本落在系统临时目录、跑完回收；④ 白名单额外拦掉解释器内联代码（`python -c` / `node -e`）——放行它等于白名单形同虚设。⚠️ 一个被集成用例抓到的真 bug 值得记住：`_try_patch` 原先漏了 `attempt["checks"] = checks` 这一行（现已回写），而单元用例当时全绿——断言都打在 `verification` 上，没人在 `checks` 上较真，于是回喂给模型的反馈照「没有可跑的验收命令」分支发了一句假提示、闸门也静默退回旧路径。第 11 组 pipeline 接线用例就是为堵这类「模块自己对了、接线接错」而加的。回归：`pytest` 安全子集 11/11（新增 `check_fix_agent`）；`frontend` eslint 0 error、`vite build` 通过。

## 2026-10-08 17:00 · 新增 · 复现环节：修代码之前先写用例跑红，`verified` 从此分三档证据强度

- 内容：上一条的「沙箱真跑」有个没说透的问题——`lint` / `tsc` 从红变绿证明的是「没改坏构建」，不是「这条缺陷修好了」。现在修复循环在动代码之前必须先交一个**针对工单现象的复现用例**：它被写进沙箱、在**未修复**的代码上跑一次，**必须红**；现在就绿会被打回重写（默认 2 次，仍复现不出就放弃该环节并在提案里如实写明）。判据成立后，每一轮补丁的验证都带上它一起跑，转绿才算最强的那一档。`verified` 现在标清来源：`repro-test`（单测从红转绿）> `repro-assert`（机械断言脚本从红转绿）> `gate-command`（只有验收命令从红转绿）。跑器探测顺序：配置的 `agent.repro.command` → package.json 测试脚本 → `node_modules/.bin` 里的 vitest/jest/mocha → 退化成可执行断言脚本。复现用例不进提案补丁（补丁引擎不支持新建文件），自动挂成 `type=repro` 的产出物，在「缺陷修复 → 复现用例」面板单独采纳；界面还新增复现用例原文、每一次运行的真实输出、以及证据强度提示。
- 做法：① 新增 `scripts/repro.py`：跑器探测、命令模板渲染、用例落点校验、以及**三态判定**——`red` / `green` / `broken`。`broken`（Cannot find module、No test files found、SyntaxError、npm ERR!、被白名单拒绝）不算红：一个 import 都失败的文件永远是红的，拿它当判据会得到「永远修不好」的假结论。② 两条硬闸门写在代码而不是提示词里：**现在就绿的用例必须打回**；**补丁里出现复现用例路径直接拒绝**（防「改温度计说退烧」，模型确实会为了拿绿去改用例）。基线折算时显式补 `repro=False`——用例是基线跑完之后才创建的，不补这一笔红→绿的比较会把它当成「基线就绿」，`fixed` 里永远不含它。③ 复现要占轮次，开启时 `max_rounds` 自动 +2，否则等于把修代码的机会全让给写用例。④ 产出物那条路：`ArtifactStore.create` 的 id 只精确到秒，两条工单同一秒各挂一份会**静默覆盖**（覆盖掉的是用户可能审了半天的一份文件正文），改成在类级锁内探测 + 加 `-2` 后缀避让；探测与写入必须同锁，分两步并发双方都挑中同一个 id。⑤ 重跑去重按「路径 + 正文」比对，只回看最近 200 份（`list` 已按新→旧排），别为了去重读遍全量。⑥ DefectPane 挂 `ArtifactsPanel(id-suffix="repro")` 并自己 `loadArtifacts('repro')`：App.vue 的全局面板按 `ARTIFACT_TAB_LABEL` 决定显隐，defect 页签不在表里，不自己拉就永远是空的。
- 文件：scripts/repro.py（新增）、scripts/fix_agent.py、scripts/pipeline.py、scripts/artifact.py、web/state.py、web/routers/config.py、frontend/src/{utils/proposalRender.js,composables/useSettings.js,views/ConfigPane.vue,views/DefectPane.vue}、config.yaml.example、ui_settings.example.json、README.md、tests/check_repro.py（新增，7 组）
- 影响：需重启后端 + 刷新页面。① 缺陷修复的 token 再涨一档（多用例那 1~2 轮）；`agent.repro: off` 可整体退回上一条的行为；② `artifacts/` 目录会开始收到 `type=repro` 的产出物，不采纳就不进仓库；③ `ArtifactStore.create` 的 id 规则变了（同秒冲突时带 `-2` 后缀），按 `<kind>-<时间戳>` 精确匹配 id 的脚本要注意；④ 云枢那批没有测试跑器的仓库会走「可执行断言脚本」退化路径，界面明确标成弱证据——它不是单测，别当单测信。⚠️ 复现命令走沙箱白名单：配置 `agent.repro.command` 时别写内联代码（`python -c` / `node -e` 一律被拒）。回归：`check_repro.py` 7 组全过、`check_fix_agent.py` 12 组全过（为保持轮次断言确定性，那批显式 `repro: off`）；`pytest` 安全子集全绿；`check_panel_fold` / `check_vue_defect_ui` / `check_vue_settings_ui` 全过（新面板插在提案与统计之间，四面板相对顺序不变）；eslint 0 error。

## 2026-10-08 17:40 · 新增 · 页面复验（在沙箱里起 dev server 看现象消失没有）+ 同类历史缺陷先例注入

- 内容：两条补齐「命令级证据 ≠ 修好了」的缺口。① **页面复验**：第一次拿到绿之后，循环会在**打了补丁的沙箱**里起一次 dev server（自动挑空闲端口，不抢用户自己开着的 3000/5173），按工单同一套复现操作再拍一张图，把修复前/修复后两张图一起交给模型判 `gone / same / unclear`，外加 console 报错的前后差集。判读 `same` 时这次的绿**不算收敛**——结论降级成新的 `symptom_persists`（徽标「命令全绿，但页面现象仍在」，标需人工），并把判读原文回喂给模型继续改，下一版必须重新复验（一条缺陷最多起 2 次服务）。服务起不来 / 白屏 / 缺后端接口 → 标 `unusable` 并写进提案说明，算证据缺席而不是通过。提案详情新增第三格截图（补丁后·沙箱里）与页面复验分区（起的命令、URL、判读依据、新增/消失的报错、服务日志尾部）。② **同类历史缺陷先例**：每次开工前按目录段/关键词/标题挑出最多 3 条历史提案注入，**连人工拒绝过的和本工单上一次的失败尝试一起给**，带当时结局与备注；注入了哪几条记进提案可追溯。知识卡片新增「修复循环结论」分区与 `fix_conclusion` / `evidence_via` / `human_decision` 三个 frontmatter 字段（`kb_version` 升到 3）。
- 做法：① `scripts/sandbox.py` 加 `spawn_server()`：与一次性 `run()` 的区别只有一件事——不等命令结束。日志同样写临时文件不走管道，环境变量给 `PORT`、命令行支持 `{port}` 占位（vite 用 `--port`、next 用 `-p`，与其猜不如让配置写明确），就绪靠轮询端口，进程提前退出就直接带退出码返回；`stop()` **等端口真的释放**才返回（不等会留下还在监听 127.0.0.1 的 dev server，也会让下一次复验抢到半死端口）。起服务同样过白名单，`python -c` / `node -e` 照样拒。`default_server_command()` 只认 vite/next/rsbuild/webpack/nuxt/react-scripts 这类能确定参数写法的技术栈，认不出就返回空 → 复验自动跳过，绝不瞎猜一个命令拿空白页当「修复后」。② 判读用 `verifier.judge_fix()`：两张图一起给，只许回答三种结论，并明写「白屏/看不出来必须 unclear 或 same」——视觉判读最容易产出的不是错判而是假自信。③ 复验次数用 `sandbox_server_max_checks`（默认 2）而不是「只跑一次」：只跑一次的话，一次误判就会永久压住后面真正修好的那一版；也不放开成每轮都跑，起一次 dev server 太贵。④ `scripts/precedents.py`：从 `proposals/*.json` 折出先例卡（当时改哪些文件、补丁原文、循环结论、人工结局与备注），按 mtime 回看最近 150 份；**结局与证据只能修饰一个已经非零的相关度**——第一版把它们当基础分加，结果「登录页图标错位」凭「被采纳过 +2」越过了门槛，被用例当场抓住。渲染封顶从最低分那条开始砍补丁正文（先例最值钱的是「当时的改法后来有没有被认」，diff 排第二）。⑤ 卡片只沉淀枚举值与计数，不搬模型散文：脱敏导出是按形状识别敏感串的（绝对路径、12 位混合 token、账号行…），往卡片里加大段自由文本等于给自检留一个静默漏洗的口子。
- 文件：scripts/sandbox.py（`ServerHandle` / `spawn_server` / `default_server_command` / `stop` 等端口释放）、scripts/fix_agent.py、scripts/pipeline.py、scripts/precedents.py（新增）、scripts/verifier.py（`judge_fix`）、scripts/kb.py、web/state.py、web/routers/config.py、frontend/src/{utils/labels.js,utils/proposalRender.js,composables/useSettings.js,views/ConfigPane.vue}、config.yaml.example、ui_settings.example.json、README.md、tests/check_repro.py（+4 组：页面复验四态、先例检索与封顶、起停与失败上报、pipeline 端到端）
- 影响：需重启后端 + 刷新页面。① token 与墙钟再涨（一次 dev server 启动最多 90s + 两次判读），不要页面复验就设 `sandbox_server: off`；② `agent.conclusion` 多了一个取值 `symptom_persists`（按未修好处理，走强制采纳通道需显式确认）；③ 知识卡片 frontmatter 新增三字段、正文多一节，老卡片是 `kb_version: 2` 且不会被回写，模式层重算后照常；④ 沙箱起服务要求仓库有可识别的 dev 脚本，云枢这类多包仓建议直接写 `sandbox_server_command: "pnpm --filter <子包> dev --port {port}"` + `sandbox_server_cwd`。⚠️ 沙箱页面复验只解决「能不能渲染」，不解决「后端数据从哪来」：内网应用要登录要接口，起得来但拿不到数据时会标 `unusable`，不会伪造成 `gone`。回归：`check_repro` 11 组、`check_fix_agent` 12 组、`pytest` 安全子集 13/13、`check_kb_patterns` 18/18、`check_kb_export` ALL PASS、`check_panel_fold` / `check_vue_defect_ui` / `check_vue_settings_ui` 全过、eslint 0 error、`vite build` 通过。

## 2026-10-08 18:10 · 修复 · 收掉两条 Vue 迁移遗留的空壳用例，断言搬进可直调的纯函数测试（顺带修好外链渲染）

- 内容：`tests/check_stages_ui.py` 与 `tests/check_kb_render.py` 是旧版原生界面时代的用例，靠 `page.evaluate()` 直调挂在 window 上的全局函数。Vue 收口时那层 debugBridge 被删，两条自此只报 `ReferenceError`，在仓库里躺了两个多月没人管——它们看着还"在"，实际什么也没守：「部分 invalid → warn、全 invalid → fail、拉取失败 → 后四步 off」这类分支判定，以及「代码块里的空行不能把 SEARCH/REPLACE 腰斩」这类渲染规则都随之失守。现在两条收掉，断言搬到 `frontend/tests/pure.test.mjs`（24 条，`npm test` 直调），并由 `tests/check_pure_js.py` 收进 pytest。顺带修掉一个用户可见的渲染缺陷：**知识卡片与问答正文里的 http(s) 外链以前根本点不动**，会原样显示成 `[文字](https://…)`。
- 做法：① 步骤条推导函数从 `useDefect.js` 抽到 `frontend/src/utils/stages.js`——它们本来就是纯函数，只是住在一个 import 了 vue 与 element-plus toast 的模块里，Node 直调会连带拉起整棵浏览器依赖树。`useDefect.js` 改成 import + 再 export（App.vue 与界面用例的取法不变）；`liveStage()` 里那份与 `stageAdvance()` 重复的推进逻辑合并成一份。⚠️ 写成 `export {…} from` 不产生本地绑定，本模块自己就调不到那几个函数（第一次改完 eslint 立刻报 `stagesRunning is not defined`）。② 空壳用例走系统回收站收掉，git 历史与回收站双通道可恢复。③ 包一层 Python 用例而不是让 pytest 直接收 .mjs：项目里 40 多个用例都是「脚本 + 退出码」的形状，照同一个形状进 suite 最省事；**缺 node 时判失败而不是退出码 2 当跳过**——静默不跑正是这两条当初烂掉的机制原因。另外断言「跑到的条数 ≥ 20」，防止有人把断言文件改空还全绿。④ 新写的渲染断言当场抓到外链那个缺陷：`renderMarkdown` 的链接正则字符类里没有 `:`，`https://` 永远匹配不上，注释写着「外链直接跳」是句空话；补上 `:` 与 `?=&%` 之后又发现整行是先 escapeHtml 再解析链接的，`&` 此时已成 `&amp;`，`;` 不在类里会让链接断在查询参数中间，一并补进字符类。转义在解析之前完成，所以引号与尖括号进不了 href，另加一条属性注入断言钉住这一点。
- 文件：frontend/src/utils/stages.js（新增）、frontend/src/composables/useDefect.js、frontend/src/utils/format.js、frontend/tests/pure.test.mjs（新增）、frontend/package.json（加 `npm test`）、tests/check_pure_js.py（新增）、tests/suite_pytest.py、tests/check_vue_all.py、tests/check_vue_defect_ui.py、tests/kb_fixture.py；收掉 tests/check_stages_ui.py、tests/check_kb_render.py
- 影响：纯前端与测试层，需 `npm run build` + 刷新页面。① 卡片/正文里的 http(s) 链接现在真的可点（外链新窗口）；② 链接字符类放宽后，「链接紧跟中文分号」这种极端写法可能把分号吃进 URL，只影响那条链接的跳转目标，不影响安全；③ 步骤条状态的单一事实来源变成 `utils/stages.js`，改状态取值要同步 style.css 的 `.stage.*` 类；④ pytest 安全子集多一条依赖 node 的用例（能构建前端就能跑）。回归：`pure.test.mjs` 24/24、`check_pure_js` 全过、`pytest` 14/14、`check_vue_all` 15 个 Playwright 用例全过、eslint 0 error、`vite build` 通过。

## 2026-10-08 18:30 · 修复 · `symptom_persists` 的提案以前闸门照判通过，采纳按钮直接可点

- 内容：上一条「页面复验」把结论降级成 `symptom_persists`（命令全绿但现象仍在），却漏了让它去影响闸门——这类提案仍停在 `pending`，「采纳」按钮亮着，只有点进去看轨迹才知道页面没过。而 README 与界面文案都写着「按未修好处理 / 走强制采纳需显式确认」，属于我自己把话说在前、代码没做到。现在这类提案落 `gate_failed`，不勾选风险确认后端直接拒。
- 做法：`pipeline._gate_of` 在拼沙箱闸门结果时，若 `agent.conclusion == "symptom_persists"` 就补一条失败项 `page-reverify`（`kind: "page"`，`output_tail` 放模型的判读原文，让「为什么没通过」在闸门区直接可见），而不是另开一套状态字段——审批矩阵、强制采纳勾选、角标口径全都自然复用。
- 文件：scripts/pipeline.py、README.md、tests/check_repro.py（第 12 组：闸门判未通过 / 提案落 gate_failed / 不勾风险后端拒且零写入 / 正常 verified 不被误伤）
- 影响：行为收紧——以前能直接点的「页面仍有现象」提案，现在要走强制采纳。回归：`check_repro` 12 组全过、`pytest` 14/14。

## 2026-10-09 09:30 · 新增 · 一键返工：自测「没修好」从此是下一次修复的硬判据，不是一条备注

- 内容：提案详情底部加「没修好，重修」按钮。选一个判据（现象完全没变 / 有变化但没修对 / 引出别的问题 / 环境或入口无法自测）+ 一句你实际看到的现象，后端按顺序做四件事：**撤销采纳**（若已采纳）→ 把反馈结构化记进提案（`rework.verdict/detail/note/at/undone`）→ 拒绝旧提案 → 带着这条反馈重跑该工单。重跑时它是**最高优先级判据**：复现用例必须能把反馈里描述的现象跑红，做不到就要在说明里写清卡在哪、需要什么信息，不许交一份「只让命令变绿」的补丁了事；旧版被拒的改法以「本工单上一次尝试」的身份进先例，明令不许重演。新提案记 `agent.rework_ref` 指回被返工的那一版，弹窗里有「人工返工判据」分区。
- 做法：① 判据做成枚举（`proposal.REWORK_VERDICTS`）而不是自由文本——这句话既进 prompt 当硬要求，又要参与返工率统计，前端下拉与它一一对应。② **顺序不能换**：先撤销采纳再重跑，否则新版补丁会叠在你已收进工作区的旧补丁上，SEARCH 基准就不是原始代码；撤销失败（漂移、分支不对、预检不过）就整体中止返工并返回 `need_undo_first`，宁可什么都不动，也不留「旧补丁还在、旧提案已拒」的半套状态。③ 重跑按**工单号回捞**最近一次返工（`pipeline.latest_rework`），而不是把参数从接口一路传进循环：命令行重跑、界面重跑、隔几天再跑，都会自动带上同一条反馈——返工记录不该只在点按钮那一次生效。④ 前端复用既有 `/api/run/stream`：返工端点只做「记账 + 撤销 + 拒绝」并立即返回，重跑由界面用现有流式通道发起，进度与实时日志全都照旧；流水线正在跑时不抢跑道（409），界面退回提示而不是排队。⑤ 顺带合并了三份重复的定位桩成 `stub_analyzer`（返工用例是第四个需要它的场景）。
- 文件：scripts/proposal.py（`REWORK_VERDICTS` / `rework_text`）、scripts/pipeline.py（`rework` / `latest_rework` / 注入返工反馈 / `_page_after_hook`）、scripts/fix_agent.py（返工判据段 + `rework_ref`）、web/routers/review.py（`POST /api/proposals/{pid}/rework`）、frontend/src/{composables/useProposalModal.js,components/ProposalModal.vue,utils/proposalRender.js}、README.md、tests/check_repro.py（第 13 组端到端返工）、tests/check_fix_agent.py（共享定位桩）
- 影响：需重启后端 + 刷新页面。① 新增的写盘动作只有「撤销采纳」这一步，且沿用撤销原有的全部前置检查（分支/漂移/预检）；② 旧提案被拒后仍完整留在 `proposals/` 与知识卡片里，返工率从此可统计；③ 「立刻带这条反馈重跑」默认勾选，会真实消耗模型额度，不想跑就在面板里取消勾选。回归：`check_repro` 13 组全过、`pure.test.mjs` 24/24、`pytest` 14/14、`check_vue_all` 15 个 Playwright 用例全过、eslint 0 error（62 条既有 warning 数不变）、`vite build` 通过。

## 2026-10-09 10:20 · 修复 · 首批真实工单暴露的四个坑：子包依赖没链上、推理截断没被认出、永久红基线让 verified 永不可达

- 内容：拿云枢 830-LUI 真实工单跑 agentic 闭环的第一批，一条都没"修好"——但原因全在管道，不在模型。四个都修了：① 沙箱只链了根 `node_modules`，`packages/*/node_modules` 全漏，`tsc` 报一片"找不到模块"，模型收到的是与补丁无关的假失败；② 中继把 `usage.completion_tokens_details` 抹平，推理模型烧光配额留下的空回答被诊断成"响应取值路径不对"，于是**加倍额度重试没触发**，整条工单白跑 600s；③ 结论只认「所有命令全绿」，而该仓 `tsconfig` 写了 `"types": ["jest"]` 却全仓没装 `@types/jest`，type-check 打什么补丁都是 rc=2 —— `verified` 在这类仓库里**永远不可达**；④ 上一版的 `_find_dep_dirs` 用 `glob('*/*/node_modules')`，会枚举 `node_modules/<每个包>/<每个包>` 几十万条目录项，慢且抛 OSError，而异常被 `except` 吞掉，表现就是"安静地少链依赖"。
- 做法：① 依赖目录扫描改成**逐层 scandir 且遇到依赖目录不下钻**（层数上限 3、每层 400 项），返回的必须是 `packages/admin/node_modules` 本身而不是父目录 —— 记成父目录会让链接步骤撞上 worktree 里已存在的源码目录（`dst.exists()` 为真）而静默跳过，这个二次 bug 是新写的 `[13]` 用例当场抓出来的。② 空回答的诊断尾巴改看两个更稳的信号：`choices[0].message` 里既无 `content` 也无 `reasoning_content`、或 `finish_reason=length`；尾巴必须带上 `_TRUNCATION_MARKERS` 的原话，否则 `ai_model` 的重试判据不匹配（有断言专门钉这条）。③ `conclusion_of` 多给一条出路：**无回归 + 复现判据从红转绿**即 verified，永久红项单列 `baseline_still_red` 且不算回归，反馈里明确告诉模型"别为了历史红项扩大改动"；没有复现判据时保持保守（不冒充 verified）。④ 该仓的 `always-fail` 场景做成用例第 [14] 组，锁住"命令从未变绿但结论可以是 verified"这条新语义。
- 文件：scripts/sandbox.py、scripts/ai_providers.py、scripts/ai_model.py（无改动，判据文本对齐）、scripts/fix_agent.py、README.md、tests/check_fix_agent.py（第 13 组）、tests/check_repro.py（第 14 组）
- 影响：需重启后端。① monorepo 的 type-check/lint 现在能真正解析到子包依赖，基线红项会显著变少；② 推理模型遇到配额烧在思考上会自动加倍重试一次（有界），不再整条白跑；③ **`verified` 的含义变了**：不再要求所有验收命令全绿，而是「不新增失败 + 复现判据转绿」，`baseline_still_red` 在提案里可见 —— 读结论时 `verified_via` 比 `verified` 这个词更重要；④ 建议顺手把基线修绿（该仓 `yarn add -D @types/jest`），type-check 就能从"永久红"变成真防线。回归：`check_fix_agent` 13 组、`check_repro` 14 组、`pytest` 14/14 全过。

## 2026-10-09 11:20 · 新增 · 后端归因成一等结论 + 契约护栏：不许拿容错代码代替查证，接口真相只读可探

- 内容：回答"会不会把后端缺陷也当前端问题硬修"——**会的**，首批真实工单里已经出现三种：① 把筛选项字段名改成推断出来的 `modelType`，并用交叉类型断言绕开 typings 不一致；② 详情接口结构与列表不一致，于是写归一化兼容层，`non_frontend` 却标成 False；③ 自己承认"沙箱内没有接口响应样本，无法确认字段"，然后把 `list/rows/items/data` 全列进候选。现在新增三个一等结论：`backend_issue`（有可核查证据，不产补丁）、`backend_issue_unverified`（拿不出证据，不能当定论）、`adapted_pending_backend`（前端兼容已跑绿、等上游契约，撤点写在 cleanup）。两条硬拦写在代码里：改请求契约必须有仓库内旁证、一次新增 ≥3 个响应字段读取必须有接口真相或引用；`as any` / `@ts-ignore` / 交叉断言会被列进提案「契约核查」给审批人看。另有只读 `api_probe`（只 GET/HEAD、只白名单主机、只回字段名与类型）与「一键回写 ONES 工单评论」的交接出口。
- 做法：① **引用会真去读那一行**：`repo_read` 把"文件不存在/越界/空文件"也当作文本返回（那是给模型看的友好提示），核查时不按前缀判无效，随手编个路径就能给"后端归因"盖章"有据"。② 旁证判定排除本次被改动的文件本身——只在自己改的文件里出现的字段名，正是"凭语义推断"的定义。③ 拦"全都兼容一遍"要按**点链整条**抓（`response.data.list` 得拿到 `list`；只匹配紧邻一个点会漏，于是最该拦的行为反而放行），同时把 `data`/`body` 这类**信封词**与数组方法一起排掉，否则"补两处 content/records"的正常改动会凑够 3 个被误拦——误拦的代价是模型学会绕检查，比漏拦更贵。④ 只认**赋值**：`_MEMBER_WRITE_RE` 原先连 `data.list` 这种读也算改契约，收窄成 `=(?!=|>)` / `+=` / `-=`。⑤ 跑绿后若这版动了契约面，循环**多花一轮专门问归因**，让"该后端改"和"前端先兼容"都有地方放，不再被挤进 `give_up` 那个筐。⑥ 探测只回字段名与类型，响应体不落盘、不进提案、不进卡片；交接评论同样只带结构与引用，必须显式 `confirm`、同一提案只发一次——这是全链路唯一写到目标仓库之外系统的动作。⑦ `adapted` 的结论映射要挪到 `best` 覆盖之后，否则会被打回 verified。
- 文件：scripts/apicontract.py（新增）、scripts/fix_agent.py、scripts/pipeline.py、scripts/proposal.py、web/routers/{review,config}.py、web/state.py、frontend/src/{utils/labels.js,utils/proposalRender.js,composables/useProposalModal.js,composables/useSettings.js,components/ProposalModal.vue,views/ConfigPane.vue}、config.yaml.example、ui_settings.example.json、README.md、tests/check_apicontract.py（新增 6 组）、tests/suite_pytest.py
- 影响：需重启后端 + 刷新页面。① `agent.conclusion` 多三个取值；② 拦下时按"当轮未构建"反馈，模型可能多耗 1~2 轮去拿证据；③ 默认 `api_probe: auto`，但没填被测应用地址就完全不发请求，介意可显式设 `off`；④ 回写工单评论是真写到 ONES，界面必须显式确认，同一提案只发一次。回归：`check_apicontract` 6 组、`check_fix_agent` 13 组、`check_repro` 14 组、`pytest` 安全子集 16/16、`check_panel_fold` / `check_kb_patterns` 18/18 / `check_vue_defect_ui` 全过、eslint 0 error、`vite build` 通过。

## 2026-10-09 12:00 · 重构 · 清掉五个临时产物：扫描器转正成 tools/，用例改写副本挪出 tests/

- 内容：仓库根与 `tests/` 下长期躺着五个 `_tmp_*` / `_rewired_*` 文件，靠 gitignore 兜着，分不清哪个是在用的。分开处理：**推送载荷扫描器转正**为 `tools/prepush_scan.py`（可复用，加了参数与退出码）；一次性脱敏脚本 `_tmp_scrub.py`（改写已落地，且文件里存着真实工单号，绝不该进仓库）与三份迁移期副本收进回收站。另改一处根因：`check_vue_all.py` 每跑一次就会在 `tests/` 里生成一批 `_tmp_rewired_*.py` 改写副本，删了还会回来。
- 做法：① 扫描器带上了 ref 范围参数与 `--worktree`，命中即非零退出，可以直接挂 pre-push；顺手修了它一个误报 —— 盘符正则原先会把 `https://…` 后跟中文识别成 `D:/云枢/...` 那样的内部路径，现在要求排除 `://`。② `check_vue_all` 的改写副本不能扔进系统临时目录：这些脚本用 `Path(__file__).resolve().parents[1]` 反推仓库根、还要 `from temp_server import ...` 拿 tests/ 夹具，扔出去两条都断。改成落在 `ROOT/.scratch/`（parents[1] 恰好还是仓库根）并显式传 `PYTHONPATH`，`.gitignore` 收一条目录规则，比散在 `tests/` 里的五个文件干净。③ 五个文件走系统回收站（可恢复），其中 `_tmp_scrub.py` 里那两条真实工单号只存在于未跟踪文件与本地 `knowledge_base/`，都已在忽略清单内。
- 文件：tools/prepush_scan.py（转正）、tests/check_vue_all.py、.gitignore、README.md；收掉 `_tmp_prepush_scan.py`、`_tmp_scrub.py`、`tests/_rewired_check_panel_fold.py`、`tests/_tmp_rewired_check_panel_fold.py`、`tests/_tmp_rewired_check_layout_ui.py`
- 影响：`tests/` 从此不再产生改写副本；跑 `check_vue_all.py` 的中间文件在 `.scratch/`（已忽略）。`tools/prepush_scan.py` 是新增的推送前检查，建议推送前跑一次。回归：`pytest` 安全子集 16/16、`check_apicontract` 全过；扫描器对当前 `origin/main..HEAD` 的命中经确认均为合成测试值（已把测试里的假内网地址换成文档段 192.0.2.10，避免自造误报）。

## 2026-10-09 11:55 · 改进 · 流水线占用从裸布尔升级成带身份的租约：看得见是谁在跑，并且停得掉

- 内容：回答"提示『已有流水线在运行』却看不到是哪条、也关不掉"这个卡了很久的死局。现在运行面板多一条**运行占用条**，实时显示「谁占着槽位」（缺陷流水线 / 试运行 / 任务 / 长任务作业）、正在处理**哪条工单**（第 i/N 条 + 标题）、**当前阶段**在做什么、**来源**（界面点的还是外部脚本发的）、**已运行多久**；右侧配「停止」按钮，请求停止后再出「强制解除占用」。顶栏状态药丸同步改成以服务端占用为准，被占着时不再显示上一次运行的成功文案误导"可以再跑"。409 的正文从一句空话变成一句可行动的说明：`已有任务在运行——试运行（拉取 + AI 定位）、当前阶段 fetch、来源 label-check、已运行 3 秒。可在运行面板点「停止」，或「强制解除占用」后重试试运行。`
- 做法：① 占用标记从 `_RUN_STATE = {"running": False}` 换成带身份字段的**租约**，新增 `_acquire_run` / `_release_run` / `_run_progress` / `_run_snapshot` / `_request_run_cancel`。顺手修掉一个真并发洞：老写法 `if running: 409` 与 `running = True` 分作两步，`/api/tasks/run` 两步之间还夹着 MCP 握手，两个请求能双双通过。② `_acquire_run` 返回的是**这一次运行专属的 `threading.Event`**，取消信号跟着租约走而不是跟着全局标记走——否则「强制解除占用」后新起的运行会被上一条流水线的僵尸线程读到同一个已置起的事件，或者反过来被它清掉占用。释放与写进度都比对事件身份，僵尸线程的收尾与进度回写全部作废。③ 取消是**协作式**的：`pipeline.run` / `probe` 在工单边界、`FixAgent` 在轮次边界读信号（新增结论 `cancelled`，进 `NEEDS_HUMAN`），刻意不在模型调用与沙箱命令中途砍——那会留下半截沙箱和半截提案；要立刻腾槽位是「强制解除」的职责，两件事不混在一个按钮里。④ 长任务作业本来就有自己的终止总线，把租约的取消事件翻译成 `bus.request_stop()`，一个停止按钮管得住两条路。⑤ **非流式的 `/api/run` 以前完全不传 emit**，这才是"外部脚本跑批量、界面什么都不知道"的根因；现在无条件挂一层进度镜像，同步接口也全程可见。⑥ 前端新起 `useRunLease`（3 秒轮询 `/api/run/status`）而不是复用 `useHealth`：`/api/health` 每次都要跑 git 预检，拿它当轮询载体会把整页拖慢。
- 文件：web/state.py、web/streaming.py、web/routers/{pipeline,tasks,team,review}.py、web/urls.py、scripts/pipeline.py、scripts/fix_agent.py、frontend/src/{composables/useRunLease.js,components/RunLeaseBar.vue,views/DefectPane.vue,App.vue,api/client.js}（新增前两个）、tests/check_run_lease.py（新增）
- 影响：需重启后端 + 刷新页面。① 新增 `GET /api/run/status`、`POST /api/run/cancel`（`{"force": true}` 为强制解除），`/api/health` 保留 `running` 并新增 `run` 对象；`/api/run` 与 probe 的返回多一个 `stopped` 字段，批量脚本据此知道结果不是全集。② 请求体可选 `source` 字段自报家门，多个脚本并行排查时能分清是谁占的。③ 停止只保证"下一次不再开工"，当前那一单的模型调用与沙箱命令仍会跑完，最坏等一轮。回归：`check_run_lease` 端到端 3 轮全绿（含 8 线程并发抢槽、僵尸线程不清新占用、异常路径必释放）；`check_apicontract` / `check_fix_agent` / `check_repro` / `check_origin_guard` / `check_render_escape` / `check_pure_js` 全过，`vite build` 通过。真实验证：对真实 ONES + 真实模型发起试运行，占用条如实显示，点停止后槽位 0 秒释放、立刻能跑下一次，并顺手用「强制解除」收掉了一个跑挂 103 秒的孤儿试运行。

## 2026-10-09 12:05 · 修复 · 步骤条 active 态：去掉背景框，改成主色 + 呼吸灯

- 内容：正在执行的那一步以前是被一个圆角背景框"圈住"，看不出在跑。现在没有背景框了——该步的序号圈、标题走主色，圈外一圈光环做 1.6 秒呼吸（明暗 + 微缩放），一眼就是"这一步正在执行"。顺手把另外几态的颜色补齐：已完成转绿、闸门未过黄、失败红、不适用整步压暗，五种状态彼此都分得开。
- 做法：根因不是配色，是**一段静默失效两个月的死 CSS**。`.stage-num` / `.stage-name` / `.stage-desc` / `.stage-arrow` 这四个选择器写的是迁移前的原生步骤条 DOM，Vite+Vue 收口时那套 DOM 换成了 `<el-step>`，再没有任何地方产出这些类——于是真正表达"正在跑"的 `stage-pulse` 呼吸灯挂在 `.stage.active .stage-num` 上彻底不生效，只剩 `.stage.active` 那条背景框还在生效，看就成了"框住当前步"。修法是把状态色全部重写到 el-step 的真实结构（`.el-step__head` / `.el-step__title` / `.el-step__icon`）上并删掉死选择器；呼吸灯只动 `opacity` 与 `transform`（避开"同色不同 alpha"的插值脏边，也让主色跟着风格预设自动变），并按 `prefers-reduced-motion` 关掉。已完成的一步显式压回 `--success`：Element 默认 finish 与 process 都是主色，不分开就分不清"已经过了"和"正在跑"。
- 文件：web/static/style.css
- 影响：刷新页面即生效（style.css 不走 vite 构建）。与上一条「运行占用」同源的一处提醒：**状态类绑对了 DOM，不等于样式打得中**——`check_vue_defect_ui` 当初补的就是"stageStates 算出来却没绑到 #stage-flow"，这次是它的下半场：绑上了，但 CSS 选的是已经不存在的子元素。回归：`check_vue_shell_ui` / `check_vue_defect_ui` / `check_panel_fold` / `check_layout_ui` 19/19 全过；浏览器实机量过 active 根节点 `background: rgba(0,0,0,0)`、`box-shadow: none`、`animation: stage-breathe 1.6s`，且隔半个周期取两帧 `::after` 的 opacity 不同（0.588 → 0.771），证明确在动。

## 2026-10-09 14:15 · 改进 · 步骤条跟着服务端占用走：别人发起的运行也看得见进度

- 内容：外部脚本或另一个标签页跑批量时，运行占用条显示得明明白白，顶部步骤条却整排静止——看起来像"没在干活"。现在步骤条由服务端租约推导：`拉取` 呼吸 → 取到工单后 `定位` 呼吸 → 修复循环那几轮落在 `生成提案` → `闸门` → `沉淀`，试运行则后三步恒为不适用。别人跑完时列表会自己刷出来，不用手动点「刷新」。
- 做法：① 新增纯函数 `stagesFromLease(lease)`（`utils/stages.js`，可在 Node 直调断言）：租约只给"当前在哪个阶段"这一个快照，所以推导规则是"之前的步 done、当前步 active、之后 idle"，而不是事件流那种逐步 start/done。② 阶段名映射**另立一张表** `LEASE_STAGE_IDX`，不复用事件用的 `LIVE_STAGE_IDX`：那张表面向"事件推进"，没映射上的名字（`verify` / `repro` / `agent`）忽略即可，前后事件会自己补格；这张表面向"快照"，同样的名字若不归位，脚本跑到修复循环那 7 轮时步骤条会退回「定位」，正是要治的那个症状。③ 本标签页自己在跑时**绝不抢**：SSE 事件流比 3 秒快照细得多，用快照覆盖它是降级。④ 外部运行结束时退回空态而不是"五步全绿"——我们手里没有它的结果数据，画成完成就是撒谎。
- 文件：frontend/src/utils/stages.js、frontend/src/composables/useDefect.js、frontend/tests/pure.test.mjs
- 影响：刷新页面即生效（需先 `npm run build`）。回归：`pure.test.mjs` 新增 6 组断言（快照推导、取证环节归位、没映射的名字不瞎猜、试运行后三步恒 off、空闲返回 null）共 29 项全过；`check_vue_defect_ui` / `check_vue_shell_ui` / `check_panel_fold` / `check_layout_ui` 全过。真实验证：脚本起一条 `/api/probe/stream`，浏览器什么都不点，步骤条 2 秒时 `['active','idle','off','off','off']`、16 秒时推进为 `['done','active','off','off','off']`，运行结束退回全 idle。

## 2026-10-09 14:18 · 修复 · 自检用例不再污染真实数据：check_live_stream 改走临时实例，并收掉历史残留与六个死壳

- 内容：`tests/check_live_stream.py` 原先硬编码打开发者正在用的 `127.0.0.1:8765`，两个后果都很疼——注入的合成工单（`STREAM-TEST-1` / `COMPAT-TEST-1`）会往真实 `proposals/` 与 `knowledge_base/` 落提案和卡片，跑一次积一份，混在待审批列表里分不清真假；而 SSE 客户端一旦被杀或脚本超时退出，服务端工作线程仍会把整条流水线跑完，期间所有端点 409，把别人正在用的槽位占死（本轮改造过程中它就真的占了一次）。现在 B/C/D 三段全部打在临时实例上，历史残留也清了：9 份合成提案 + 2 张合成知识卡收进回收站，知识库派生视图（`INDEX.md` / `_stats.json` / `_patterns/`）重建后从 18 张卡回到 16 张真卡，两条只由合成单撑起来的"模式"自动消失。另收掉 6 个 Vue 收口遗留的死壳用例。
- 做法：① 临时实例**透传真实 AI 配置与真实仓库路径**，否则 `ai_delta` 这条主线就测不到东西（mock 模式没有流式增量，空仓库没有可 grep 的关键词）；同时关掉 agentic 修复循环、清空闸门——本用例断言的是事件序列与契约字段，不是补丁对错，没必要为它花 8 分钟跑沙箱。② 补三条新断言：跑完占用标记自动释放、`/api/run` 返回 `stopped`、以及一条**反向对照**——真实 `proposals/` 里不许出现 `*TEST-*`（临时实例配置没生效就会红）。③ 那 6 个死壳（`check_pager` / `check_expand_modal` / `check_live_ui` / `check_kb_patterns_ui` / `check_changelog_ui` / `check_chat_ui`）直调 `proposalsCache` / `liveSetup` / `renderRunResults` 这类已不存在的 window 全局，一跑就 `ReferenceError`，属于"名字看着还像在守什么、实际没人跑"的那类文件；承接关系早就写在 `check_vue_all.py` 顶部的退役清单里，这次把 `check_pager` 也补进清单并在 `check_vue_defect_ui` 头部注明来源文件已收掉，免得后人按图找不到。
- 文件：tests/check_live_stream.py、tests/check_vue_all.py、tests/check_vue_defect_ui.py；收进回收站 `proposals/` 9 份 + `knowledge_base/` 2 张卡 + `tests/` 6 个死壳用例
- 影响：跑自检不会再需要"先等上一次测试跑完"，也不会再往真实数据里灌假工单。回归：`check_live_stream` 26 项全过（含真实网关流式冒烟 + 三条新断言）；`check_vue_all` 合计 15 个用例非零退出 0；`check_kb_patterns` 18/18、`check_run_lease`、`check_apicontract`、`check_fix_agent`、`check_repro`、`check_team` 48/0、`check_chat` 122/0、`check_origin_guard`、`check_render_escape`、`check_pure_js` 全过。
