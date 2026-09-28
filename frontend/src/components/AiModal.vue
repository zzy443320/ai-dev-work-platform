<!--
  大模型配置弹窗（旧版 index.html:953-1105 的 #aimodal）。
  与 SettingsPanel 共用 useSettings() 单例，表单字段 / 保存按钮语义一致。

  可见性：由 App.vue 的 showAiModal 控制（在 #aimodal 上 toggle hidden class）；
  这里只管内容。#ai-test-result 用 :class="{ hidden: ... }" 而不是 v-if —— 旧版
  测试脚本断言 el.classList.contains('hidden')，元素必须始终在 DOM 里。
-->
<script setup>
import { onMounted, ref, nextTick, computed } from 'vue'
import { useSettings } from '../composables/useSettings.js'

const {
  ai, modelHint, modelSuggestList, presets,
  aiTest, testing, saving,
  providerHint, endpointPh, modelsPathPh, contentPathPh, authHeaderNeeded,
  keyPlaceholder,
  loadAiPresets, applyAiPreset, clearAiKey, testAi, closeAiTestResult,
  fetchAiModels, saveSettings,
} = useSettings()

const emit = defineEmits(['close'])

/** 显隐由 App.vue 传入（旧版 openAiModal/closeAiModal 只切 #aimodal 的 hidden class）。
    #aimodal 必须**始终在 DOM 里** —— 旧版用例会查询它并断言 classList 含 hidden。 */
const props = defineProps({ visible: { type: Boolean, default: false } })

const suggestItems = computed(() => {
  const q = (ai.model || '').trim().toLowerCase()
  return modelSuggestList.value.filter((m) => !q || m.toLowerCase().includes(q))
})

onMounted(() => { loadAiPresets() })

function onPresetChange(e) {
  const v = e.target.value
  if (v) applyAiPreset(v)
}

// ── 模型名建议浮层（旧版 cs-panel，position:fixed 挂 body） ──
const suggestOpen = ref(false)
const suggestStyle = ref({})
const modelInput = ref(null)
const suggestPanel = ref(null)

function refreshSuggest() {
  const items = suggestItems.value
  if (!items.length) { suggestOpen.value = false; return }
  suggestOpen.value = true
  nextTick(() => {
    const inputEl = modelInput.value
    const panelEl = suggestPanel.value
    if (!inputEl || !panelEl) return
    const r = inputEl.getBoundingClientRect()
    const st = { minWidth: r.width + 'px' }
    st.left = Math.min(r.left, window.innerWidth - panelEl.offsetWidth - 8) + 'px'
    const below = window.innerHeight - r.bottom
    if (panelEl.offsetHeight + 10 <= below || below >= r.top) st.top = (r.bottom + 6) + 'px'
    else st.top = (r.top - panelEl.offsetHeight - 6) + 'px'
    suggestStyle.value = st
  })
}
function pickModel(name) {
  ai.model = name
  suggestOpen.value = false
}
</script>

<template>
  <div class="modal aimodal" id="aimodal" :class="{ hidden: !props.visible }" @click.self="emit('close')">
    <div class="modal-box aimodal-box">
      <div class="modal-head">
        <h3>大模型配置</h3>
        <button class="close" @click="emit('close')">×</button>
      </div>
      <div class="aimodal-body">
        <section class="settings-group">
          <label class="switch">
            <input type="checkbox" id="cfg-ai-mock" v-model="ai.mock" />
            <span class="track"></span><span class="switch-label">Mock 模式（不调用真实 API）</span>
          </label>

          <label class="field">
            <span>快速模板</span>
            <select id="cfg-ai-preset" @change="onPresetChange">
              <option value="">— 选择模板自动填充下方字段 —</option>
              <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.label }}</option>
            </select>
            <small class="hint">模板只是填一遍默认值，填完每一项都还能改。</small>
          </label>

          <div class="field-row">
            <label class="field">
              <span>接入协议</span>
              <select id="cfg-ai-provider" v-model="ai.provider">
                <option value="anthropic">Anthropic Messages</option>
                <option value="openai">OpenAI Chat Completions</option>
                <option value="custom">自定义（自建/异构网关）</option>
              </select>
            </label>
            <label class="field">
              <span>鉴权方式</span>
              <select id="cfg-ai-auth" v-model="ai.auth_style">
                <option value="bearer">Authorization: Bearer</option>
                <option value="x-api-key">x-api-key</option>
                <option value="header">自定义请求头</option>
                <option value="query">URL query 参数</option>
                <option value="none">不带凭据</option>
              </select>
            </label>
          </div>

          <label class="field">
            <span>Base URL</span>
            <input type="text" id="cfg-ai-base" v-model="ai.base_url"
                   placeholder="https://你的中转站.com/v1" autocomplete="off" />
            <small class="hint" id="cfg-ai-base-hint">{{ providerHint || '留空则用官方地址；中转站/自建网关请填到这里' }}</small>
          </label>

          <div class="field-row">
            <label class="field">
              <span>Chat 路径</span>
              <input type="text" id="cfg-ai-endpoint" v-model="ai.endpoint"
                     :placeholder="endpointPh" />
            </label>
            <label class="field">
              <span>模型列表路径</span>
              <input type="text" id="cfg-ai-models-path" v-model="ai.models_path"
                     :placeholder="modelsPathPh" />
            </label>
          </div>

          <!-- 鉴权字段名只在「自定义请求头 / URL query」时需要；旧版用 toggle('hidden') -->
          <label class="field" id="ai-auth-header-field" :class="{ hidden: !authHeaderNeeded }">
            <span>鉴权字段名</span>
            <input type="text" id="cfg-ai-auth-header" v-model="ai.auth_header"
                   placeholder="X-Auth-Token / api-key" autocomplete="off" />
            <small class="hint">仅在「自定义请求头」或「URL query 参数」时需要</small>
          </label>

          <label class="field">
            <span>模型名称</span>
            <span class="combo">
              <input type="text" id="cfg-ai-model" v-model="ai.model" ref="modelInput"
                     placeholder="任意该网关支持的模型名" autocomplete="off"
                     @focus="refreshSuggest" @input="refreshSuggest" @click.stop="refreshSuggest" />
              <button type="button" class="btn-mini" id="btn-ai-models"
                      :disabled="!!testing" @click="fetchAiModels">拉取列表</button>
            </span>
            <small class="hint" id="cfg-ai-model-hint">{{ modelHint }}</small>
          </label>

          <label class="field">
            <span>API Key</span>
            <span class="combo">
              <input type="password" id="cfg-ai-key" v-model="ai.api_key"
                     :placeholder="keyPlaceholder" autocomplete="off" />
              <button type="button" class="btn-mini" @click="clearAiKey">清除</button>
            </span>
            <small class="hint" id="cfg-ai-key-hint"></small>
          </label>

          <div class="field-row n4">
            <label class="field">
              <span>温度</span>
              <input type="number" id="cfg-ai-temp" step="0.1" min="0" max="2"
                     v-model="ai.temperature" placeholder="1" />
            </label>
            <label class="field">
              <span>最大输出</span>
              <input type="number" id="cfg-ai-max-tokens" min="1"
                     v-model="ai.max_tokens" placeholder="4000" />
            </label>
            <label class="field">
              <span>top_p</span>
              <input type="number" id="cfg-ai-top-p" step="0.05" min="0" max="1"
                     v-model="ai.top_p" placeholder="留空不传" />
            </label>
            <label class="field">
              <span>超时(秒)</span>
              <input type="number" id="cfg-ai-timeout" min="5"
                     v-model="ai.timeout" placeholder="120" />
            </label>
          </div>

          <details class="adv">
            <summary>高级：网关私有参数与响应适配</summary>
            <div class="adv-body">
              <label class="field">
                <span>响应取值路径</span>
                <input type="text" id="cfg-ai-content-path" v-model="ai.content_path"
                       :placeholder="contentPathPh" autocomplete="off" />
                <small class="hint">点号分隔，数字表示数组下标。网关返回结构不同就改这里，例如 <code>data.reply</code></small>
              </label>
              <label class="field">
                <span>max_tokens 字段名</span>
                <input type="text" id="cfg-ai-max-tokens-field" v-model="ai.max_tokens_field"
                       placeholder="max_tokens" autocomplete="off" />
                <small class="hint">部分新网关要求 <code>max_completion_tokens</code></small>
              </label>
              <label class="field">
                <span>system 角色名</span>
                <input type="text" id="cfg-ai-system-role" v-model="ai.system_role"
                       placeholder="system" autocomplete="off" />
                <small class="hint">有的网关把 system 改成了 developer</small>
              </label>
              <label class="field">
                <span>HTTP 代理</span>
                <input type="text" id="cfg-ai-proxy" v-model="ai.proxy"
                       placeholder="http://127.0.0.1:7890" autocomplete="off" />
              </label>
              <label class="field">
                <span>额外请求头（JSON 或每行 <code>名称: 值</code>）</span>
                <textarea id="cfg-ai-headers" rows="3" spellcheck="false"
                          v-model="ai.extra_headers" placeholder='{"X-Tenant": "h3yun"}'></textarea>
              </label>
              <label class="field">
                <span>额外请求体（JSON，会合并进请求）</span>
                <textarea id="cfg-ai-body" rows="3" spellcheck="false"
                          v-model="ai.extra_body" placeholder='{"enable_thinking": false}'></textarea>
              </label>
              <label class="switch">
                <input type="checkbox" id="cfg-ai-json-mode" v-model="ai.json_mode" />
                <span class="track"></span><span class="switch-label">请求 JSON 输出模式（response_format）</span>
              </label>
              <label class="switch">
                <input type="checkbox" id="cfg-ai-verify-ssl" v-model="ai.verify_ssl" />
                <span class="track"></span><span class="switch-label">校验 TLS 证书（内网自签证书可关）</span>
              </label>
            </div>
          </details>
        </section>
        <p class="settings-note">「保存配置」会连同其他设置一起保存到本地 <code>ui_settings.json</code>（密钥不会进 git）。</p>
      </div>

      <!-- 测试结果：始终在 DOM 里，只切 hidden（旧版契约） -->
      <div class="ai-test-result" id="ai-test-result"
           :class="[aiTest.cls, { hidden: !aiTest.visible }]"
           style="margin: 0 20px 10px;" v-html="aiTest.html"></div>

      <div class="aimodal-foot">
        <button type="button" class="btn-ghost" id="btn-ai-test"
                :disabled="!!testing" @click="testAi(false)">测试连接</button>
        <button type="button" class="btn-ghost" id="btn-ai-dry"
                :disabled="!!testing" @click="testAi(true)">只校验配置</button>
        <button class="btn-primary" id="btn-save-ai"
                :disabled="saving" @click="saveSettings">保存配置</button>
      </div>
    </div>

    <!-- 模型名建议浮层（position: fixed，等价旧版挂在 body 上的 .cs-panel） -->
    <div class="cs-panel" ref="suggestPanel" :class="{ open: suggestOpen }"
         :style="suggestStyle">
      <div class="cs-option" v-for="m in suggestItems" :key="m"
           :class="{ selected: m === ai.model }" @click="pickModel(m)">{{ m }}</div>
    </div>
  </div>
</template>
