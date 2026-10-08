<script setup>
// 扩展能力页签（#pane-extensions）：技能 + MCP 两个 panel。
// 旧版 index.html:674-797 + app.js:2323-2587 的等价迁移。
//
// 结构契约：#skill-form / #skill-list / #skill-count / #mcp-form / #mcp-list /
// #mcp-count / #mcp-test-result / #ext-mcp-stdio-fields / #ext-mcp-http-fields /
// 每张 MCP 卡的 #mcp-out-<id>，全部沿用旧 id。
// 空态文案与旧版 innerHTML 逐字一致（界面用例会断言）。
//
// 迁移说明：文本/数字 input 与 textarea 换成 el-input，按钮换成 el-button，
// #ext-mcp-transport 换成 el-select（id 落在根 .el-select 上，pick_select 已
// 配套升级为可驱动 el-select）。作用范围 / 启用的勾选保留原生 checkbox：
// 界面用例用 page.check('#ext-skill-scope-reqdev') 直接勾选，el-switch 改 DOM
// 结构会让它失效，而原 .switch 已是自绘开关，外壳不动。
import { onMounted, nextTick, ref, watch } from 'vue'
import { useExtensions, EXT_SCOPE_TEXT, EXT_SCOPE_KEYS } from '../composables/useExtensions.js'
import { useFold } from '../composables/useFold.js'
import { useModuleParams } from '../composables/useModuleParams.js'
import ParamDrawer from '../components/ParamDrawer.vue'

const {
  skills, mcp, extError, loadExtensions,
  skillFormVisible, editingSkillId, skillName, skillDesc, skillContent,
  skillScope, skillEnabled, showSkillForm, hideSkillForm, saveSkill,
  toggleSkill, deleteSkill,
  mcpFormVisible, editingMcpId, mcpName, mcpTransport, mcpTimeout, mcpEnabled,
  mcpCommand, mcpArgs, mcpEnv, mcpCwd, mcpUrl, mcpHeaders,
  formTest, mcpOut, showMcpForm, hideMcpForm, testMcpForm, testMcp,
  saveMcp, toggleMcp, deleteMcp,
} = useExtensions()

const { isCollapsed, toggleFold } = useFold()

// ── 扩展能力的两个表单进右侧抽屉 ─────────────────────────────────────
// 与另外五个模块不同，这里**默认关着**：技能 / MCP 列表才是这个模块的主体，
// 表单只在「新增 / 编辑」那一下出现（旧版就是靠 #skill-form / #mcp-form 的
// hidden class 内联展开）。所以沿用 useExtensions 里已有的 skillFormVisible /
// mcpFormVisible 作为唯一事实来源，抽屉只是它的另一种呈现：
//   · 表单可见性变了 → 同步开合抽屉（新增、编辑、保存后关闭都走这条）；
//   · 用户直接关抽屉（收起按钮 / 遮罩 / Esc）→ 反过来把表单标记为不可见，
//     否则「表单 visible 但抽屉关着」会卡住：下次点新增时值没变化，
//     第一个 watcher 不触发，抽屉就再也打不开。
// 两个 key 用 `extensions:skill` / `extensions:mcp`，同页签内互斥（见 useModuleParams）。
const SKILL_KEY = 'extensions:skill'
const MCP_KEY = 'extensions:mcp'
const { isOpen: isDrawerOpen, show: showDrawer, close: closeDrawer } = useModuleParams()

watch(skillFormVisible, (v) => { v ? showDrawer(SKILL_KEY) : closeDrawer(SKILL_KEY) })
watch(mcpFormVisible, (v) => { v ? showDrawer(MCP_KEY) : closeDrawer(MCP_KEY) })
watch(() => isDrawerOpen(SKILL_KEY), (v) => { if (!v) hideSkillForm() })
watch(() => isDrawerOpen(MCP_KEY), (v) => { if (!v) hideMcpForm() })

const skillNameInput = ref(null)
const mcpNameInput = ref(null)

/** 角标：N 启用 / M 条。旧版 renderExtSkills 的 badge 文案 */
function badge(list) {
  return `${list.filter((s) => s.enabled).length} 启用 / ${list.length}`
}

/** 卡片上的作用范围徽标。旧版 scope.map(...).join(' ') */
function scopeTexts(s) {
  return (s.scope || ['all']).map((x) => EXT_SCOPE_TEXT[x] || x)
}

/** 技能内容预览：截 300 字 + 超长补换行省略号（与旧版 innerHTML 一致） */
function skillPreview(s) {
  const c = s.content || ''
  return c.slice(0, 300) + (c.length > 300 ? '\n…' : '')
}

/** MCP 卡副标题：HTTP 用 url，stdio 用 command + args */
function mcpWhere(s) {
  return s.transport === 'http' ? (s.url || '')
    : [s.command, s.args].filter(Boolean).join(' ')
}

function onAddSkill() {
  showSkillForm(null)
  nextTick(() => skillNameInput.value && skillNameInput.value.focus())
}
function onEditSkill(id) {
  showSkillForm(id)
  nextTick(() => skillNameInput.value && skillNameInput.value.focus())
}
function onAddMcp() {
  showMcpForm(null)
  nextTick(() => mcpNameInput.value && mcpNameInput.value.focus())
}
function onEditMcp(id) {
  showMcpForm(id)
  nextTick(() => mcpNameInput.value && mcpNameInput.value.focus())
}

function onDeleteSkill(id) { deleteSkill(id) }
function onDeleteMcp(id) { deleteMcp(id) }

// 页签切进来时组件才挂载（App.vue 的 v-else-if），挂载即拉列表 ——
// 等价于旧版 switchTab 里每次进 extensions 都 loadExtensions()。
// 注意不能靠父组件持有模板 ref 来调：ref 挂在 v-for 内会收集成数组。
onMounted(loadExtensions)
</script>

<template>
  <section class="panel" id="ext-skill-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></svg>
        技能（团队规范 / 提示词包，自动注入 AI 任务）
        <span class="count-badge" id="skill-count">{{ badge(skills) }}</span>
      </h2>
      <el-button class="btn-ghost" id="btn-skill-add" @click="onAddSkill">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
        新增技能
      </el-button>
    </div>
    <!-- 表单不再内联展开在列表上方（2026-09-30 改版）：搬进右侧参数抽屉，
         见文件末尾的 ParamDrawer。主区 = 计数 + 列表，一眼看完。 -->
    <div id="skill-list" class="ext-list">
      <div v-if="!skills.length" class="empty">还没有技能。新增后，启用的技能会自动注入对应 AI 任务的 system prompt。</div>
      <div v-for="s in skills" :key="s.id" class="ext-card" :class="{ 'ext-off': !s.enabled }">
        <div class="ext-head">
          <span class="st" :class="s.enabled ? 'st-applied' : 'st-rejected'">{{ s.enabled ? '启用' : '停用' }}</span>
          <b>{{ s.name || '' }}</b>
          <span class="muted">{{ s.description || '' }}</span>
          <span class="ext-actions">
            <el-button size="small" class="btn-mini" @click="toggleSkill(s.id)">{{ s.enabled ? '停用' : '启用' }}</el-button>
            <el-button size="small" class="btn-mini" @click="onEditSkill(s.id)">编辑</el-button>
            <el-button size="small" class="btn-mini" @click="onDeleteSkill(s.id)">删除</el-button>
          </span>
        </div>
        <div class="ext-scopes"><template v-for="(t, i) in scopeTexts(s)" :key="i"><span class="probe-kw">{{ t }}</span>{{ ' ' }}</template></div>
        <pre class="ext-content">{{ skillPreview(s) }}</pre>
      </div>
    </div>
  </section>

  <section class="panel" id="ext-mcp-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" /><rect x="2" y="14" width="20" height="8" rx="2" /><path d="M6 6h.01M6 18h.01" /></svg>
        MCP 服务（AI 任务中可调用的外部工具）
        <span class="count-badge" id="mcp-count">{{ badge(mcp) }}</span>
      </h2>
      <el-button class="btn-ghost" id="btn-mcp-add" @click="onAddMcp">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
        新增 MCP 服务
      </el-button>
    </div>
    <!-- 表单同样搬进右侧参数抽屉（#mcp-form 的 id 与全部字段 id 原样保留在抽屉里） -->
    <div id="mcp-list" class="ext-list">
      <div v-if="!mcp.length" class="empty">还没有 MCP 服务。添加并启用后，它的工具会出现在「需求开发 / 接口联调 / 代码测试」的 AI 可调用工具列表里。</div>
      <div v-for="s in mcp" :key="s.id" class="ext-card" :class="{ 'ext-off': !s.enabled }">
        <div class="ext-head">
          <span class="st" :class="s.enabled ? 'st-applied' : 'st-rejected'">{{ s.enabled ? '启用' : '停用' }}</span>
          <span class="probe-kw">{{ s.transport === 'http' ? 'HTTP' : 'stdio' }}</span>
          <b>{{ s.name || '' }}</b>
          <span class="muted ext-where">{{ mcpWhere(s) }}</span>
          <span class="ext-actions">
            <el-button size="small" class="btn-mini" @click="testMcp(s.id)">测试</el-button>
            <el-button size="small" class="btn-mini" @click="toggleMcp(s.id)">{{ s.enabled ? '停用' : '启用' }}</el-button>
            <el-button size="small" class="btn-mini" @click="onEditMcp(s.id)">编辑</el-button>
            <el-button size="small" class="btn-mini" @click="onDeleteMcp(s.id)">删除</el-button>
          </span>
        </div>
        <div class="mcp-test-out" :id="`mcp-out-${s.id}`" v-html="mcpOut[s.id] || ''" />
      </div>
    </div>
    <p class="panel-note">安全说明：MCP 工具只在 AI 生成产出物的过程中被调用，结果仅作为模型参考；本应用不执行工具返回的任何指令。<br>stdio 服务以你配置的命令在本机启动。</p>
  </section>

  <!-- ── 右侧参数抽屉：技能表单 ── -->
  <ParamDrawer name="extensions:skill" title="技能" :default-open="false"
               hint="技能内容会原样注入 AI 任务的 system prompt，只影响启用的作用范围。">
    <div id="skill-form" class="ext-form" :class="{ hidden: !skillFormVisible }">
      <div class="run-form">
        <label class="field-inline grow">
          <span>技能名称</span>
          <el-input id="ext-skill-name" ref="skillNameInput" v-model="skillName" placeholder="例如：团队前端代码规范" />
        </label>
        <label class="field-inline grow">
          <span>一句话描述</span>
          <el-input id="ext-skill-desc" v-model="skillDesc" placeholder="这段规范管什么" />
        </label>
      </div>
      <label class="field">
        <span>技能内容（会原样注入 AI 的 system prompt）</span>
        <el-input id="ext-skill-content" type="textarea" :rows="8" v-model="skillContent" placeholder="例如：&#10;1. 组件一律函数式 + hooks；&#10;2. 请求必须走 src/utils/request 封装；&#10;3. 样式用 less module，禁止内联颜色。" />
      </label>
      <div class="field">
        <span class="scope-label">作用范围</span>
        <div class="run-form">
          <!-- 保留原生 checkbox：page.check('#ext-skill-scope-*') 直接勾选 -->
          <label v-for="k in EXT_SCOPE_KEYS" :key="k" class="switch">
            <input type="checkbox" :id="`ext-skill-scope-${k}`" v-model="skillScope[k]"><span class="track" /><span class="switch-label">{{ EXT_SCOPE_TEXT[k] }}</span>
          </label>
          <label class="switch"><input type="checkbox" id="ext-skill-enabled" v-model="skillEnabled"><span class="track" /><span class="switch-label">启用</span></label>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button type="primary" class="btn-primary" id="btn-skill-save" @click="saveSkill()">保存技能</el-button>
      <el-button class="btn-ghost" id="btn-skill-cancel" @click="hideSkillForm()">取消</el-button>
    </template>
  </ParamDrawer>

  <!-- ── 右侧参数抽屉：MCP 服务表单 ── -->
  <ParamDrawer name="extensions:mcp" title="MCP 服务" :default-open="false"
               hint="stdio 用本机命令拉起子进程，HTTP 连远程端点；两种传输的字段不同，切换时只留相关项。">
    <div id="mcp-form" class="ext-form" :class="{ hidden: !mcpFormVisible }">
      <div class="run-form">
        <label class="field-inline grow">
          <span>服务名称</span>
          <el-input id="ext-mcp-name" ref="mcpNameInput" v-model="mcpName" placeholder="例如：figma-mcp / database-tools" />
        </label>
        <label class="field-inline">
          <span>传输方式</span>
          <!-- 迁移重点：原生 select → el-select，id 落在根 .el-select 上；
               data-value 供 pick_select 按值定位选项（见 tests/ui_select.py） -->
          <el-select id="ext-mcp-transport" v-model="mcpTransport">
            <el-option value="stdio" data-value="stdio" label="stdio（本地子进程）" />
            <el-option value="http" data-value="http" label="HTTP（远程服务）" />
          </el-select>
        </label>
        <label class="field-inline">
          <span>超时(秒)</span>
          <el-input id="ext-mcp-timeout" type="number" v-model="mcpTimeout" />
        </label>
        <label class="switch"><input type="checkbox" id="ext-mcp-enabled" v-model="mcpEnabled"><span class="track" /><span class="switch-label">启用</span></label>
      </div>
      <div id="ext-mcp-stdio-fields" :class="{ hidden: mcpTransport === 'http' }">
        <label class="field">
          <span>启动命令</span>
          <el-input id="ext-mcp-command" v-model="mcpCommand" placeholder="npx / node / python（建议用绝对路径）" />
        </label>
        <label class="field">
          <span>命令参数（空格分隔，支持引号）</span>
          <el-input id="ext-mcp-args" v-model="mcpArgs" placeholder="-y @modelcontextprotocol/server-figma" />
        </label>
        <label class="field">
          <span>环境变量（每行 KEY: VALUE，可选）</span>
          <el-input id="ext-mcp-env" v-model="mcpEnv" placeholder="FIGMA_TOKEN: figd_xxx" />
        </label>
        <label class="field">
          <span>工作目录（可选）</span>
          <el-input id="ext-mcp-cwd" v-model="mcpCwd" placeholder="D:/tools/mcp-servers" />
        </label>
      </div>
      <div id="ext-mcp-http-fields" :class="{ hidden: mcpTransport !== 'http' }">
        <label class="field">
          <span>服务 URL（MCP Streamable HTTP 端点）</span>
          <el-input id="ext-mcp-url" v-model="mcpUrl" placeholder="https://host/mcp" />
        </label>
        <label class="field">
          <span>请求头（每行 KEY: VALUE，可选）</span>
          <el-input id="ext-mcp-headers" v-model="mcpHeaders" placeholder="Authorization: Bearer xxx" />
        </label>
      </div>
      <!-- 空的时候不带内容节点；hidden 由 formTest 是否有值驱动（与旧版 hidden class 一致） -->
      <pre id="mcp-test-result" class="log" :class="{ hidden: !formTest }"
           :data-placeholder="'测试结果会显示在这里'"
           v-html="formTest ? (formTest === '测试中…' ? '测试中…' : formTest) : ''" />
    </div>

    <template #footer>
      <el-button class="btn-ghost" id="btn-mcp-test" @click="testMcpForm()">测试连接</el-button>
      <el-button type="primary" class="btn-primary" id="btn-mcp-save" @click="saveMcp()">保存服务</el-button>
      <el-button class="btn-ghost" id="btn-mcp-cancel" @click="hideMcpForm()">取消</el-button>
    </template>
  </ParamDrawer>
</template>
