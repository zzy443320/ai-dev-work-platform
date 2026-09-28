// 配置（侧栏 #settings-panel + 大模型弹窗 #aimodal）的全部状态与副作用。
// 旧版 app.js:946-1363（renderAiSummary / loadSettings / fillAiForm / collectAi /
// applyAiPreset / clearAiKey / renderAiTestResult / testAi / fetchAiModels /
// saveSettings / renderGatePreview / updateRepoHint / loadAiPresets）的等价迁移。
//
// 模块级单例：SettingsPanel（侧栏）与 AiModal（弹窗）共享同一份表单状态，
// 两边的「保存配置」按钮提交的是同一份数据（旧版就是一组全局 id 的两个入口）。
import { computed, reactive, ref } from 'vue'
import { api } from '../api/client.js'
import { useToast } from './useToast.js'
import { useHealth } from './useHealth.js'
import { escapeHtml, shortPath } from '../utils/format.js'

const { toast } = useToast()
const { health, loadHealth } = useHealth()

// ---- 原始 settings 缓存（闸门探测命令等从这里取） ----
const settings = ref(null)

// ---- 预设与鉴权方式（/api/ai/providers） ----
const presets = ref([])
const authStyles = ref(null)      // null = 还没拉到，用内置 5 项
let presetsLoaded = false         // 旧版只填一次（options.length <= 1 的人才填）

// ---- 表单：非 AI 部分（字段名与旧版 id 对应） ----
const form = reactive({
  repo: { path: '', branch: '' },
  ones: { base_url: '', project_uuid: '', team_uuid: '', email: '', password: '', token: '' },
  figma: { token: '' },
  pagelogin: { enabled: false, email: '', password: '' },
  gate: { commands_text: '', degraded: true },
  playwright: { base_url: '', headless: true },
})

// ---- 表单：AI 部分（#aimodal 里那堆 cfg-ai-*） ----
const ai = reactive({
  mock: true, provider: 'openai', auth_style: 'bearer',
  base_url: '', endpoint: '', models_path: '', auth_header: '',
  model: '', api_key: '',
  temperature: '', max_tokens: '', top_p: '', timeout: '',
  content_path: '', max_tokens_field: '', system_role: '', proxy: '',
  extra_headers: '', extra_body: '', json_mode: true, verify_ssl: true,
})

// ---- 敏感字段的占位文案（旧版在 loadSettings / clearAiKey 里逐个 set） ----
const ph = reactive({
  aiKey: '填写后才会发起真实调用（留空=Mock）',
  onesPassword: 'ONES 登录密码',
  onesToken: '可粘贴浏览器里的 token',
  figma: '没有 Token 时可在需求开发页用「浏览器打开」兜底',
  pagePassword: '被测应用的登录密码',
})
const aiKeyCleared = ref(false)
const modelHint = ref('可自由填写；点「拉取列表」后可直接点选')

// ---- 模型名建议列表（旧版 modelSuggestList） ----
const modelSuggestList = ref([])

// ---- 测试连接结果（#ai-test-result） ----
const aiTest = reactive({ visible: false, cls: 'bad', html: '' })

function prettyJson(obj) {
  if (!obj || !Object.keys(obj).length) return ''
  try { return JSON.stringify(obj, null, 2) } catch (e) { return '' }
}

function fillAiForm(a) {
  ai.mock = !!a.mock
  ai.provider = a.provider || 'openai'
  ai.auth_style = a.auth_style || 'bearer'
  ai.base_url = a.base_url || ''
  ai.endpoint = a.endpoint || ''
  ai.models_path = a.models_path || ''
  ai.auth_header = a.auth_header || ''
  ai.model = a.model || ''
  ai.temperature = a.temperature ?? ''
  ai.max_tokens = a.max_tokens ?? ''
  ai.top_p = a.top_p ?? ''
  ai.timeout = a.timeout ?? ''
  ai.content_path = a.content_path || ''
  ai.max_tokens_field = a.max_tokens_field || ''
  ai.system_role = a.system_role || ''
  ai.proxy = a.proxy || ''
  ai.extra_headers = a.extra_headers_text || prettyJson(a.extra_headers) || ''
  ai.extra_body = a.extra_body_text || prettyJson(a.extra_body) || ''
  ai.json_mode = a.json_mode !== false
  ai.verify_ssl = a.verify_ssl !== false
  ai.api_key = ''
}

async function loadSettings() {
  try {
    const s = await api.getSettings()
    settings.value = s
    form.repo.path = (s.repo || {}).path || ''
    form.repo.branch = (s.repo || {}).branch || ''
    fillAiForm(s.ai || {})
    ai.api_key = ''
    ph.aiKey = s.has_api_key
      ? `已保存 ${(s.ai || {}).api_key_masked}，留空保持不变`
      : '填写后才会发起真实调用（留空=Mock）'
    aiKeyCleared.value = false
    const ones = s.ones || {}
    form.ones.base_url = ones.base_url || ''
    form.ones.project_uuid = ones.project_uuid || ''
    form.ones.team_uuid = ones.team_uuid || ''
    form.ones.email = ones.email || ''
    form.ones.password = ''
    ph.onesPassword = s.has_password ? '已保存，留空保持不变' : 'ONES 登录密码'
    form.ones.token = ''
    ph.onesToken = s.has_token ? `已保存 ${ones.token_masked}，留空保持不变` : '可粘贴浏览器里的 token'
    const fig = s.figma || {}
    form.figma.token = ''
    ph.figma = s.has_figma_token
      ? `已保存 ${fig.token_masked || ''}，留空保持不变`
      : '没有 Token 时可在需求开发页用「浏览器打开」兜底'
    const plg = s.pagelogin || {}
    form.pagelogin.enabled = plg.enabled === true
    form.pagelogin.email = plg.email || ''
    form.pagelogin.password = ''
    ph.pagePassword = s.has_page_password ? '已保存，留空保持不变' : '被测应用的登录密码'
    const gate = s.gate || {}
    form.gate.commands_text = gate.commands_text || ''
    form.gate.degraded = gate.degraded !== false
    const pw = s.playwright || {}
    form.playwright.base_url = pw.base_url || ''
    form.playwright.headless = pw.headless !== false
  } catch (e) {
    toast(`配置加载失败: ${e}`, 'err')
  }
}

async function loadAiPresets() {
  try {
    const data = await api.aiProviders()
    if (!presetsLoaded) {
      presets.value = data.presets || []
      if (data.auth_styles) authStyles.value = data.auth_styles
      presetsLoaded = true
    }
  } catch (e) { /* 模板拉不到不影响手工填写 */ }
}

// ---- provider 相关派生（旧版 onProviderChange 的提示与占位） ----
const PROVIDER_HINTS = {
  anthropic: '留空 = https://api.anthropic.com；中转站填自己的域名（不带 /v1/messages）',
  openai: '填到版本层为止，例如 https://你的中转站.com/v1',
  custom: '自建/异构网关必填，Base URL + Chat 路径拼成完整地址',
}
const providerHint = computed(() => PROVIDER_HINTS[ai.provider] || '')
function currentPreset() {
  return presets.value.find((x) => x.provider === ai.provider && x.id === ai.provider) || null
}
const endpointPh = computed(() => {
  const p = currentPreset()
  return (p && !ai.endpoint.trim()) ? (p.endpoint || '') : '/chat/completions'
})
const modelsPathPh = computed(() => {
  const p = currentPreset()
  return (p && !ai.models_path.trim()) ? (p.models_path || '') : '/models'
})
const contentPathPh = computed(() => {
  const p = currentPreset()
  return (p && !ai.content_path.trim()) ? (p.content_path || '') : 'choices.0.message.content'
})

/** 鉴权字段名只在「自定义请求头 / URL query」时需要（旧版 onAuthChange） */
const authHeaderNeeded = computed(() => ai.auth_style === 'header' || ai.auth_style === 'query')

/** Mock 开关联动 Key 占位（旧版 cfg-ai-mock change 监听） */
const keyPlaceholder = computed(() => {
  if (aiKeyCleared.value) return '将清除已保存的 Key（保存后生效）'
  if (ai.mock) return 'Mock 模式下不会发起真实调用'
  return ph.aiKey
})

function applyAiPreset(id) {
  const p = presets.value.find((x) => x.id === id)
  if (!p) return
  ai.provider = p.provider
  ai.base_url = p.base_url || ''
  ai.endpoint = p.endpoint || ''
  ai.models_path = p.models_path || ''
  ai.content_path = p.content_path || ''
  ai.auth_style = p.auth_style || 'bearer'
  ai.json_mode = !!p.json_mode
  modelSuggestList.value = (p.models || []).slice()
  if ((p.models || []).length && !ai.model.trim()) {
    ai.model = p.models[0]
  }
  toast(`已套用「${p.label}」，下方字段仍可逐项修改`, 'ok')
}

function clearAiKey() {
  ai.api_key = ''
  aiKeyCleared.value = true
  toast('保存后将清除已存的 API Key', 'err')
}

function collectAi() {
  const t = (v) => String(v ?? '').trim()
  const out = {
    provider: ai.provider,
    auth_style: ai.auth_style,
    base_url: t(ai.base_url),
    endpoint: t(ai.endpoint),
    models_path: t(ai.models_path),
    auth_header: t(ai.auth_header),
    model: t(ai.model),
    temperature: t(ai.temperature),
    max_tokens: t(ai.max_tokens),
    top_p: t(ai.top_p),
    timeout: t(ai.timeout),
    content_path: t(ai.content_path),
    max_tokens_field: t(ai.max_tokens_field),
    system_role: t(ai.system_role),
    proxy: t(ai.proxy),
    extra_headers: ai.extra_headers,
    extra_body: ai.extra_body,
    json_mode: ai.json_mode,
    verify_ssl: ai.verify_ssl,
    mock: ai.mock,
  }
  const key = ai.api_key.trim()
  if (key) out.api_key = key
  if (aiKeyCleared.value) out.clear_api_key = true
  return out
}

// ---- 测试连接（旧版 testAi + renderAiTestResult） ----
const testing = ref('')   // '' | 'test' | 'dry'

function renderAiTestResult(data, dry) {
  aiTest.visible = true
  const mockSkipped = !!data.mock && !dry
  const ok = !!data.ok && !mockSkipped
  const req = data.request || {}
  const rows = []
  if (req.url) rows.push(['请求地址', req.url])
  if (req.headers) rows.push(['请求头', Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join('\n')])
  if (req.body_keys && req.body_keys.length) rows.push(['请求体字段', req.body_keys.join(', ')])
  if (data.reply) rows.push(['模型回显', data.reply])
  if (data.usage && (data.usage.input || data.usage.output)) {
    rows.push(['token', `输入 ${data.usage.input ?? '?'} / 输出 ${data.usage.output ?? '?'}`])
  }
  if (data.seconds) rows.push(['耗时', `${data.seconds}s`])
  const errs = data.errors || (data.error ? [data.error] : [])
  const head = mockSkipped ? '未发起请求'
    : ok ? (dry ? '配置校验通过' : '连接成功') : '不通'
  aiTest.cls = `${mockSkipped ? 'warn' : (ok ? 'good' : 'bad')}`
  aiTest.html = `
    <div class="atr-head">
      <b>${escapeHtml(head)}</b>
      <span class="atr-mode">${escapeHtml(data.mode || '')}</span>
      <button type="button" class="atr-close" title="关闭" data-atr-close>×</button>
    </div>
    ${errs.length ? `<ul class="atr-errs">${errs.map((e) => `<li>${escapeHtml(String(e))}</li>`).join('')}</ul>` : ''}
    ${data.body ? `<pre class="atr-body">${escapeHtml(String(data.body).slice(0, 600))}</pre>` : ''}
    ${rows.map(([k, v]) => `<div class="atr-row"><b>${escapeHtml(k)}</b><span>${escapeHtml(String(v))}</span></div>`).join('')}
    ${data.note ? `<p class="atr-note">${escapeHtml(data.note)}</p>` : ''}
    ${ok && !dry ? '<p class="atr-note">已确认可用。保存后流水线会走这个地址。</p>' : ''}
  `
}

function closeAiTestResult() {
  aiTest.visible = false
}

async function testAi(dry) {
  if (testing.value) return
  testing.value = dry ? 'dry' : 'test'
  try {
    let data, ok
    try {
      data = await api.aiTest({ ai: collectAi(), dry: !!dry })
      ok = true
    } catch (e) {
      // 非 2xx 也要把后端给的错误详情渲染出来（旧版不 throw，直接画「不通」）
      data = (e && e.data) || { error: String((e && e.message) || e) }
      ok = false
    }
    renderAiTestResult(data, dry)
    if (data.mock && !dry) toast('Mock 模式，没有发起真实请求', 'err')
    else if (!ok && !data.ok) toast(data.error || (data.errors || [])[0] || '不通', 'err')
    else toast(dry ? '配置校验通过' : '连接成功', 'ok')
  } finally {
    testing.value = ''
  }
}

async function fetchAiModels() {
  if (testing.value === 'models') return
  testing.value = 'models'
  try {
    const data = await api.aiModels({ ai: collectAi() })
    const names = data.models || []
    modelSuggestList.value = names.slice()
    modelHint.value = `该端点返回 ${names.length} 个模型，点击下方即可填入`
    toast(`拉到 ${names.length} 个模型`, 'ok')
  } catch (e) {
    modelHint.value = `拉取失败：${e}`
    toast(`拉取模型列表失败: ${e}`, 'err')
  } finally {
    testing.value = ''
  }
}

// ---- 保存（两个按钮共用：#btn-save 与 #btn-save-ai） ----
const saving = ref(false)

async function saveSettings() {
  if (saving.value) return
  saving.value = true
  const body = {
    repo: { path: form.repo.path.trim(), branch: form.repo.branch.trim() },
    ai: collectAi(),
    ones: {
      base_url: form.ones.base_url.trim(),
      project_uuid: form.ones.project_uuid.trim(),
      team_uuid: form.ones.team_uuid.trim(),
      email: form.ones.email.trim(),
    },
    figma: {},
    pagelogin: {
      enabled: form.pagelogin.enabled,
      email: form.pagelogin.email.trim(),
    },
    gate: {
      commands_text: form.gate.commands_text,
      degraded: form.gate.degraded,
    },
    playwright: {
      base_url: form.playwright.base_url.trim(),
      headless: form.playwright.headless,
    },
  }
  const token = form.ones.token.trim()
  if (token) body.ones.token = token
  const onesPassword = form.ones.password
  if (onesPassword) body.ones.password = onesPassword
  const figmaToken = form.figma.token.trim()
  if (figmaToken) body.figma.token = figmaToken
  const pagePassword = form.pagelogin.password
  if (pagePassword) body.pagelogin.password = pagePassword

  try {
    await api.saveSettings(body)
    toast('配置已保存', 'ok')
    aiKeyCleared.value = false
    ai.api_key = ''
    form.ones.token = ''
    form.ones.password = ''
    form.figma.token = ''
    form.pagelogin.password = ''
    await loadSettings()
    await useHealth().loadHealth()
  } catch (e) {
    toast(`保存失败: ${e}`, 'err')
  } finally {
    saving.value = false
  }
}

// ---- 验收闸门预览（旧版 renderGatePreview + cfg-gate-commands input 监听） ----
function parseGatePreviewLines(text) {
  return (text || '').split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((l) => ({ name: l.split(/\s+/)[0], cmd: l }))
}

/** 用户动过闸门文本框后，命令清单改由前端实时解析（与旧版 input 监听一致）；
    没动过时用服务端返回的 commands（程序化赋值不触发 input，故要区分）。 */
const gateDirty = ref(false)
function onGateInput() { gateDirty.value = true }

const GATE_TEXT_LOCAL = {
  explicit: '显式命令', package_json: 'package.json 探测',
  degraded: '降级语法检查', none: '未校验',
}

const gatePreviewHtml = computed(() => {
  const cmds = gateDirty.value
    ? parseGatePreviewLines(form.gate.commands_text)
    : ((settings.value && settings.value.gate && settings.value.gate.commands) || [])
  const h = useHealth().health.value || {}
  const badge = (lv) => `<span class="gate-badge gate-${escapeHtml(lv)}">${escapeHtml(GATE_TEXT_LOCAL[lv] || lv)}</span>`
  let html
  if (cmds.length) {
    html = `<div class="gp-title">${badge('explicit')} 逐条真实执行以下命令：</div>`
      + `<ul class="gp-list">${cmds.map((c) => `<li><span class="gp-name">${escapeHtml(c.name || '')}</span><code>${escapeHtml(c.cmd || '')}</code></li>`).join('')}</ul>`
  } else if ((h.gate_commands || []).length) {
    html = `<div class="gp-title">${badge('package_json')} 已从 package.json 探测到：</div>`
      + `<ul class="gp-list">${h.gate_commands.map((c) => `<li><code>${escapeHtml(c)}</code></li>`).join('')}</ul>`
  } else {
    html = `<div class="gp-title">${badge('degraded')} 无可用命令，闸门降级：</div>`
      + '<p>仓库里没有可识别的 type-check / lint / test / build 脚本，闸门只做括号与引号闭合、node --check、py ast.parse。<b>它不代表代码正确</b>。</p>'
  }
  html += '<p class="hint">留空则自动从 package.json 探测 type-check/lint/test/build；把团队真正用来验收的命令写进来（explicit）可信度最高。</p>'
  return html
})

// ---- 仓库路径提示（旧版 updateRepoHint + cfg-repo-path change 监听） ----
const repoHint = ref('')
function onRepoPathChange() {
  repoHint.value = form.repo.path.trim() ? '保存时校验路径是否存在' : ''
}

// ---- 侧栏大模型摘要卡（旧版 renderAiSummary） ----
const AI_PROVIDER_TEXT = { anthropic: 'Anthropic', openai: 'OpenAI', custom: '自定义网关' }

const aiSummaryHtml = computed(() => {
  const s = settings.value || {}
  const a = s.ai || {}
  const h = health.value || {}
  const mock = !!(a.mock || h.ai_mode === 'mock')
  const provider = AI_PROVIDER_TEXT[a.provider] || a.provider || '—'
  const model = a.model || h.ai_model || '未设置'
  const base = a.base_url || '官方默认地址'
  const keyText = s.has_api_key
    ? `Key 已保存 ${(a.api_key_masked || '').trim()}`
    : '未配置 API Key'
  const attr = (v) => escapeHtml(v)
  return `
    <div class="ais-row">
      <span class="ais-badge ${mock ? 'mock' : 'live'}">${mock ? 'Mock 模式' : '真实调用'}</span>
      <span class="ais-provider">${escapeHtml(provider)}</span>
    </div>
    <div class="ais-line" title="${attr(model)}">模型：${escapeHtml(model)}</div>
    <div class="ais-line" title="${attr(base)}">地址：${escapeHtml(shortPath(base))}</div>
    <div class="ais-line">${mock ? '不调用真实 API，结果仅供演示' : escapeHtml(keyText)}</div>
  `
})

export function useSettings() {
  return {
    // 状态
    settings, form, ai, ph, aiKeyCleared, modelHint, modelSuggestList,
    presets, authStyles, aiTest, testing, saving, repoHint, gateDirty,
    // 派生
    providerHint, endpointPh, modelsPathPh, contentPathPh, authHeaderNeeded,
    keyPlaceholder, gatePreviewHtml, aiSummaryHtml,
    // 动作
    loadSettings, loadAiPresets, applyAiPreset, clearAiKey, collectAi,
    testAi, closeAiTestResult, fetchAiModels, saveSettings,
    onGateInput, onRepoPathChange,
  }
}
