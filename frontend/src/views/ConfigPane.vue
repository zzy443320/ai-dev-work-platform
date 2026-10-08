<!--
  配置模块（#pane-config）。2026-09-30 从右栏侧栏（#settings-panel）独立成页签：
  侧栏只留「重要配置只读摘要 + 入口」，全部编辑控件搬到这里。

  字段 id 全部沿用 cfg-*（测试与自检脚本按 id 定位），读写逻辑一行没动 ——
  与侧栏摘要、大模型弹窗 #aimodal 共用 useSettings() 的模块级单例，提交同一份数据。

  布局（2026-09-30 第二次改版）：
    旧版是 `auto-fit minmax(330px,1fr)` 的自动多列 —— 浏览器按最小宽硬切列，卡片高度
    差到 2.4 倍（实测同排 183 / 205 / 443），短的卡片下方一片死白，读不出分组关系。
    现在改成「三个语义分区 × 12 栏谱系」：

      .cfg-region 每个分区是一张 12 栏网格，分区标题通栏（grid-column: 1/-1）+
      右侧一条通栏细线；卡片用 `--span` 显式占栏 —— 占位数由内容量决定，不用猜。

      同排卡片 align-items: stretch 等高，卡片内 `margin-top: auto` 把「脚注」
      （.info-note / .gate-preview / .repo-hint / 按钮行）压到底边 —— 残差变成
      有意的脚注位，而不是半张卡片的空白。

      ONES 的 6 个字段两两成对（.field-row），443px → 约 240px。
      页面验证只有两个控件，铺满整行做横条（.cfg-inline），不再单独占一格。

  高度（2026-09-30 第三次改版）：不再给自己套 `max-height: 100vh - 常量` + 面板内滚。
    那是配合已删除的「三栏经典」的特例（要让右栏与问答网格底边对齐）。现在配置模块
    跟其他模块一样按内容自然流、整页滚动 —— 少一个只有这一页才出现的内层滚动条。
-->
<script setup>
import { useSettings } from '../composables/useSettings.js'

const {
  form, ph, ai, settings,
  aiSummaryHtml, gatePreviewHtml, repoHint,
  saving, aiKeyCleared,
  loadSettings, saveSettings, onGateInput, onRepoPathChange,
} = useSettings()

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
      <span class="cfg-tip">改完点「保存配置」；密钥类字段留空＝保持不变</span>
    </div>

    <div class="config-grid">
      <!-- ── 分区 1：先把「改动写进哪个仓库、用哪个模型」定下来 ── -->
      <section class="cfg-region">
        <div class="region-head">代码与模型</div>

        <div class="settings-group" style="--span:6">
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
          <div class="repo-hint" id="repo-hint">{{ repoHint }}</div>
        </div>

        <div class="settings-group" id="ai-summary-group" style="--span:6">
          <div class="group-head">
            <span class="group-title">大模型</span>
          </div>
          <div class="ai-summary" id="ai-summary" v-html="aiSummaryHtml"></div>
          <div class="settings-actions">
            <el-button class="btn-ghost" @click="openAiModal">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.9 2.9l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.9-2.9l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.9-2.9l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.9 2.9l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>
              大模型设置
            </el-button>
          </div>
        </div>
      </section>

      <!-- ── 分区 2：外部系统的凭据 ── -->
      <section class="cfg-region">
        <div class="region-head">外部接入</div>

        <div class="settings-group" style="--span:7">
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
        </div>

        <div class="settings-group" style="--span:5">
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
          <div class="info-note">Figma 官方接口只有 Token 一种凭据（企业 SSO 也是通过 OAuth 换 token），<strong>不支持账号密码登录</strong>；账号密码在这个工具里走的是另一条链路——「页面验证登录」。</div>
        </div>
      </section>

      <!-- ── 分区 3：跑起来之后怎么验收 ── -->
      <section class="cfg-region">
        <div class="region-head">运行与验证</div>

        <div class="settings-group" style="--span:5">
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
        </div>

        <div class="settings-group" style="--span:7">
          <div class="group-head">
            <span class="group-title">验收闸门</span>
            <span class="group-tag">可选</span>
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
          <div class="gate-preview" id="gate-preview" v-html="gatePreviewHtml"></div>
        </div>

        <div class="settings-group" id="cfg-agent-group" style="--span:5">
          <div class="group-head">
            <span class="group-title">Agentic 修复</span>
            <span class="group-tag">缺陷流水线</span>
          </div>
          <label class="switch">
            <input type="checkbox" id="cfg-agent-enabled" v-model="form.agent.enabled" />
            <span class="track"></span><span class="switch-label">修复阶段走「改 → 沙箱真跑 → 读报错 → 再改」的闭环</span>
          </label>
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
          <div class="info-note">补丁只写进<strong>仓库外的临时副本</strong>，你的工作区在修复阶段不会被写入；
            跑完即回收。要让「验证通过」这件事真正成立，上面「验收闸门」里必须有可执行的命令
            （云枢这类没配脚本的仓库，填 <code>npx vue-tsc --noEmit</code> / <code>npx vitest run</code> 就能跑起来）。</div>
        </div>

        <div class="settings-group" style="--span:12">
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
        </div>
      </section>
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
  </section>
</template>
