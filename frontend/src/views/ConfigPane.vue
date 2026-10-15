<!--
  配置模块（#pane-config）。2026-09-30 从右栏侧栏（#settings-panel）独立成页签：
  侧栏只留「重要配置只读摘要 + 入口」，全部编辑控件搬到这里。

  字段 id 全部沿用 cfg-*（测试与自检脚本按 id 定位），读写逻辑一行没动 ——
  与侧栏摘要、大模型弹窗 #aimodal 共用 useSettings() 的模块级单例，提交同一份数据。

  布局（2026-10-14 第四次改版：就绪度驾驶舱）：
    前三版都在「卡片怎么摆」上打转（三分区 × 12 栏 → 主从双栏 → 三列一屏），
    9 张卡一张没少，实测 1600 视口下仍然 2509px 高、右下角约 120 万 px² 死白。
    这一版换的是「配置这件事本身的存在形式」：

      页面 = 一屏**环境状态**：环形就绪度（必填项几项就绪）+ 8 张能力卡
      （状态灯 / 关键值 / 「检测」「配置」），回答的是「现在能不能开工」，
      不是「参数填什么」。

      编辑控件收进**右侧抽屉**（.cfg-drawer，Teleport 到 body）：点卡的「配置」
      才展开那一段表单 —— 一屏之外不再有长表单在流里。大模型例外：它的字段
      本来就在独立弹窗 #aimodal 里，卡上只留只读摘要 + 入口按钮。

    契约：cfg-* 字段 id 一个没动（settings / page_login / browser_ai 三个用例按 id
    定位）；抽屉**常驻 DOM**（.hidden 一档开关，不是 v-if），所以
    `getElementById` 在抽屉关着时也能拿到全部字段 —— 这是「hidden 契约」。

    「检测」不是摆设：它重跑一次服务端预检（/api/health 内含 git 子进程，
    真读路径 / 当前分支 / 工作区脏不脏 / 闸门命令），把结论写回卡上那一行。
-->
<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useSettings } from '../composables/useSettings.js'
import { useHealth } from '../composables/useHealth.js'
import { useToast } from '../composables/useToast.js'

const {
  form, ph, ai, settings,
  aiSummaryHtml, gatePreviewHtml, repoHint,
  saving, gateDirty,
  loadSettings, saveSettings, onGateInput, onRepoPathChange,
} = useSettings()

const { health, loadHealth } = useHealth()
const { toast } = useToast()

// ---------------------------------------------------------------------------
// 能力卡定义。rows 是函数：它读到的是响应式的 form / settings，
// 值一变卡面就跟着变（不用手动刷新）。
//   req     是否「不配就跑不起来」的必填项（环形就绪度只数必填）
//   body    'ai' → 卡体是大模型只读摘要卡；'gate' → 卡体是闸门命令预览
// ---------------------------------------------------------------------------
const cut = (s, n) => {
  s = String(s ?? '').trim()
  return s ? (s.length > n ? s.slice(0, n - 1) + '…' : s) : ''
}

const CARDS = [
  {
    key: 'repo', name: '代码仓库', tag: '必填', req: true,
    rows: () => [['路径', cut(form.repo.path, 28)], ['分支', form.repo.branch]],
  },
  { key: 'ai', name: '大模型', tag: '必填', req: true, body: 'ai', rows: () => [] },
  {
    key: 'ones', name: 'ONES 接入', tag: '取工单', req: false,
    rows: () => [
      ['地址', cut(form.ones.base_url, 26)],
      ['项目', cut(form.ones.project_uuid, 18)],
      ['账号', form.ones.email],
    ],
  },
  {
    key: 'figma', name: 'Figma 接入', tag: '需求开发', req: false,
    rows: () => [
      ['Token', (settings.value && settings.value.has_figma_token)
        ? '已保存 ' + (((settings.value.figma || {}).token_masked) || '') : ''],
      ['账号', form.figma.account],
    ],
  },
  { key: 'gate', name: '验收闸门', tag: '必填', req: true, body: 'gate', rows: () => [] },
  {
    key: 'agent', name: 'Agentic 修复', tag: '流水线', req: true,
    rows: () => [
      ['轮次', form.agent.max_rounds ? form.agent.max_rounds + ' 轮' : ''],
      ['沙箱', form.agent.sandbox],
      ['预算', form.agent.deadline_seconds ? form.agent.deadline_seconds + ' 秒' : ''],
    ],
  },
  {
    key: 'playwright', name: '页面验证', tag: '可选', req: false,
    rows: () => [
      ['被测应用', cut(form.playwright.base_url, 26)],
      ['模式', form.playwright.headless ? '无头' : '有头'],
    ],
  },
  {
    key: 'pagelogin', name: '页面验证登录', tag: '可选', req: false,
    rows: () => [
      ['自动登录', form.pagelogin.enabled ? '开启' : '关闭'],
      ['账号', form.pagelogin.email],
    ],
  },
]

const CARD_BY_KEY = Object.fromEntries(CARDS.map((c) => [c.key, c]))

// ---------------------------------------------------------------------------
// 就绪度：状态灯与环形共用一个判断，避免「卡上绿点、环里没数」这种自相矛盾
// ---------------------------------------------------------------------------
function gateCount() {
  const lines = String(form.gate.commands_text || '').split('\n')
    .map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
  if (gateDirty.value) return lines.length
  const fromServer = (settings.value && settings.value.gate && settings.value.gate.commands) || []
  return lines.length || fromServer.length
}

const cardStates = computed(() => {
  const h = health.value || {}
  const s = settings.value || {}
  const repo = form.repo.path.trim()
  const gateN = gateCount()
  const onesToken = s.has_token || s.has_password
  return {
    repo: !repo ? 'off' : (h.repo_exists === false ? 'warn' : 'ok'),
    ai: (!ai.model && !ai.mock) ? 'off' : ((ai.mock || s.has_api_key) ? 'ok' : 'warn'),
    ones: (!form.ones.base_url || !(form.ones.project_uuid || h.ones_configured))
      ? 'off' : (onesToken ? 'ok' : 'warn'),
    figma: s.has_figma_token ? 'ok' : 'off',
    gate: gateN ? 'ok' : ((h.gate_commands || []).length ? 'warn' : 'off'),
    agent: form.agent.enabled ? 'ok' : 'off',
    playwright: form.playwright.base_url.trim() ? 'ok' : 'off',
    pagelogin: form.pagelogin.enabled ? (form.pagelogin.email.trim() ? 'ok' : 'warn') : 'off',
  }
})

const REQ_CARDS = CARDS.filter((c) => c.req)
const readyCount = computed(() => REQ_CARDS.filter((c) => cardStates.value[c.key] === 'ok').length)
const reqTotal = computed(() => REQ_CARDS.length)

const RING_C = 2 * Math.PI * 52
const ringC = RING_C.toFixed(1)
const ringOffset = computed(() => {
  const t = reqTotal.value || 1
  return (RING_C * (1 - readyCount.value / t)).toFixed(1)
})

const heroTitle = computed(() => readyCount.value === reqTotal.value
  ? '必填项全部就绪，可以跑完整流水线'
  : `还差 ${reqTotal.value - readyCount.value} 项必填，暂时不能跑完整流水线`)

const gaps = computed(() => CARDS
  .filter((c) => cardStates.value[c.key] !== 'ok')
  .map((c) => ({
    text: (c.req ? '' : '可选 · ') + c.name
      + (cardStates.value[c.key] === 'off' ? ' 未配置' : ' 待完善'),
  })))

// ---------------------------------------------------------------------------
// 「检测」：重跑一次 /api/health（内含 git 子进程，真读仓库状态），
// 再把结论写回卡上那一行。不是本地瞎猜。
// ---------------------------------------------------------------------------
const checks = reactive({})
const checking = ref('')

function buildCheck(key) {
  const h = health.value || {}
  const s = settings.value || {}
  switch (key) {
    case 'repo': {
      if (!form.repo.path.trim()) return { level: 'bad', text: '未填仓库路径，流水线不知道改写哪个仓库' }
      if (h.repo_exists === false) return { level: 'bad', text: `路径不存在：${form.repo.path}` }
      const bits = ['路径存在']
      if (h.current_branch) bits.push(`当前分支 ${h.current_branch}`)
      bits.push(h.dirty ? '工作区有未提交改动' : '工作区干净')
      return { level: h.dirty ? 'warn' : 'ok', text: bits.join(' · ') }
    }
    case 'ai': {
      if (ai.mock || h.ai_mode === 'mock') return { level: 'warn', text: 'Mock 模式：不会发起真实调用' }
      const problems = h.ai_problems || []
      return problems.length
        ? { level: 'bad', text: String(problems[0]) }
        : { level: 'ok', text: `配置校验通过 · ${h.ai_model || ai.model}` }
    }
    case 'ones':
      return h.ones_configured
        ? { level: 'ok', text: `已配置（地址 + Access Token + 项目 UUID）· ${cut(form.ones.base_url, 30)}` }
        : { level: 'bad', text: '未配全：地址 / Access Token（或邮箱密码）/ Project UUID 三样都要有' }
    case 'figma':
      return s.has_figma_token
        ? { level: 'ok', text: 'Personal Access Token 已保存' }
        : { level: 'bad', text: '未配 Token：需求开发页取稿会退回「浏览器打开」兜底' }
    case 'gate': {
      const n = gateCount()
      if (n) return { level: 'ok', text: `显式闸门 ${n} 条命令，逐条真实执行` }
      const detected = h.gate_commands || []
      if (detected.length) {
        return { level: 'warn', text: `未填显式命令，已从 package.json 探测到 ${detected.length} 条` }
      }
      return { level: 'bad', text: '没有可执行命令，闸门降级为语法检查 —— 它只说明没崩，不说明对' }
    }
    case 'agent':
      return form.agent.enabled
        ? {
          level: 'ok',
          text: `已开启 · ${form.agent.max_rounds} 轮 / ${form.agent.deadline_seconds} 秒`
            + ` · 沙箱 ${form.agent.sandbox}`,
        }
        : { level: 'warn', text: '已关闭：修复阶段只出补丁，不做沙箱真跑' }
    case 'playwright':
      return form.playwright.base_url.trim()
        ? { level: 'ok', text: `被测应用 ${form.playwright.base_url}` }
        : { level: 'bad', text: '未填被测应用地址：页面验证与截图会跳过' }
    case 'pagelogin':
      if (!form.pagelogin.enabled) {
        return { level: 'warn', text: '未开启：复用已保存的登录态（screenshots/_page_session.json）' }
      }
      return form.pagelogin.email.trim()
        ? { level: 'ok', text: `自动登录已开启 · ${form.pagelogin.email}` }
        : { level: 'bad', text: '已开启但没有登录账号' }
    default:
      return { level: 'warn', text: '没有可检测的信号' }
  }
}

async function runCheck(key) {
  if (checking.value) return
  checking.value = key
  try {
    await loadHealth()
    const r = buildCheck(key)
    checks[key] = r
    toast(`${CARD_BY_KEY[key].name}：${r.text}`, r.level === 'bad' ? 'err' : (r.level === 'warn' ? 'warn' : 'ok'))
  } finally {
    checking.value = ''
  }
}

async function checkAll() {
  if (checking.value) return
  checking.value = '__all__'
  try {
    await loadHealth()
    CARDS.forEach((c) => { checks[c.key] = buildCheck(c.key) })
    const bad = CARDS.filter((c) => checks[c.key].level === 'bad').length
    toast(bad ? `体检完成：${bad} 项需要处理` : '体检完成：没有发现阻塞项',
      bad ? 'warn' : 'ok')
  } finally {
    checking.value = ''
  }
}

// ---------------------------------------------------------------------------
// 抽屉：一次只装一段表单（其余用 .hidden 收着，仍在 DOM 里）
// ---------------------------------------------------------------------------
const drawerKey = ref('')
const drawerCard = computed(() => CARD_BY_KEY[drawerKey.value] || null)

function openCard(c) {
  if (c.modal) { openAiModal(); return }
  drawerKey.value = c.key
}
function closeDrawer() { drawerKey.value = '' }

function onKeydown(e) {
  if (e.key === 'Escape' && drawerKey.value) closeDrawer()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

function openAiModal() {
  window.openAiModal && window.openAiModal()
}
</script>

<template>
  <section class="panel" id="config-panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></svg>
        配置
        <span class="head-note">仓库 · 模型 · 接入 · 闸门</span>
      </h2>
      <span class="cfg-tip">点卡的「配置」在右侧抽屉里改；密钥类字段留空＝保持不变</span>
    </div>

    <div class="config-grid ck">
      <!-- ── 顶部：就绪度。回答「现在能不能开工」，不是「参数填什么」 ── -->
      <div class="ck-hero">
        <div class="ck-ring" id="cfg-ready-ring">
          <svg viewBox="0 0 120 120" width="108" height="108" aria-hidden="true">
            <circle cx="60" cy="60" r="52" fill="none" stroke="var(--border-strong)" stroke-width="9" />
            <circle cx="60" cy="60" r="52" fill="none" stroke="var(--primary)" stroke-width="9"
                    stroke-linecap="round" :stroke-dasharray="ringC" :stroke-dashoffset="ringOffset" />
          </svg>
          <div class="ck-num">
            <b>{{ readyCount }}<i>/{{ reqTotal }}</i></b>
            <span>必填项就绪</span>
          </div>
        </div>

        <div class="ck-hero-txt">
          <h3>{{ heroTitle }}</h3>
          <p>这一页回答的是「现在能不能开工」，不是「参数填什么」。点任意能力卡的「配置」在右侧抽屉里改。</p>
          <div class="ck-todo">
            <template v-if="gaps.length">
              <span v-for="g in gaps" :key="g.text">{{ g.text }}</span>
            </template>
            <span v-else class="ok">全部就绪</span>
          </div>
        </div>

        <div class="ck-hero-act">
          <button type="button" class="ck-btn pri" :disabled="!!checking" @click="checkAll">
            {{ checking === '__all__' ? '体检中…' : '全部检测' }}
          </button>
          <button type="button" class="ck-btn" @click="loadSettings">重新读取配置</button>
        </div>
      </div>

      <!-- ── 能力卡墙：8 张卡 = 8 项能力，卡上只放「状态 + 关键值」 ── -->
      <div class="ck-grid">
        <section v-for="c in CARDS" :key="c.key" class="ck-card" :data-key="c.key">
          <div class="ck-head">
            <span class="ck-dot" :class="cardStates[c.key]"></span>
            <span class="ck-name">{{ c.name }}</span>
            <span class="ck-tag">{{ c.tag }}</span>
          </div>

          <!-- 大模型：卡体就是只读摘要（#ai-summary 由 useSettings 渲染），
               字段全在独立弹窗 #aimodal 里，这里只留入口 -->
          <div v-if="c.body === 'ai'" class="settings-group" id="ai-summary-group">
            <div class="ai-summary" id="ai-summary" v-html="aiSummaryHtml"></div>
            <div class="settings-actions">
              <el-button class="btn-ghost" @click="openAiModal">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>
                大模型设置
              </el-button>
            </div>
          </div>

          <!-- 验收闸门：卡体是「哪几条命令真的会跑」的预览（#gate-preview） -->
          <div v-else-if="c.body === 'gate'" class="gate-preview" id="gate-preview"
               v-html="gatePreviewHtml"></div>

          <div v-else class="ck-kv">
            <div v-for="(r, i) in c.rows()" :key="i">
              <b>{{ r[0] }}</b>
              <span :class="{ void: !r[1] }">{{ r[1] || '未配置' }}</span>
            </div>
          </div>

          <div v-if="c.key === 'repo'" class="ck-note" id="repo-hint">{{ repoHint }}</div>

          <div v-if="checks[c.key]" class="ck-check" :class="checks[c.key].level">
            {{ checks[c.key].text }}
          </div>

          <div class="ck-foot">
            <button type="button" class="ck-mini" :disabled="!!checking" @click="runCheck(c.key)">
              {{ checking === c.key ? '检测中…' : '检测' }}
            </button>
            <button type="button" class="ck-mini pri cfg" @click="openCard(c)">
              {{ c.modal ? '设置' : '配置' }}
            </button>
          </div>
        </section>
      </div>
    </div>

    <div class="config-foot">
      <div class="settings-actions" id="config-actions">
        <el-button type="primary" class="btn-primary" id="btn-save" :disabled="saving" @click="saveSettings">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          保存配置
        </el-button>
        <el-button class="btn-ghost" @click="loadSettings">重置</el-button>
      </div>
      <p class="settings-note">配置保存在本地 <code>ui_settings.json</code>（已加入 .gitignore，密钥不会进 git）。</p>
    </div>

    <!-- ── 编辑抽屉：常驻 DOM，只切 .hidden；一次只显示被点开的那一段表单 ──
         Teleport 到 body 是为了 position:fixed 不受面板内层叠上下文影响。 -->
    <Teleport to="body">
      <div class="cfg-mask" :class="{ hidden: !drawerKey }" @click="closeDrawer"></div>
      <aside class="cfg-drawer" :class="{ hidden: !drawerKey }" role="dialog" aria-modal="true"
             :aria-label="drawerCard ? drawerCard.name : '配置编辑'">
        <div class="cfg-drawer-head">
          <h3>{{ drawerCard ? drawerCard.name : '' }}</h3>
          <span class="cfg-drawer-tag">{{ drawerCard ? drawerCard.tag : '' }}</span>
          <button type="button" class="x" title="关闭（Esc）" @click="closeDrawer">✕</button>
        </div>

        <div class="cfg-drawer-body">
          <!-- 代码仓库 -->
          <section class="settings-group" id="cfg-group-repo" :class="{ hidden: drawerKey !== 'repo' }">
            <div class="group-head">
              <span class="group-title">代码仓库</span>
              <span class="group-tag req">必填</span>
            </div>
            <label class="field">
              <span>仓库路径</span>
              <el-input id="cfg-repo-path" v-model="form.repo.path"
                        placeholder="D:/code/your-repo" @change="onRepoPathChange" />
            </label>
            <label class="field">
              <span>分支</span>
              <el-input id="cfg-repo-branch" v-model="form.repo.branch" placeholder="main" />
            </label>
            <div class="info-note">所有补丁只写进仓库外的临时副本，这里定的是「读哪儿、拿哪条分支当基线」。</div>
          </section>

          <!-- ONES 接入 -->
          <section class="settings-group" id="cfg-group-ones" :class="{ hidden: drawerKey !== 'ones' }">
            <div class="group-head">
              <span class="group-title">ONES 接入</span>
              <span class="group-tag">关闭 Mock 时生效</span>
            </div>
            <div class="field-row">
              <label class="field">
                <span>Base URL</span>
                <el-input id="cfg-ones-url" v-model="form.ones.base_url"
                          placeholder="https://ones.your-company.com" />
              </label>
              <label class="field">
                <span>Project UUID</span>
                <el-input id="cfg-ones-uuid" v-model="form.ones.project_uuid" placeholder="项目 UUID" />
              </label>
            </div>
            <div class="field-row">
              <label class="field">
                <span>Team UUID</span>
                <el-input id="cfg-ones-team" v-model="form.ones.team_uuid"
                          placeholder="浏览器地址栏 /team/XXXX 里那一段" />
              </label>
              <label class="field">
                <span>登录邮箱（推荐）</span>
                <el-input id="cfg-ones-email" v-model="form.ones.email"
                          placeholder="你登录 ONES 用的邮箱" autocomplete="off" />
              </label>
            </div>
            <div class="field-row">
              <label class="field">
                <span>登录密码</span>
                <el-input id="cfg-ones-password" type="password" v-model="form.ones.password"
                          :placeholder="ph.onesPassword" autocomplete="off" />
                <small class="hint" id="cfg-ones-password-hint"></small>
              </label>
              <label class="field">
                <span>Access Token（可选）</span>
                <el-input id="cfg-ones-token" type="password" v-model="form.ones.token"
                          :placeholder="ph.onesToken" autocomplete="off" />
                <small class="hint" id="cfg-ones-token-hint"></small>
              </label>
            </div>
            <div class="card-note">登录邮箱用于 token 过期时自动重登；Access Token 与「邮箱 + 密码」二选一即可。</div>
          </section>

          <!-- Figma 接入 -->
          <section class="settings-group" id="cfg-group-figma" :class="{ hidden: drawerKey !== 'figma' }">
            <div class="group-head">
              <span class="group-title">Figma 接入</span>
              <span class="group-tag">需求开发用</span>
            </div>
            <label class="field">
              <span>Personal Access Token</span>
              <el-input id="cfg-figma-token" type="password" v-model="form.figma.token"
                        :placeholder="ph.figma" autocomplete="off" />
              <small class="hint" id="cfg-figma-token-hint">Figma 个人设置 → Security → Personal access tokens 生成；只读权限即可</small>
            </label>
            <label class="field">
              <span>登录账号（内嵌看稿用，可选）</span>
              <el-input id="cfg-figma-account" v-model="form.figma.account"
                        placeholder="Figma 登录邮箱；SSO 企业账号留空" autocomplete="off" />
              <small class="hint" id="cfg-figma-account-hint">需求开发页「内嵌看稿」打开登录页时代填；官方 REST 不认账号密码，结构化取稿仍走上面的 Token</small>
            </label>
            <label class="field">
              <span>登录密码（内嵌看稿用，可选）</span>
              <el-input id="cfg-figma-password" type="password" v-model="form.figma.password"
                        :placeholder="ph.figmaPassword" autocomplete="new-password" />
            </label>
            <div class="info-note">Figma 官方接口只有 Token 一种凭据（企业 SSO 也是通过 OAuth 换 token），<strong>不支持账号密码登录</strong>；账号密码在这个工具里走的是另一条链路——「页面验证登录」。上面的登录账号 / 密码只给「内嵌看稿」的网页登录用，遇 SSO / 验证码时在面板里人工登一次，登录态会保存下来下次直接用。</div>
          </section>

          <!-- 页面验证登录 -->
          <section class="settings-group" id="cfg-group-pagelogin"
                   :class="{ hidden: drawerKey !== 'pagelogin' }">
            <div class="group-head">
              <span class="group-title">页面验证登录</span>
              <span class="group-tag">可选</span>
            </div>
            <!-- 开关保留原生 checkbox + .track 自绘样式：测试只按 id 定位，不操作它，
                 且 el-switch 会改掉 DOM 结构；原 .switch 已是自绘开关，外壳不动。 -->
            <label class="switch">
              <input type="checkbox" id="cfg-pagelogin-enabled" v-model="form.pagelogin.enabled" />
              <span class="track"></span><span class="switch-label">截图前自动登录被验收的应用（关闭则复用已保存的登录态）</span>
            </label>
            <label class="field">
              <span>登录账号</span>
              <el-input id="cfg-pagelogin-email" v-model="form.pagelogin.email"
                        placeholder="邮箱 / 用户名 / 手机号" autocomplete="off" />
            </label>
            <label class="field">
              <span>登录密码</span>
              <el-input id="cfg-pagelogin-password" type="password" v-model="form.pagelogin.password"
                        :placeholder="ph.pagePassword" autocomplete="off" />
              <small class="hint" id="cfg-pagelogin-password-hint"></small>
            </label>
            <div class="info-note">填的是<strong>被测前端应用</strong>的账号密码，与 ONES / Figma 的凭据无关。<br>仅当工单的复现步骤里本身包含「输入账号密码 → 点登录」时才会被使用（值替换为这里的真实凭据），不会去猜登录页；<br>登录态保存在 <code>screenshots/_page_session.json</code>，后续截图直接复用。</div>
          </section>

          <!-- 验收闸门 -->
          <section class="settings-group" id="cfg-group-gate" :class="{ hidden: drawerKey !== 'gate' }">
            <div class="group-head">
              <span class="group-title">验收闸门</span>
              <span class="group-tag req">必填</span>
            </div>
            <label class="field">
              <span>闸门命令（每行一条，留空则自动探测）</span>
              <el-input id="cfg-gate-commands" type="textarea" :rows="4"
                        v-model="form.gate.commands_text" @input="onGateInput"
                        placeholder="npm run type-check&#10;npm run lint&#10;npm run test:unit -- --run" />
            </label>
            <label class="switch">
              <input type="checkbox" id="cfg-gate-degraded" v-model="form.gate.degraded" />
              <span class="track"></span><span class="switch-label">无命令时降级为语法检查（默认开）</span>
            </label>
            <div class="card-note">没有可执行命令时，「验证通过」不成立 —— 降级语法检查只说明没崩，不说明对。</div>
          </section>

          <!-- Agentic 修复 -->
          <section class="settings-group" id="cfg-agent-group" :class="{ hidden: drawerKey !== 'agent' }">
            <div class="group-head">
              <span class="group-title">Agentic 修复</span>
              <span class="group-tag">缺陷流水线</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="cfg-agent-enabled" v-model="form.agent.enabled" />
              <span class="track"></span><span class="switch-label">修复阶段走「改 → 沙箱真跑 → 读报错 → 再改」的闭环</span>
            </label>
            <div class="field-row">
              <label class="field">
                <span>最大轮次（一次「交补丁 + 拿真实验证」算一轮）</span>
                <el-input-number id="cfg-agent-rounds" v-model="form.agent.max_rounds"
                                 :min="1" :max="24" controls-position="right" />
              </label>
              <label class="field">
                <span>墙钟预算（秒）</span>
                <el-input-number id="cfg-agent-deadline" v-model="form.agent.deadline_seconds"
                                 :min="60" :max="1800" :step="30" controls-position="right" />
              </label>
            </div>
            <div class="field-row">
              <label class="field">
                <span>连续几次同样的验证结果就认输转人工</span>
                <el-input-number id="cfg-agent-stall" v-model="form.agent.max_stall"
                                 :min="1" :max="8" controls-position="right" />
              </label>
              <label class="field">
                <span>沙箱后端</span>
                <el-select id="cfg-agent-sandbox" v-model="form.agent.sandbox">
                  <el-option label="auto（有 git 用 worktree，否则拷贝）" value="auto" />
                  <el-option label="worktree（git 临时工作树）" value="worktree" />
                  <el-option label="copy（逐文件拷贝）" value="copy" />
                </el-select>
              </label>
            </div>
            <label class="field">
              <span>沙箱根目录（留空用系统临时目录）</span>
              <el-input id="cfg-agent-sandbox-dir" v-model="form.agent.sandbox_dir"
                        placeholder="例如 D:/tmp/ones-sandbox" />
            </label>
            <label class="switch">
              <input type="checkbox" id="cfg-agent-nm" v-model="form.agent.link_node_modules" />
              <span class="track"></span><span class="switch-label">把源仓库 node_modules 链进沙箱（关掉会有大片假失败）</span>
            </label>
            <label class="switch">
              <input type="checkbox" id="cfg-agent-pageread" v-model="form.agent.page_read" />
              <span class="track"></span><span class="switch-label">让模型看修复前截图与 console 报错（多模态判读）</span>
            </label>
            <label class="field">
              <span>复现环节（先写用例跑红，再动手修）</span>
              <el-select id="cfg-agent-repro" v-model="form.agent.repro">
                <el-option label="auto（探测到可用跑器才做，没有则退化成断言脚本）" value="auto" />
                <el-option label="on（必须做，做不到就在提案里标明）" value="on" />
                <el-option label="off（只要验收命令级证据）" value="off" />
              </el-select>
            </label>
            <label class="field">
              <span>复现命令（留空自动探测；{file} = 用例路径）</span>
              <el-input id="cfg-agent-repro-cmd" v-model="form.agent.repro_command"
                        placeholder="npx vitest run --root packages/demo-app {file}" />
            </label>
            <div class="field-row">
              <label class="field">
                <span>用例复现失败时允许重写几次</span>
                <el-input-number id="cfg-agent-repro-rewrite" v-model="form.agent.repro_max_rewrite"
                                 :min="0" :max="6" controls-position="right" />
              </label>
              <label class="field">
                <span>注入几条同类历史缺陷先例（0=关）</span>
                <el-input-number id="cfg-agent-precedents" v-model="form.agent.precedents"
                                 :min="0" :max="8" controls-position="right" />
              </label>
            </div>
            <label class="field">
              <span>页面复验（拿到绿后在沙箱里起服务再拍一张）</span>
              <el-select id="cfg-agent-server" v-model="form.agent.sandbox_server">
                <el-option label="auto（探得到 dev 脚本才起服务）" value="auto" />
                <el-option label="on（必须起，起不来就标未复验）" value="on" />
                <el-option label="off（只要命令级证据）" value="off" />
              </el-select>
            </label>
            <label class="field">
              <span>dev server 命令（{port} 换成空闲端口）</span>
              <el-input id="cfg-agent-server-cmd" v-model="form.agent.sandbox_server_command"
                        placeholder="pnpm --filter demo-app dev --port {port}" />
            </label>
            <div class="field-row">
              <label class="field">
                <span>子包目录（monorepo 相对仓库根，可空）</span>
                <el-input id="cfg-agent-server-cwd" v-model="form.agent.sandbox_server_cwd"
                          placeholder="packages/demo-app" />
              </label>
              <label class="field">
                <span>等服务就绪的秒数</span>
                <el-input-number id="cfg-agent-server-timeout"
                                 v-model="form.agent.sandbox_server_ready_timeout"
                                 :min="20" :max="600" :step="10" controls-position="right" />
              </label>
            </div>
            <label class="field">
              <span>只读接口探测（判后端归因要有真实返回，不靠猜）</span>
              <el-select id="cfg-agent-probe" v-model="form.agent.api_probe">
                <el-option label="auto（填了被测应用地址就启用）" value="auto" />
                <el-option label="on（尽量启用）" value="on" />
                <el-option label="off（关掉，后端归因只能靠仓库引用）" value="off" />
              </el-select>
            </label>
            <label class="field">
              <span>允许探测的主机（分号分隔，默认只允许被测应用域名）</span>
              <el-input id="cfg-agent-probe-hosts" v-model="form.agent.api_probe_hosts"
                        placeholder="ai.example.internal;gateway.example.internal" />
            </label>
            <div class="info-note">接口探测只发 GET/HEAD、只打白名单主机，并且<strong>只回字段名与类型</strong>：
              响应体不写进提案、不进知识卡片 —— 判契约要的是 <code>data.list: array</code> 这种形状，
              不是列表里的真实数据。</div>
            <div class="field-row">
              <label class="field">
                <span>单条命令超时（秒）</span>
                <el-input-number id="cfg-agent-cmd-timeout" v-model="form.agent.per_command_timeout"
                                 :min="15" :max="1800" :step="15" controls-position="right" />
              </label>
              <label class="field">
                <span>额外放行的命令正则（一行一条，默认已含 npm/npx/node/tsc/eslint/vitest/git diff）</span>
                <el-input id="cfg-agent-allow" type="textarea" :rows="2"
                          v-model="form.agent.command_allow_text"
                          placeholder="^(pnpm|make)\b" />
              </label>
            </div>
            <div class="info-note">补丁只写进<strong>仓库外的临时副本</strong>，你的工作区在修复阶段不会被写入；
              跑完即回收。要让「验证通过」这件事真正成立，上面「验收闸门」里必须有可执行的命令
              （云枢这类没配脚本的仓库，填 <code>npx vue-tsc --noEmit</code> / <code>npx vitest run</code> 就能跑起来）。</div>
          </section>

          <!-- 页面验证 -->
          <section class="settings-group" id="cfg-group-playwright"
                   :class="{ hidden: drawerKey !== 'playwright' }">
            <div class="group-head">
              <span class="group-title">页面验证</span>
              <span class="group-tag">可选</span>
            </div>
            <div class="cfg-inline">
              <label class="field field-inline-label">
                <span>被测应用地址</span>
                <el-input id="cfg-app-url" v-model="form.playwright.base_url"
                          placeholder="http://localhost:3000" />
              </label>
              <label class="switch">
                <input type="checkbox" id="cfg-headless" v-model="form.playwright.headless" />
                <span class="track"></span><span class="switch-label">无头模式运行浏览器</span>
              </label>
            </div>
            <div class="card-note">这一项决定了「修复后页面复验」和缺陷截图能不能拍：地址不通就没有页面级证据。</div>
          </section>
        </div>

        <div class="cfg-drawer-foot">
          <el-button type="primary" class="btn-primary" :disabled="saving" @click="saveSettings">
            保存配置
          </el-button>
          <el-button class="btn-ghost" @click="loadSettings">重置</el-button>
        </div>
      </aside>
    </Teleport>
  </section>
</template>
