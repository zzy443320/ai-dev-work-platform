<!--
  侧栏配置面板（旧版 index.html:815-939 的 #settings-panel）。
  字段 id 全部沿用 cfg-*，测试与自检脚本按 id 定位。
  与 AiModal 共用 useSettings() —— 模块级单例，两个「保存配置」提交同一份数据。
-->
<script setup>
import { useSettings } from '../composables/useSettings.js'
import { useHealth } from '../composables/useHealth.js'

const {
  form, ph, ai, settings,
  aiSummaryHtml, gatePreviewHtml, repoHint,
  saving, aiKeyCleared,
  loadSettings, saveSettings, onGateInput, onRepoPathChange,
} = useSettings()

const { health } = useHealth()

function openAiModal() {
  window.openAiModal && window.openAiModal()
}
</script>

<template>
  <aside class="col-side">
    <section class="panel" id="settings-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>
          配置
        </h2>
      </div>

      <div class="settings-group">
        <div class="group-title">代码仓库</div>
        <label class="field">
          <span>仓库路径</span>
          <input type="text" id="cfg-repo-path" v-model="form.repo.path"
                 placeholder="D:/code/your-repo" @change="onRepoPathChange" />
        </label>
        <div class="repo-hint" id="repo-hint">{{ repoHint }}</div>
        <label class="field">
          <span>分支</span>
          <input type="text" id="cfg-repo-branch" v-model="form.repo.branch" placeholder="main" />
        </label>
      </div>

      <div class="settings-group" id="ai-summary-group">
        <div class="group-title">大模型</div>
        <div class="ai-summary" id="ai-summary" v-html="aiSummaryHtml"></div>
        <div class="settings-actions">
          <button class="btn-ghost" @click="openAiModal">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>
            大模型设置
          </button>
        </div>
      </div>

      <div class="settings-group">
        <div class="group-title">ONES 接入（关闭 Mock 时生效）</div>
        <label class="field">
          <span>Base URL</span>
          <input type="text" id="cfg-ones-url" v-model="form.ones.base_url"
                 placeholder="https://ones.your-company.com" />
        </label>
        <label class="field">
          <span>Project UUID</span>
          <input type="text" id="cfg-ones-uuid" v-model="form.ones.project_uuid" placeholder="项目 UUID" />
        </label>
        <label class="field">
          <span>Team UUID</span>
          <input type="text" id="cfg-ones-team" v-model="form.ones.team_uuid"
                 placeholder="浏览器地址栏 /team/XXXX 里那一段" />
        </label>
        <label class="field">
          <span>登录邮箱（推荐，token 过期时自动重登）</span>
          <input type="text" id="cfg-ones-email" v-model="form.ones.email"
                 placeholder="你登录 ONES 用的邮箱" autocomplete="off" />
        </label>
        <label class="field">
          <span>登录密码</span>
          <input type="password" id="cfg-ones-password" v-model="form.ones.password"
                 :placeholder="ph.onesPassword" autocomplete="off" />
          <small class="hint" id="cfg-ones-password-hint"></small>
        </label>
        <label class="field">
          <span>Access Token（可选，和账号密码二选一）</span>
          <input type="password" id="cfg-ones-token" v-model="form.ones.token"
                 :placeholder="ph.onesToken" autocomplete="off" />
          <small class="hint" id="cfg-ones-token-hint"></small>
        </label>
      </div>

      <div class="settings-group">
        <div class="group-title">Figma 接入（需求开发用）</div>
        <label class="field">
          <span>Personal Access Token</span>
          <input type="password" id="cfg-figma-token" v-model="form.figma.token"
                 :placeholder="ph.figma" autocomplete="off" />
          <small class="hint" id="cfg-figma-token-hint">Figma 个人设置 → Security → Personal access tokens 生成；只读权限即可</small>
        </label>
        <div class="info-note">Figma 官方接口只有 Token 一种凭据（企业 SSO 也是通过 OAuth 换 token），<strong>不支持账号密码登录</strong>；<br>账号密码在这个工具里用的是另一条链路——下面的「页面验证登录」。</div>
      </div>

      <div class="settings-group">
        <div class="group-title">页面验证登录（缺陷修复截图用，可选）</div>
        <label class="field field-check">
          <input type="checkbox" id="cfg-pagelogin-enabled" v-model="form.pagelogin.enabled" />
          <span>截图前自动登录被验收的应用（关闭则复用已保存的登录态）</span>
        </label>
        <label class="field">
          <span>登录账号（邮箱 / 用户名 / 手机号）</span>
          <input type="text" id="cfg-pagelogin-email" v-model="form.pagelogin.email"
                 placeholder="你登录该应用用的账号" autocomplete="off" />
        </label>
        <label class="field">
          <span>登录密码</span>
          <input type="password" id="cfg-pagelogin-password" v-model="form.pagelogin.password"
                 :placeholder="ph.pagePassword" autocomplete="off" />
          <small class="hint" id="cfg-pagelogin-password-hint"></small>
        </label>
        <div class="info-note">这里填的是<strong>被测前端应用</strong>的账号密码，与 ONES / Figma 的凭据无关。<br>仅当工单的复现步骤里本身包含「输入账号密码 → 点登录」时才会被使用（值替换为这里的真实凭据），不会去猜登录页；<br>登录态保存在 <code>screenshots/_page_session.json</code>，后续截图直接复用。</div>
      </div>

      <div class="settings-group">
        <div class="group-title">验收闸门</div>
        <label class="field">
          <span>闸门命令（每行一条，留空则自动探测）</span>
          <textarea id="cfg-gate-commands" rows="4" spellcheck="false"
                    v-model="form.gate.commands_text" @input="onGateInput"
                    placeholder="npm run type-check&#10;npm run lint&#10;npm run test:unit -- --run"></textarea>
        </label>
        <label class="switch">
          <input type="checkbox" id="cfg-gate-degraded" v-model="form.gate.degraded" />
          <span class="track"></span><span class="switch-label">无命令时降级为语法检查（默认开）</span>
        </label>
        <div class="gate-preview" id="gate-preview" v-html="gatePreviewHtml"></div>
      </div>

      <div class="settings-group">
        <div class="group-title">页面验证</div>
        <label class="field">
          <span>被测应用地址</span>
          <input type="text" id="cfg-app-url" v-model="form.playwright.base_url"
                 placeholder="http://localhost:3000" />
        </label>
        <label class="switch">
          <input type="checkbox" id="cfg-headless" v-model="form.playwright.headless" />
          <span class="track"></span><span class="switch-label">无头模式运行浏览器</span>
        </label>
      </div>

      <div class="settings-actions">
        <button class="btn-primary" id="btn-save" :disabled="saving" @click="saveSettings">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          保存配置
        </button>
        <button class="btn-ghost" @click="loadSettings">重置</button>
      </div>
      <p class="settings-note">配置保存在本地 <code>ui_settings.json</code>（已加入 .gitignore，密钥不会进 git）。</p>
    </section>
  </aside>
</template>
