<script setup>
// 任务型模块的右侧**参数抽屉**（外壳）。六个模块共用这一份结构，内容各自用插槽填。
//
// 形态照配置抽屉（.col-side）来：固定右侧浮层 + 遮罩 + 自身内滚，
// 但显隐走项目的 `.hidden`（display:none）契约而不是 visibility + transform ——
// 理由和问答页两个浮层一样：① 界面用例读的是「有没有 .hidden」；
// ② display:none 让关着的抽屉完全不参与可访问性树，不会把 Tab 序拖进一个看不见的表单；
// ③ 打开时的入场动画靠 CSS animation 天然重播（display:none → 显示会重新触发）。
//
// 挂在各模块 pane **内部**而不是外壳里：el-tab-pane 切走时本身是 display:none，
// 抽屉和遮罩跟着一起消失，不会出现「切到别的页签、上一个模块的遮罩还盖着」。
//
// 关闭路径有三条：遮罩点击、右上角收起按钮、Esc（Esc 统一绑在 App.vue 外壳）。
// 提交动作放抽屉底部 footer 插槽，并且**由调用方在提交时关掉抽屉**
// （见各模块的 runTask 包装）—— 因为点「运行」之后要看的是过程与产物。
import { computed } from 'vue'
import { useModuleParams } from '../composables/useModuleParams.js'

const props = defineProps({
  /** 抽屉 key：多数模块就是页签 name；一个页签里多个抽屉时用 `页签名:槽位`
      （扩展能力是 extensions:skill / extensions:mcp），同页签内互斥 */
  name: { type: String, required: true },
  title: { type: String, required: true },
  /** 标题下的副说明，一句话说清这里填的是什么 */
  hint: { type: String, default: '' },
  /** 任务型模块默认打开（进来就是要填参数）；列表管理型保持关着，点新增才开 */
  defaultOpen: { type: Boolean, default: true },
})

const { register, isOpen, close } = useModuleParams()
// setup 里注册一次：状态是全局单例，注册即声明「这个 key 存在 + 它默认开不开」
register(props.name, props.defaultOpen)
const opened = computed(() => isOpen(props.name))
// id 里不能出现 `:`（extensions:skill 这种 key）—— querySelector / Playwright
// 会把它当伪类解析。key 保留冒号，落到 id 上统一换成连字符。
const slug = computed(() => props.name.replace(/:/g, '-'))
const did = computed(() => `mod-drawer-${slug.value}`)
const mid = computed(() => `mod-mask-${slug.value}`)
const cid = computed(() => `mod-${slug.value}-close`)
</script>

<template>
  <div class="mod-mask" :id="mid" :class="{ hidden: !opened }" @click="close(name)" />

  <aside class="mod-drawer" :id="did" :class="{ hidden: !opened }"
         role="dialog" :aria-label="`${title}（参数）`">
    <div class="mod-drawer-head">
      <div class="mod-drawer-title">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 6h16M4 12h10M4 18h13" /><circle cx="18" cy="12" r="2.2" /><circle cx="15" cy="18" r="2.2" />
        </svg>
        <span>{{ title }}</span>
      </div>
      <button type="button" class="mod-drawer-close" :id="cid"
              :title="`收起${title}参数`" @click="close(name)">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>
    <div v-if="hint" class="mod-drawer-hint">{{ hint }}</div>

    <div class="mod-drawer-body">
      <slot />
    </div>

    <div class="mod-drawer-foot">
      <slot name="footer" />
    </div>
  </aside>
</template>
