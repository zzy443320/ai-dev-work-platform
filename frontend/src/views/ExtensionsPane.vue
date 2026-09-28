<script setup>
// 扩展能力页签（#pane-extensions）：技能 + MCP 两个 panel。
// 旧版 index.html:674-797 + app.js:2323-2587 的等价迁移。
//
// 结构契约：#skill-form / #skill-list / #skill-count / #mcp-form / #mcp-list /
// #mcp-count / #mcp-test-result / #ext-mcp-stdio-fields / #ext-mcp-http-fields /
// 每张 MCP 卡的 #mcp-out-<id>，全部沿用旧 id。
// 空态文案与旧版 innerHTML 逐字一致（界面用例会断言）。
import { onMounted, nextTick, ref } from 'vue'
import { useExtensions, EXT_SCOPE_TEXT, EXT_SCOPE_KEYS } from '../composables/useExtensions.js'
import { useFold } from '../composables/useFold.js'

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
      <button class="btn-ghost" id="btn-skill-add" @click="onAddSkill">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
        新增技能
      </button>
    </div>
    <div id="skill-form" class="ext-form" :class="{ hidden: !skillFormVisible }">
      <div class="run-form">
        <label class="field-inline grow">
          <span>技能名称</span>
          <input type="text" id="ext-skill-name" ref="skillNameInput" v-model="skillName" placeholder="例如：团队前端代码规范">
        </label>
        <label class="field-inline grow">
          <span>一句话描述</span>
          <input type="text" id="ext-skill-desc" v-model="skillDesc" placeholder="这段规范管什么">
        </label>
      </div>
      <label class="field">
        <span>技能内容（会原样注入 AI 的 system prompt）</span>
        <textarea id="ext-skill-content" rows="6" spellcheck="false" v-model="skillContent" placeholder="例如：&#10;1. 组件一律函数式 + hooks；&#10;2. 请求必须走 src/utils/request 封装；&#10;3. 样式用 less module，禁止内联颜色。" />
      </label>
      <div class="run-form">
        <span class="scope-label">作用范围：</span>
        <label v-for="k in EXT_SCOPE_KEYS" :key="k" class="switch">
          <input type="checkbox" :id="`ext-skill-scope-${k}`" v-model="skillScope[k]"><span class="track" /><span class="switch-label">{{ EXT_SCOPE_TEXT[k] }}</span>
        </label>
        <label class="switch"><input type="checkbox" id="ext-skill-enabled" v-model="skillEnabled"><span class="track" /><span class="switch-label">启用</span></label>
      </div>
      <div class="run-form">
        <button class="btn-primary" @click="saveSkill()">保存技能</button>
        <button class="btn-ghost" @click="hideSkillForm()">取消</button>
      </div>
    </div>
    <div id="skill-list" class="ext-list">
      <div v-if="!skills.length" class="empty">还没有技能。新增后，启用的技能会自动注入对应 AI 任务的 system prompt。</div>
      <div v-for="s in skills" :key="s.id" class="ext-card" :class="{ 'ext-off': !s.enabled }">
        <div class="ext-head">
          <span class="st" :class="s.enabled ? 'st-applied' : 'st-rejected'">{{ s.enabled ? '启用' : '停用' }}</span>
          <b>{{ s.name || '' }}</b>
          <span class="muted">{{ s.description || '' }}</span>
          <span class="ext-actions">
            <button class="btn-mini" @click="toggleSkill(s.id)">{{ s.enabled ? '停用' : '启用' }}</button>
            <button class="btn-mini" @click="onEditSkill(s.id)">编辑</button>
            <button class="btn-mini" @click="onDeleteSkill(s.id)">删除</button>
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
      <button class="btn-ghost" id="btn-mcp-add" @click="onAddMcp">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
        新增 MCP 服务
      </button>
    </div>
    <div id="mcp-form" class="ext-form" :class="{ hidden: !mcpFormVisible }">
      <div class="run-form">
        <label class="field-inline grow">
          <span>服务名称</span>
          <input type="text" id="ext-mcp-name" ref="mcpNameInput" v-model="mcpName" placeholder="例如：figma-mcp / database-tools">
        </label>
        <label class="field-inline">
          <span>传输方式</span>
          <select id="ext-mcp-transport" v-model="mcpTransport">
            <option value="stdio">stdio（本地子进程）</option>
            <option value="http">HTTP（远程服务）</option>
          </select>
        </label>
        <label class="field-inline">
          <span>超时(秒)</span>
          <input type="number" id="ext-mcp-timeout" v-model="mcpTimeout" min="3">
        </label>
        <label class="switch"><input type="checkbox" id="ext-mcp-enabled" v-model="mcpEnabled"><span class="track" /><span class="switch-label">启用</span></label>
      </div>
      <div id="ext-mcp-stdio-fields" :class="{ hidden: mcpTransport === 'http' }">
        <div class="run-form">
          <label class="field-inline grow">
            <span>启动命令</span>
            <input type="text" id="ext-mcp-command" v-model="mcpCommand" placeholder="npx / node / python（建议用绝对路径）">
          </label>
          <label class="field-inline grow">
            <span>命令参数（空格分隔，支持引号）</span>
            <input type="text" id="ext-mcp-args" v-model="mcpArgs" placeholder="-y @modelcontextprotocol/server-figma">
          </label>
        </div>
        <div class="run-form">
          <label class="field-inline grow">
            <span>环境变量（每行 KEY: VALUE，可选）</span>
            <input type="text" id="ext-mcp-env" v-model="mcpEnv" placeholder="FIGMA_TOKEN: figd_xxx">
          </label>
          <label class="field-inline grow">
            <span>工作目录（可选）</span>
            <input type="text" id="ext-mcp-cwd" v-model="mcpCwd" placeholder="D:/tools/mcp-servers">
          </label>
        </div>
      </div>
      <div id="ext-mcp-http-fields" :class="{ hidden: mcpTransport !== 'http' }">
        <div class="run-form">
          <label class="field-inline grow">
            <span>服务 URL（MCP Streamable HTTP 端点）</span>
            <input type="text" id="ext-mcp-url" v-model="mcpUrl" placeholder="https://host/mcp">
          </label>
          <label class="field-inline grow">
            <span>请求头（每行 KEY: VALUE，可选）</span>
            <input type="text" id="ext-mcp-headers" v-model="mcpHeaders" placeholder="Authorization: Bearer xxx">
          </label>
        </div>
      </div>
      <div class="run-form">
        <button class="btn-ghost" id="btn-mcp-test" @click="testMcpForm()">测试连接</button>
        <button class="btn-primary" @click="saveMcp()">保存服务</button>
        <button class="btn-ghost" @click="hideMcpForm()">取消</button>
      </div>
      <!-- 空的时候不带内容节点；hidden 由 formTest 是否有值驱动（与旧版 hidden class 一致） -->
      <pre id="mcp-test-result" class="log" :class="{ hidden: !formTest }"
           :data-placeholder="'测试结果会显示在这里'"
           v-html="formTest ? (formTest === '测试中…' ? '测试中…' : formTest) : ''" />
    </div>
    <div id="mcp-list" class="ext-list">
      <div v-if="!mcp.length" class="empty">还没有 MCP 服务。添加并启用后，它的工具会出现在「需求开发 / 接口联调 / 代码测试」的 AI 可调用工具列表里。</div>
      <div v-for="s in mcp" :key="s.id" class="ext-card" :class="{ 'ext-off': !s.enabled }">
        <div class="ext-head">
          <span class="st" :class="s.enabled ? 'st-applied' : 'st-rejected'">{{ s.enabled ? '启用' : '停用' }}</span>
          <span class="probe-kw">{{ s.transport === 'http' ? 'HTTP' : 'stdio' }}</span>
          <b>{{ s.name || '' }}</b>
          <span class="muted ext-where">{{ mcpWhere(s) }}</span>
          <span class="ext-actions">
            <button class="btn-mini" @click="testMcp(s.id)">测试</button>
            <button class="btn-mini" @click="toggleMcp(s.id)">{{ s.enabled ? '停用' : '启用' }}</button>
            <button class="btn-mini" @click="onEditMcp(s.id)">编辑</button>
            <button class="btn-mini" @click="onDeleteMcp(s.id)">删除</button>
          </span>
        </div>
        <div class="mcp-test-out" :id="`mcp-out-${s.id}`" v-html="mcpOut[s.id] || ''" />
      </div>
    </div>
    <p class="panel-note">安全说明：MCP 工具只在 AI 生成产出物的过程中被调用，结果仅作为模型参考；本应用不执行工具返回的任何指令。<br>stdio 服务以你配置的命令在本机启动。</p>
  </section>
</template>
