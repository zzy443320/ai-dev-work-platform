// 扩展能力（技能 / MCP）的全部状态与副作用。
// 旧版 app.js:2323-2587（loadExtensions / renderExtSkills / showSkillForm /
// saveSkill / toggleSkill / deleteSkill / renderExtMcp / showMcpForm / testMcp /
// saveMcp / toggleMcp / deleteMcp）的等价迁移。
//
// 只在 extensions 页签用，状态放工厂里即可（不跨组件共享）。
import { ref } from 'vue'
import { api } from '../api/client.js'
import { useToast } from './useToast.js'
import { escapeHtml } from '../utils/format.js'

/** 作用范围徽标文案。旧版 EXT_SCOPE_TEXT（app.js:2329） */
export const EXT_SCOPE_TEXT = { all: '全部任务', reqdev: '需求开发', apidebug: '接口联调', codetest: '代码测试' }
export const EXT_SCOPE_KEYS = ['all', 'reqdev', 'apidebug', 'codetest']

export function useExtensions() {
  const { toast } = useToast()

  const skills = ref([])
  const mcp = ref([])
  const extError = ref('')

  // ---- 技能表单 ----
  const skillFormVisible = ref(false)
  const editingSkillId = ref(null)
  const skillName = ref('')
  const skillDesc = ref('')
  const skillContent = ref('')
  const skillScope = ref({ all: true, reqdev: false, apidebug: false, codetest: false })
  const skillEnabled = ref(true)

  function showSkillForm(id) {
    editingSkillId.value = id || null
    const s = id ? skills.value.find((x) => x.id === id) : null
    skillName.value = s ? (s.name || '') : ''
    skillDesc.value = s ? (s.description || '') : ''
    skillContent.value = s ? (s.content || '') : ''
    const scope = (s ? s.scope : ['all']) || ['all']
    skillScope.value = { all: false, reqdev: false, apidebug: false, codetest: false }
    EXT_SCOPE_KEYS.forEach((k) => { skillScope.value[k] = scope.includes(k) })
    skillEnabled.value = s ? !!s.enabled : true
    skillFormVisible.value = true
  }

  function hideSkillForm() {
    skillFormVisible.value = false
    editingSkillId.value = null
  }

  function _skillScopeFromForm() {
    const out = EXT_SCOPE_KEYS.filter((k) => skillScope.value[k])
    return out.length ? out : ['all']
  }

  async function saveSkill() {
    const name = skillName.value.trim()
    const content = skillContent.value.trim()
    if (!name) { toast('请填技能名称', 'err'); return }
    if (!content) { toast('请填技能内容', 'err'); return }
    const obj = {
      id: editingSkillId.value || undefined,
      name, content,
      description: skillDesc.value.trim(),
      scope: _skillScopeFromForm(),
      enabled: skillEnabled.value,
    }
    const list = skills.value.slice()
    if (editingSkillId.value) {
      const i = list.findIndex((x) => x.id === editingSkillId.value)
      if (i >= 0) list[i] = { ...list[i], ...obj, id: editingSkillId.value }
    } else {
      list.push(obj)
    }
    await _saveSkills(list)
    hideSkillForm()
  }

  function toggleSkill(id) {
    return _saveSkills(skills.value.map((s) => s.id === id ? { ...s, enabled: !s.enabled } : s))
  }

  function deleteSkill(id) {
    if (!window.confirm('确认删除该技能？')) return
    return _saveSkills(skills.value.filter((s) => s.id !== id))
  }

  async function _saveSkills(list) {
    try {
      const data = await api.extensionSkills({ skills: list })
      skills.value = data.skills || []
      toast('技能已保存', 'ok')
    } catch (e) {
      toast(`技能保存失败: ${e}`, 'err')
    }
  }

  // ---- MCP 表单 ----
  const mcpFormVisible = ref(false)
  const editingMcpId = ref(null)
  const mcpName = ref('')
  const mcpTransport = ref('stdio')
  const mcpTimeout = ref(30)
  const mcpEnabled = ref(true)
  const mcpCommand = ref('')
  const mcpArgs = ref('')
  const mcpEnv = ref('')
  const mcpCwd = ref('')
  const mcpUrl = ref('')
  const mcpHeaders = ref('')
  /** 表单内测试结果（旧 #mcp-test-result）：'' = 隐藏，'…' = 测试中，HTML = 结果 */
  const formTest = ref('')
  /** 每张卡自己的测试输出（旧 #mcp-out-<id>）：id → HTML 串 */
  const mcpOut = ref({})

  function showMcpForm(id) {
    editingMcpId.value = id || null
    const s = id ? mcp.value.find((x) => x.id === id) : null
    mcpName.value = s ? (s.name || '') : ''
    mcpTransport.value = s ? (s.transport || 'stdio') : 'stdio'
    mcpTimeout.value = s ? (s.timeout || 30) : 30
    mcpEnabled.value = s ? !!s.enabled : true
    mcpCommand.value = s ? (s.command || '') : ''
    mcpArgs.value = s ? (s.args || '') : ''
    mcpEnv.value = s ? (s.env || '') : ''
    mcpCwd.value = s ? (s.cwd || '') : ''
    mcpUrl.value = s ? (s.url || '') : ''
    mcpHeaders.value = s ? (s.headers || '') : ''
    formTest.value = ''
    mcpFormVisible.value = true
  }

  function hideMcpForm() {
    mcpFormVisible.value = false
    editingMcpId.value = null
  }

  function _mcpObjFromForm() {
    return {
      id: editingMcpId.value || undefined,
      name: mcpName.value.trim(),
      transport: mcpTransport.value,
      timeout: mcpTimeout.value,
      enabled: mcpEnabled.value,
      command: mcpCommand.value.trim(),
      args: mcpArgs.value.trim(),
      env: mcpEnv.value.trim(),
      cwd: mcpCwd.value.trim(),
      url: mcpUrl.value.trim(),
      headers: mcpHeaders.value.trim(),
    }
  }

  /** 测试一个 server；into 为 null 时写表单内结果框，否则写对应卡片的输出框 */
  async function _testMcp(server, into) {
    const box = into === null ? formTest : mcpOut
    const key = into === null ? '' : into
    if (into === null) formTest.value = '测试中…'
    else mcpOut.value = { ...mcpOut.value, [key]: '<div class="log-title">测试中…</div>' }
    try {
      const data = await api.mcpTest({ server })
      if (!data.ok) throw new Error(data.error || '连接失败')
      const tools = (data.tools || []).map((t) => `· ${t.name}${t.description ? ' — ' + t.description : ''}`).join('\n')
      const html = `<div class="log-title">连接成功（${data.seconds}s，${data.tool_count} 个工具）</div>`
        + `<div class="log-html">${escapeHtml(tools || '（该服务没有暴露任何工具）')}</div>`
      if (into === null) {
        formTest.value = html
        toast(`连接成功，发现 ${data.tool_count} 个工具`, 'ok')
      } else {
        mcpOut.value = { ...mcpOut.value, [key]: html }
      }
    } catch (e) {
      const html = `<div class="log-title">连接失败</div><div class="log-html">${escapeHtml(String(e))}</div>`
      if (into === null) {
        formTest.value = html
        toast(`连接失败: ${e}`, 'err')
      } else {
        mcpOut.value = { ...mcpOut.value, [key]: html }
      }
    }
  }

  /** 表单里点「测试连接」 */
  function testMcpForm() {
    return _testMcp(_mcpObjFromForm(), null)
  }
  /** 卡片上点「测试」 */
  function testMcp(id) {
    const s = mcp.value.find((x) => x.id === id)
    if (!s) return
    return _testMcp(s, id)
  }

  async function saveMcp() {
    const obj = _mcpObjFromForm()
    if (!obj.name) { toast('请填服务名称', 'err'); return }
    const list = mcp.value.slice()
    if (editingMcpId.value) {
      const i = list.findIndex((x) => x.id === editingMcpId.value)
      if (i >= 0) list[i] = { ...list[i], ...obj, id: editingMcpId.value }
    } else {
      list.push(obj)
    }
    await _saveMcp(list)
    hideMcpForm()
  }

  function toggleMcp(id) {
    return _saveMcp(mcp.value.map((s) => s.id === id ? { ...s, enabled: !s.enabled } : s))
  }

  function deleteMcp(id) {
    if (!window.confirm('确认删除该 MCP 服务配置？')) return
    return _saveMcp(mcp.value.filter((s) => s.id !== id))
  }

  async function _saveMcp(list) {
    try {
      const data = await api.mcpSave({ servers: list })
      mcp.value = data.servers || []
      toast('MCP 配置已保存', 'ok')
    } catch (e) {
      toast(`MCP 保存失败: ${e}`, 'err')
    }
  }

  async function loadExtensions() {
    try {
      const data = await api.extensions()
      skills.value = data.skills || []
      mcp.value = data.mcp || []
      extError.value = ''
    } catch (e) {
      toast(`扩展能力加载失败: ${e}`, 'err')
      extError.value = String((e && e.message) || e)
    }
  }

  return {
    skills, mcp, extError, loadExtensions,
    // 技能
    skillFormVisible, editingSkillId, skillName, skillDesc, skillContent,
    skillScope, skillEnabled, showSkillForm, hideSkillForm, saveSkill,
    toggleSkill, deleteSkill,
    // MCP
    mcpFormVisible, editingMcpId, mcpName, mcpTransport, mcpTimeout, mcpEnabled,
    mcpCommand, mcpArgs, mcpEnv, mcpCwd, mcpUrl, mcpHeaders,
    formTest, mcpOut, showMcpForm, hideMcpForm, testMcpForm, testMcp,
    saveMcp, toggleMcp, deleteMcp,
  }
}
