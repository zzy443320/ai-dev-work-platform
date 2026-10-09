<script setup>
// ⌘K 命令面板。导航 / 操作 / 最近工单 / 外观收进一个输入框，输入即过滤。
//
// 为什么值得加：这个界面有 9 个模块 + 运行/试运行/刷新/折叠日志/换主题/换风格/换布局
// 十几类动作，全靠在顶栏和面板头里翻按钮。而审批场景下想做的动作很固定
// （「跑一次」「看这份工单」「切到统计」），一个模糊搜索框比九宫格导航快得多。
//
// 切页签走 window.switchTab —— 那是 App.vue 明确保留的兼容接缝（tests/ 下十几个界面
// 用例也调它）。面板不另起一套切页逻辑，否则会出现「面板切的页签用例看不见」。
import { computed, nextTick, ref, watch } from 'vue'
import { usePalette } from '../composables/usePalette.js'
import { useTheme } from '../composables/useTheme.js'
import { useLayout } from '../composables/useLayout.js'
import { useDefect } from '../composables/useDefect.js'
import { useProposalModal } from '../composables/useProposalModal.js'
import { useFold } from '../composables/useFold.js'
import { useConfigDrawer } from '../composables/useConfigDrawer.js'
import { TABS, ICON_PATHS } from './tabs.js'
import { proposalTitle } from '../utils/labels.js'

const { open, hide } = usePalette()
const { theme, style, styles, toggle: toggleTheme, setStyle } = useTheme()
const { mode: layoutMode, setMode: setLayoutMode, layouts } = useLayout()
const { proposals, running, start, loadProposals } = useDefect()
const { selectInline } = useProposalModal()
const { isCollapsed, toggleFold } = useFold()
const { open: configOpen, toggle: toggleConfig } = useConfigDrawer()

const q = ref('')
const cursor = ref(0)
const inputEl = ref(null)

/** 动作类命令的图标不在 tabs.js 那张表里，补两个，免得为图标引整个图标库 */
const EXTRA_ICONS = {
  bolt: '<path d="M13 2 4.5 13H11l-1 9 8.5-11H12l1-9Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>',
}
function iconFor(key) { return EXTRA_ICONS[key] || ICON_PATHS[key] || ICON_PATHS.settings }

/** 归一化匹配：小写 + 去掉空格与连字符，让「apidebug」「api debug」「API-debug」都命中 */
function norm(s) { return String(s ?? '').toLowerCase().replace(/[\s_-]+/g, '') }

const COMMANDS = computed(() => {
  const acts = [
    { group: '操作', label: '运行完整流水线', icon: 'wrench', keys: ['run', '运行', '流水线'],
      hint: '产出提案 · 不写仓库', disabled: running.value, run: () => start('run') },
    { group: '操作', label: '试运行（只拉取 + AI 定位）', icon: 'bolt', keys: ['probe', 'dry', '试运行'],
      hint: '不生成提案', disabled: running.value, run: () => start('probe') },
    { group: '操作', label: '刷新提案 / 统计 / 知识库', icon: 'check', keys: ['refresh', '刷新'],
      run: () => loadProposals() },
    { group: '操作', label: isCollapsed('run-console') ? '展开运行日志' : '收起运行日志',
      icon: 'code', keys: ['log', 'console', '日志', '控制台'], hint: 'Ctrl `',
      run: () => toggleFold('run-console') },
  ]
  const nav = TABS.map((t, i) => ({
    group: '导航', label: t.label, icon: t.icon, keys: [t.label, t.name, String(i + 1)],
    hint: String(i + 1), disabled: false,
    run: () => window.switchTab && window.switchTab(t.name),
  }))
  const recent = (proposals.value || []).slice(0, 8).map((p) => ({
    group: '最近工单', label: `${p.defect_id || p.id} ${proposalTitle(p)}`, icon: 'wrench',
    keys: [p.defect_id, p.id, p.title, p.category, p.status],
    hint: p.status === 'pending' ? '待审批' : '', disabled: false,
    run: () => {
      if (window.switchTab) window.switchTab('defect')
      selectInline(p.id || p.defect_id)
    },
  }))
  const look = [
    { group: '外观', label: theme.value === 'dark' ? '切换到浅色' : '切换到深色',
      icon: 'sun', keys: ['theme', 'dark', 'light', '主题', '深色', '浅色'],
      hint: '', disabled: false, run: () => toggleTheme() },
    { group: '外观', label: configOpen.value ? '收起配置抽屉' : '打开配置抽屉',
      icon: 'settings', keys: ['config', 'drawer', '配置'], hint: '', disabled: false,
      run: () => toggleConfig() },
    ...layouts.map((l) => ({
      group: '外观', label: `布局：${l.label}`, icon: 'grid', hint: '',
      keys: ['layout', l.key, l.label], disabled: layoutMode.value === l.key,
      run: () => setLayoutMode(l.key),
    })),
    ...styles.map((s) => ({
      group: '外观', label: `风格：${s.label}`, icon: 'image', hint: '',
      keys: ['style', s.key, s.label], disabled: style.value === s.key,
      run: () => setStyle(s.key),
    })),
  ]
  // 动作在前：⌘K 之后按回车最常落在「运行」上
  return [...acts, ...nav, ...recent, ...look]
})

const GROUP_ORDER = ['操作', '导航', '最近工单', '外观']

/**
 * 摊平成「分组标题 + 命令」的渲染列表。游标是**扁平下标**，
 * 但只在命令项之间跳（分组标题不可选中）——跨组连续走比「组内游标」符合直觉。
 */
const ROWS = computed(() => {
  const needle = norm(q.value)
  const hit = needle
    ? COMMANDS.value.filter((c) => c.keys.some((k) => norm(k).includes(needle)))
    : COMMANDS.value
  const flat = []
  for (const g of GROUP_ORDER) {
    const items = hit.filter((c) => c.group === g)
    if (!items.length) continue
    flat.push({ header: g })
    for (const it of items) flat.push(it)
  }
  return flat
})
const selectableIdx = computed(() =>
  ROWS.value.map((r, i) => (r.header ? -1 : i)).filter((i) => i >= 0))

function firstSelectable() { return selectableIdx.value[0] ?? 0 }
watch(ROWS, () => {
  if (!selectableIdx.value.includes(cursor.value)) cursor.value = firstSelectable()
})

function move(delta) {
  const idxs = selectableIdx.value
  if (!idxs.length) return
  const at = idxs.indexOf(cursor.value)
  cursor.value = idxs[((at < 0 ? 0 : at) + delta + idxs.length) % idxs.length]
}

function runAt(i) {
  const cmd = ROWS.value[i]
  if (!cmd || cmd.header || cmd.disabled) return
  cmd.run()
  hide()
}

function onKey(e) {
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1) }
  else if (e.key === 'Enter') { e.preventDefault(); runAt(cursor.value) }
  else if (e.key === 'Escape') { e.preventDefault(); hide() }
}

// 打开时重置查询并把焦点交给输入框：不聚焦的话 ⌘K 之后打字会落进页面上的旧焦点里
watch(open, async (v) => {
  if (!v) return
  q.value = ''
  cursor.value = firstSelectable()
  await nextTick()
  inputEl.value?.focus?.()
})
</script>

<template>
  <div class="palette-root" id="palette" :class="{ hidden: !open }">
    <div class="pl-scrim" id="palette-scrim" @click="hide()" />
    <div class="pl-box" role="dialog" aria-modal="true" aria-label="命令面板">
      <div class="pl-input">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.2-3.2"/></svg>
        <input ref="inputEl" id="palette-input" v-model="q" placeholder="搜索模块、动作、工单…"
               autocomplete="off" spellcheck="false" @keydown="onKey">
        <span class="kbd">Esc</span>
      </div>

      <div class="pl-list" id="palette-list">
        <div v-if="!selectableIdx.length" class="pl-none">没有匹配的命令</div>
        <template v-for="(row, i) in ROWS" :key="i">
          <div v-if="row.header" class="pl-group">{{ row.header }}</div>
          <button v-else type="button" class="pl-row" :class="{ on: i === cursor, off: row.disabled }"
                  @click="runAt(i)" @mouseenter="cursor = i">
            <span class="pl-ico">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                   stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
                   v-html="iconFor(row.icon)" />
            </span>
            <span class="pl-label">{{ row.label }}</span>
            <span class="pl-hint" v-if="row.hint">{{ row.hint }}</span>
          </button>
        </template>
      </div>

      <div class="pl-foot">
        <span><span class="kbd">↑</span><span class="kbd">↓</span> 选择</span>
        <span><span class="kbd">↵</span> 执行</span>
        <span><span class="kbd">Esc</span> 关闭</span>
        <span class="pl-brand">ONES 前端研发助手</span>
      </div>
    </div>
  </div>
</template>
