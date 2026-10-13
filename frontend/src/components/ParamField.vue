<script setup>
// 一条参数的渲染器：给它元数据（web/params_schema.py 里的 field），它出对应控件。
//
// 三条硬契约，动之前先看：
//   1. **id 不变**：控件 id 用 schema 里的 `field.id`（#opt-limit / #team-title /
//      #test-file …），外层容器 id 是 `${field.id}-field`。界面用例按这些 id 点与填
//      （tests/check_vue_tasks_ui.py、tests/check_render_escape.py），换形态不换 id。
//   2. **可见性走 `.hidden` 类**而不是 v-show：既有用例读的是
//      `#req-figma-token-field` 的 classList（取稿通道切到「浏览器打开」时 Token 无意义），
//      项目里 `.hidden` 是统一契约。
//   3. **分段按钮保留 `.seg` / `.seg-btn[data-mode]`**：用例是
//      `page.click('#req-figma-mode .seg-btn[data-mode="browser"]')`。
//
// 控件一律用原生 input/textarea/select/checkbox：这次改版要的是「标签与值排在一行」
// 这种紧凑版式，el-input / el-select 的包裹层会跟版式打架；原生控件 page.fill()
// 一样能测到，观感由 styles/params.css 统一。扩展能力页那套 el-select 不在本次范围。
import { computed, ref } from 'vue'
import { isDefaultValue } from '../composables/useParamSchema.js'

const props = defineProps({
  /** schema 里的一条 field 元数据 */
  field: { type: Object, required: true },
  /** useParamModel() 的返回值：{ get, set, setPart } */
  model: { type: Object, required: true },
  /** strip = 就地控件条里的一格；rail = 参数轨里的一行（标签在上、更宽） */
  variant: { type: String, default: 'strip' },
})

const f = computed(() => props.field)
const value = computed(() => props.model.get(f.value.key))
const wrapperId = computed(() => `${f.value.id}-field`)

/** visible_when：如 { key: 'figmaMode', not: 'browser' } —— 联动条件由元数据声明 */
const visible = computed(() => {
  const vw = f.value.visible_when
  if (!vw) return true
  const cur = props.model.get(vw.key)
  if ('equals' in vw) return cur === vw.equals
  if ('not' in vw) return cur !== vw.not
  return true
})

const empty = computed(() => {
  const v = value.value
  if (typeof v === 'string') return !v.trim()
  if (v && typeof v === 'object') return Object.values(v).every((x) => !x)
  return v === '' || v === null || v === undefined
})
/** 必填但没填：只做视觉提示（黄），不做拦截 —— 「未齐禁跑」这次没选 */
const missing = computed(() => !!f.value.required && empty.value)
const atDefault = computed(() => !f.value.required && isDefaultValue(value.value, f.value.default))

// strip 里的长文本：默认两行截断，点一下才给全高（不弹层、不占住整屏）
const expanded = ref(false)
const isLongText = computed(() => f.value.kind === 'textarea')
const clamped = computed(() => props.variant === 'strip' && !expanded.value)

function write(v) { props.model.set(f.value.key, v) }
function onText(e) { write(e.target.value) }
function onNumber(e) {
  const raw = e.target.value
  write(raw === '' ? '' : Number(raw))
}
function onCheck(e) { write(e.target.checked) }
function onSegment(opt) { write(opt.value) }
function onRolePart(opt, e) { props.model.setPart(f.value.key, opt.key, e.target.checked) }
function restoreDefault() {
  const d = f.value.default
  write(typeof d === 'object' && d !== null ? JSON.parse(JSON.stringify(d)) : d)
}

/** 数字格显示成「2 轮」这种带单位的值；空值回落到占位 */
const numberText = computed(() => (empty.value ? '' : `${value.value}`))

/** select 的 change：原生 select 的 value 本来就是字符串，直接写回（写 Number 会让
    schema 的 default 比对失配，「默认」角标就永远不出现） */
function onSelect(e) { write(e.target.value) }
</script>

<template>
  <div v-if="visible" class="pf" :class="[`pf-${f.kind}`, `pf-${variant}`,
       { 'is-missing': missing, 'is-open': !clamped }]" :id="wrapperId">
    <!-- 开关：标签跟在控件后面，一格一行，不占标签列 -->
    <template v-if="f.kind === 'switch'">
      <label class="pf-switch">
        <input type="checkbox" :id="f.id" :checked="!!value" @change="onCheck">
        <span class="pf-track" aria-hidden="true" />
        <span class="pf-label">{{ f.label }}</span>
      </label>
      <span v-if="f.hint" class="pf-hint">{{ f.hint }}</span>
    </template>

    <!-- 角色组：一个参数四个子开关，子 id 沿用 schema（#team-role-coder …） -->
    <div v-else-if="f.kind === 'switch-group'" class="pf-group">
      <span class="pf-label pf-group-title">{{ f.label }}</span>
      <div class="pf-group-list">
        <label v-for="opt in f.options" :key="opt.key" class="pf-switch"
               :class="{ locked: opt.locked }" :title="opt.note || ''">
          <input type="checkbox" :id="opt.id" :checked="!!(value || {})[opt.key]"
                 :disabled="!!opt.locked" @change="onRolePart(opt, $event)">
          <span class="pf-track" aria-hidden="true" />
          <span class="pf-label">{{ opt.label }}</span>
        </label>
      </div>
      <span v-if="f.hint" class="pf-hint">{{ f.hint }}</span>
    </div>

    <!-- 分段：保留 .seg / .seg-btn[data-mode] 契约 -->
    <div v-else-if="f.kind === 'segment'" class="pf-seg">
      <span class="pf-label">{{ f.label }}</span>
      <div class="seg" :id="f.id">
        <button v-for="opt in f.options" :key="opt.value" type="button" class="seg-btn"
                :class="{ active: value === opt.value }" :data-mode="opt.value"
                @click="onSegment(opt)">{{ opt.label }}</button>
      </div>
      <span v-if="f.hint" class="pf-hint">{{ f.hint }}</span>
    </div>

    <!-- 长文本：strip 里两行截断（点一下给全高），轨里直接按 rows 给高 -->
    <div v-else-if="isLongText" class="pf-textarea">
      <div class="pf-row">
        <span class="pf-label">{{ f.label }}</span>
        <span v-if="f.required" class="pf-req">必填</span>
        <span v-else-if="f.optional" class="pf-opt">可选</span>
        <span v-if="atDefault" class="pf-def">默认</span>
        <span class="pf-flex"></span>
        <button v-if="variant === 'strip'" type="button" class="pf-expand"
                @click="expanded = !expanded">{{ expanded ? '收起' : '展开' }}</button>
        <button v-if="!atDefault && f.default" type="button" class="pf-expand"
                @click="restoreDefault">恢复默认</button>
      </div>
      <textarea :id="f.id" v-bind:value="value" :rows="clamped ? 2 : (f.rows || 4)"
                :placeholder="f.placeholder || ''" :class="{ clamped }"
                @input="onText" @focus="expanded = true"></textarea>
      <span v-if="f.hint" class="pf-hint">{{ f.hint }}</span>
    </div>

    <!-- 短控件：标签在左、值在右，一行一格 -->
    <label v-else class="pf-inline">
      <span class="pf-label">{{ f.label }}</span>
      <template v-if="f.kind === 'select'">
        <select class="pf-ctl" :id="f.id" :value="value" @change="onSelect($event)">
          <option v-for="opt in f.options" :key="opt" :value="opt">{{ opt }}</option>
        </select>
      </template>
      <template v-else-if="f.kind === 'number'">
        <input class="pf-ctl pf-num" :id="f.id" type="number" :value="numberText"
               :min="f.min" :max="f.max" :step="f.step || 1"
               :placeholder="f.placeholder || ''" @input="onNumber" @focus="$event.target.select()">
        <span v-if="f.unit" class="pf-unit">{{ f.unit }}</span>
      </template>
      <template v-else>
        <input class="pf-ctl" :id="f.id" :type="f.kind === 'password' ? 'password' : 'text'"
               :value="value === undefined ? '' : value" :autocomplete="f.autocomplete || 'off'"
               :placeholder="f.placeholder || ''" @input="onText">
      </template>
      <span v-if="f.required" class="pf-req">必填</span>
      <span v-else-if="atDefault" class="pf-def">默认</span>
    </label>
  </div>
</template>
