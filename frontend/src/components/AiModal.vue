<!--
  大模型配置弹窗（#aimodal）。
  与 SettingsPanel 共用 useSettings() 单例，表单字段 / 保存按钮语义一致。

  组件化收口：原生 input/select/textarea/checkbox/button 已全部换成
  el-input / el-select / el-switch / el-button。
    - **id 仍然是契约**：用例按 `#cfg-ai-*` 逐个断言存在，并用
      `page.input_value("#cfg-ai-model")` 读值。el-input 会把 attrs 里的 id
      透传到内部原生 <input>（Element 的实现是 mergeProps({id: inputId}, attrs)），
      所以 fill / input_value 照旧可用。
    - 仍保留原生标签的地方只有「模型名建议浮层」那块（自绘 .cs-panel），
      它是浮层建议列表而非表单控件，改动收益低、风险高，留待单独一轮。

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

/** 快速模板下拉的当前值。旧版是原生 select 直接 @change，这里保留同样的「选完不复位」行为。 */
const presetPick = ref('')

onMounted(() => { loadAiPresets() })

function onPresetChange(v) {
  if (v) applyAiPreset(v)
}

/**
 * 数字项：原生 `<input type="number" v-model>` 会自动转成数字，而 el-input 的
 * v-model 给的是字符串。配置项下游按数字用（超时、温度…），所以在这里显式收敛一次。
 */
function castNum(key) {
  const v = ai[key]
  if (typeof v === 'string' && v.trim() !== '') ai[key] = Number(v)
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
    // el-input 的 ref 是**组件实例**，不是 DOM 节点；它暴露的 `ref` 才是内部原生 input。
    // 拿实例去调 getBoundingClientRect 会直接抛错（渲染期抛错会整棵子树卸载）。
    const c = modelInput.value
    const inputEl = c && c.ref ? c.ref : c
    const panelEl = suggestPanel.value
    if (!inputEl || typeof inputEl.getBoundingClientRect !== 'function' || !panelEl) return
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
  <div id="aimodal" class="aimodal-host" :class="{ hidden: !props.visible }">
    <!-- 壳也换成 el-dialog（与其它 5 个弹窗一致）。外层常驻 div 只承载稳定 id 与
         hidden 语义：el-dialog 首开前不渲染内容体，而用例要在打开前断言
         #aimodal 存在且带 hidden（注意壳的盒子恒为 0 高，见 KbModal.vue 说明）。
         点遮罩 / ESC 关闭走 el-dialog 自己的逻辑，旧版 @click.self 不再需要。 -->
    <el-dialog
      class="aimodal-dialog"
      :model-value="props.visible"
      width="min(900px, 96vw)"
      top="4vh"
      :show-close="false"
      @update:model-value="emit('close')"
      @close="emit('close')"
    >
      <template #header>
        <div class="modal-head">
          <h3>大模型配置</h3>
          <el-button class="close" text @click="emit('close')">×</el-button>
        </div>
      </template>

      <div class="aimodal-body">
        <section class="settings-group">
          <div class="switch">
            <el-switch id="cfg-ai-mock" v-model="ai.mock" />
            <span class="switch-label">Mock 模式（不调用真实 API）</span>
          </div>

          <label class="field">
            <span>快速模板</span>
            <el-select id="cfg-ai-preset" v-model="presetPick" clearable
                       placeholder="— 选择模板自动填充下方字段 —" @change="onPresetChange">
              <el-option v-for="p in presets" :key="p.id" :label="p.label" :value="p.id" />
            </el-select>
            <small class="hint">模板只是填一遍默认值，填完每一项都还能改。</small>
          </label>

          <div class="field-row">
            <label class="field">
              <span>接入协议</span>
              <el-select id="cfg-ai-provider" v-model="ai.provider">
                <el-option label="Anthropic Messages" value="anthropic" data-value="anthropic" />
                <el-option label="OpenAI Chat Completions" value="openai" data-value="openai" />
                <el-option label="自定义（自建/异构网关）" value="custom" data-value="custom" />
              </el-select>
            </label>
            <label class="field">
              <span>鉴权方式</span>
              <!-- data-value 是给界面用例的钩子：el-select 不把 value 渲染到 DOM，
                   按 value 选项只能靠它（见 tests/ui_select.py）。 -->
              <el-select id="cfg-ai-auth" v-model="ai.auth_style">
                <el-option label="Authorization: Bearer" value="bearer" data-value="bearer" />
                <el-option label="x-api-key" value="x-api-key" data-value="x-api-key" />
                <el-option label="自定义请求头（header）" value="header" data-value="header" />
                <el-option label="URL query 参数" value="query" data-value="query" />
                <el-option label="不带凭据（none）" value="none" data-value="none" />
              </el-select>
            </label>
          </div>

          <label class="field">
            <span>Base URL</span>
            <el-input id="cfg-ai-base" v-model="ai.base_url" autocomplete="off"
                      placeholder="https://你的中转站.com/v1" />
            <small class="hint" id="cfg-ai-base-hint">{{ providerHint || '留空则用官方地址；中转站/自建网关请填到这里' }}</small>
          </label>

          <div class="field-row">
            <label class="field">
              <span>Chat 路径</span>
              <el-input id="cfg-ai-endpoint" v-model="ai.endpoint" :placeholder="endpointPh" />
            </label>
            <label class="field">
              <span>模型列表路径</span>
              <el-input id="cfg-ai-models-path" v-model="ai.models_path" :placeholder="modelsPathPh" />
            </label>
          </div>

          <!-- 鉴权字段名只在「自定义请求头 / URL query」时需要；旧版用 toggle('hidden') -->
          <label class="field" id="ai-auth-header-field" :class="{ hidden: !authHeaderNeeded }">
            <span>鉴权字段名</span>
            <el-input id="cfg-ai-auth-header" v-model="ai.auth_header" autocomplete="off"
                      placeholder="X-Auth-Token / api-key" />
            <small class="hint">仅在「自定义请求头」或「URL query 参数」时需要</small>
          </label>

          <label class="field">
            <span>模型名称</span>
            <span class="combo">
              <el-input id="cfg-ai-model" ref="modelInput" v-model="ai.model" autocomplete="off"
                        placeholder="任意该网关支持的模型名"
                        @focus="refreshSuggest" @input="refreshSuggest" @click="refreshSuggest" />
              <el-button id="btn-ai-models" :loading="!!testing" @click="fetchAiModels">拉取列表</el-button>
            </span>
            <small class="hint" id="cfg-ai-model-hint">{{ modelHint }}</small>
          </label>

          <label class="field">
            <span>API Key</span>
            <span class="combo">
              <el-input id="cfg-ai-key" v-model="ai.api_key" type="password"
                        autocomplete="off" :placeholder="keyPlaceholder" />
              <el-button @click="clearAiKey">清除</el-button>
            </span>
            <small class="hint" id="cfg-ai-key-hint"></small>
          </label>

          <div class="field-row n4">
            <label class="field">
              <span>温度</span>
              <el-input id="cfg-ai-temp" v-model="ai.temperature" type="number"
                        step="0.1" min="0" max="2" placeholder="1" @change="castNum('temperature')" />
            </label>
            <label class="field">
              <span>最大输出</span>
              <el-input id="cfg-ai-max-tokens" v-model="ai.max_tokens" type="number"
                        min="1" placeholder="4000" @change="castNum('max_tokens')" />
            </label>
            <label class="field">
              <span>top_p</span>
              <el-input id="cfg-ai-top-p" v-model="ai.top_p" type="number"
                        step="0.05" min="0" max="1" placeholder="留空不传" @change="castNum('top_p')" />
            </label>
            <label class="field">
              <span>超时(秒)</span>
              <el-input id="cfg-ai-timeout" v-model="ai.timeout" type="number"
                        min="5" placeholder="120" @change="castNum('timeout')" />
            </label>
          </div>

          <el-collapse class="adv">
            <el-collapse-item name="adv" title="高级：网关私有参数与响应适配">
              <div class="adv-body">
                <label class="field">
                  <span>响应取值路径</span>
                  <el-input id="cfg-ai-content-path" v-model="ai.content_path" autocomplete="off"
                            :placeholder="contentPathPh" />
                  <small class="hint">点号分隔，数字表示数组下标。网关返回结构不同就改这里，例如 <code>data.reply</code></small>
                </label>
                <label class="field">
                  <span>max_tokens 字段名</span>
                  <el-input id="cfg-ai-max-tokens-field" v-model="ai.max_tokens_field"
                            autocomplete="off" placeholder="max_tokens" />
                  <small class="hint">部分新网关要求 <code>max_completion_tokens</code></small>
                </label>
                <label class="field">
                  <span>system 角色名</span>
                  <el-input id="cfg-ai-system-role" v-model="ai.system_role"
                            autocomplete="off" placeholder="system" />
                  <small class="hint">有的网关把 system 改成了 developer</small>
                </label>
                <label class="field">
                  <span>HTTP 代理</span>
                  <el-input id="cfg-ai-proxy" v-model="ai.proxy" autocomplete="off"
                            placeholder="http://127.0.0.1:7890" />
                </label>
                <label class="field">
                  <span>额外请求头（JSON 或每行 <code>名称: 值</code>）</span>
                  <el-input id="cfg-ai-headers" v-model="ai.extra_headers" type="textarea" :rows="3"
                            spellcheck="false" placeholder='{"X-Tenant": "demo-tenant"}' />
                </label>
                <label class="field">
                  <span>额外请求体（JSON，会合并进请求）</span>
                  <el-input id="cfg-ai-body" v-model="ai.extra_body" type="textarea" :rows="3"
                            spellcheck="false" placeholder='{"enable_thinking": false}' />
                </label>
                <div class="switch">
                  <el-switch id="cfg-ai-json-mode" v-model="ai.json_mode" />
                  <span class="switch-label">请求 JSON 输出模式（response_format）</span>
                </div>
                <div class="switch">
                  <el-switch id="cfg-ai-verify-ssl" v-model="ai.verify_ssl" />
                  <span class="switch-label">校验 TLS 证书（内网自签证书可关）</span>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </section>
        <p class="settings-note">「保存配置」会连同其他设置一起保存到本地 <code>ui_settings.json</code>（密钥不会进 git）。</p>
      </div>

      <!-- 测试结果：始终在 DOM 里，只切 hidden（旧版契约） -->
      <div class="ai-test-result" id="ai-test-result"
           :class="[aiTest.cls, { hidden: !aiTest.visible }]"
           style="margin: 0 20px 10px;" v-html="aiTest.html"></div>

      <template #footer>
        <div class="aimodal-foot">
          <el-button id="btn-ai-test" :loading="!!testing" @click="testAi(false)">测试连接</el-button>
          <el-button id="btn-ai-dry" :loading="!!testing" @click="testAi(true)">只校验配置</el-button>
          <el-button id="btn-save-ai" type="primary" :loading="saving" @click="saveSettings">保存配置</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 模型名建议浮层（position: fixed，等价旧版挂在 body 上的 .cs-panel）。
         **留在 el-dialog 外面**：fixed 定位会被带 transform 的祖先劫持成相对定位。 -->
    <div class="cs-panel" ref="suggestPanel" :class="{ open: suggestOpen }"
         :style="suggestStyle">
      <div class="cs-option" v-for="m in suggestItems" :key="m"
           :class="{ selected: m === ai.model }" @click="pickModel(m)">{{ m }}</div>
    </div>
  </div>
</template>

<style scoped>
/* 旧版 .modal-box 是「flex 列 + 最大 92vh + 内部滚动」，el-dialog 默认不是这种
   结构，这里把它补回来：头部/底部固定，只有 body 滚。 */
.aimodal-dialog :deep(.el-dialog) {
  display: flex;
  flex-direction: column;
  max-height: 92vh;
}
.aimodal-dialog :deep(.el-dialog__header),
.aimodal-dialog :deep(.el-dialog__footer) {
  flex: none;
}
.aimodal-dialog :deep(.el-dialog__body) {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-top: 6px;
}
/* 底部留白交给 .aimodal-foot 自己（旧版有 border-top + padding） */
.aimodal-dialog :deep(.el-dialog__footer) {
  padding: 0;
}
</style>
