<script setup>
// 资源汇总页签（#pane-resources）：需求文档链接、各环境地址、常用入口的便签板。
//
// 这个模块**不接 AI、不碰仓库**：它是「东西都放哪」的那一页。因此没有参数条
// （/api/params/schema 里也就没有它），只有一份可以随时增删的列表。
//
// 交互（2026-10-20 改版）：
//   - 浏览态就是唯一的常态：每条是一整行可点的 <a target="_blank">，新页签打开；
//   - 增 / 改都走**弹窗**（新增分组、添加或编辑链接），并排的输入框不再挤在卡片里；
//   - 卡片与条目上的操作按钮常驻但压低对比度，悬停才提亮 —— 不藏起来（藏起来
//     的功能等于没有），也不抢链接本身的点击热区。
//
// 地址的合法性由后端兜底（scripts/resource_board.py 只放行 http/https，
// 因为 javascript: 这类地址写在 href 里是真能执行的）；弹窗里只做「当场看得见」
// 的提示，并在保存前把「看起来像地址」的东西补成真地址。
import { computed, onMounted, reactive, ref } from 'vue'
import { useResources } from '../composables/useResources.js'

const {
  board, loading, error, savedLabel, counts, loaded,
  load, flush, addGroup, removeGroup, addItem, removeItem,
  normalizeUrl, prettyUrl,
} = useResources()

const keyword = ref('')
const copiedId = ref('')

/** 空板时的建议分组：点一下就有分组，省掉「先想叫什么」这一步 */
const SUGGESTIONS = ['需求文档', '环境地址', '接口文档', '设计稿', '监控 / 日志']

const k = computed(() => keyword.value.trim().toLowerCase())

function matchItem(it) {
  if (!k.value) return true
  return `${it.label} ${it.url} ${it.note}`.toLowerCase().includes(k.value)
}

function itemsOf(g) {
  return (g.items || []).filter(matchItem)
}

function groupVisible(g) {
  if (!k.value) return true
  return (g.label || '').toLowerCase().includes(k.value) || (g.items || []).some(matchItem)
}

const anyVisible = computed(() => board.value.groups.some(groupVisible))
const isEmpty = computed(() => !board.value.groups.length)

// ── 弹窗：分组（新增 / 重命名） ──────────────────────────────────────
const groupDlg = reactive({ open: false, mode: 'add', gid: '', label: '' })

function openAddGroup(prefill = '') {
  Object.assign(groupDlg, { open: true, mode: 'add', gid: '', label: prefill })
}
function openRename(g) {
  Object.assign(groupDlg, { open: true, mode: 'rename', gid: g.id, label: g.label || '' })
}
function saveGroup() {
  const label = groupDlg.label.trim().slice(0, 60)
  if (!label) return                       // 名字是分组的全部内容，空着不给存
  if (groupDlg.mode === 'add') addGroup(label)
  else {
    const g = board.value.groups.find((x) => x.id === groupDlg.gid)
    if (g) g.label = label
  }
  groupDlg.open = false
  flush()
}

// ── 弹窗：链接（新增 / 编辑） ────────────────────────────────────────
const itemDlg = reactive({
  open: false, mode: 'add', gid: '', iid: '',
  label: '', url: '', note: '', err: '',
})

const dlgGroup = computed(() => board.value.groups.find((g) => g.id === itemDlg.gid))

function openAddItem(g) {
  Object.assign(itemDlg, {
    open: true, mode: 'add', gid: g.id, iid: '',
    label: '', url: '', note: '', err: '',
  })
}
function openEditItem(g, it) {
  Object.assign(itemDlg, {
    open: true, mode: 'edit', gid: g.id, iid: it.id,
    label: it.label || '', url: it.url || '', note: it.note || '', err: '',
  })
}

/** 地址只在「填了但不合法」时报错；空着允许 —— 先占个名字、地址回头补是常态 */
function checkUrl() {
  const raw = String(itemDlg.url || '').trim()
  if (!raw) return true
  if (normalizeUrl(raw)) return true
  itemDlg.err = '只支持 http / https 地址'
  return false
}

function saveItem() {
  itemDlg.err = ''
  if (!checkUrl()) return
  const label = itemDlg.label.trim().slice(0, 60)
  const note = itemDlg.note.trim().slice(0, 240)
  const url = normalizeUrl(itemDlg.url) || ''
  if (!label && !url && !note) {
    itemDlg.err = '名称和地址至少填一个'
    return
  }
  if (itemDlg.mode === 'add') {
    addItem(itemDlg.gid, { label, url, note })
  } else {
    const g = board.value.groups.find((x) => x.id === itemDlg.gid)
    const it = g && (g.items || []).find((x) => x.id === itemDlg.iid)
    if (it) Object.assign(it, { label, url, note })
  }
  itemDlg.open = false
  flush()
}

function onRemoveGroup(g) {
  const n = (g.items || []).length
  if (n && !window.confirm(`删除分组「${g.label}」？\n里面的 ${n} 条链接会一起删掉。`)) return
  removeGroup(g.id)
  flush()
}

function onRemoveItem(g, it) {
  removeItem(g.id, it.id)
  flush()
}

async function copyUrl(it) {
  if (!it.url) return
  try {
    await navigator.clipboard.writeText(it.url)
    copiedId.value = it.id
    window.setTimeout(() => { if (copiedId.value === it.id) copiedId.value = '' }, 1200)
  } catch (e) {
    /* 剪贴板不可用（无权限 / 非安全上下文）：不打扰 —— 链接本身就能点 */
  }
}

// ── 条目头像：取域名的首字符，色相由内容哈希决定（同一地址永远同色）。
// 只把「色相」写进 CSS 变量，饱和度/明度在 resources.css 里按明暗主题各配一份，
// 所以内网地址（拿不到 favicon）也有一枚稳定的视觉锚点，且两种主题都不脏。
function hashHue(s) {
  let h = 0
  const t = String(s || '')
  for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0
  return h % 360
}
function avatar(it) {
  const src = it.url || it.label || ''
  const host = String(src).replace(/^https?:\/\//i, '').split(/[/:]/)[0]
  return {
    char: (host || it.label || '·').charAt(0).toUpperCase(),
    style: { '--av-h': String(hashHue(host || it.label || src)) },
  }
}

onMounted(() => load())
</script>

<template>
  <section class="panel" id="res-panel">
    <div class="panel-head res-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" /><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></svg>
        资源汇总 · 需求文档与环境地址
      </h2>
      <div class="res-tools">
        <el-input
          id="res-filter"
          v-model="keyword"
          class="res-filter"
          size="small"
          clearable
          placeholder="过滤：名称 / 地址 / 备注"
        />
        <el-button id="btn-res-add-group" size="small" type="primary" @click="openAddGroup()">
          ＋ 新增分组
        </el-button>
      </div>
    </div>

    <div class="res-meta">
      <span class="res-count-text">{{ counts.groups }} 个分组 · {{ counts.items }} 条链接</span>
      <span class="res-state" :class="{ bad: !!error }">{{ error || savedLabel }}</span>
      <span class="res-tip">点击条目在新页签打开 · 悬停可编辑 / 复制</span>
    </div>

    <!-- 读不到板子：说清是「读失败」而不是「还没有内容」——后者会让人以为数据丢了 -->
    <div v-if="error && !loaded" class="res-empty" id="res-error">
      <div class="res-empty-icon">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      </div>
      <p class="res-empty-title">没能读到资源板</p>
      <p class="res-empty-hint">{{ error }}<br>确认本机服务在跑，然后刷新页面重试。</p>
    </div>

    <!-- 空板引导：给几个现成的分组名，省掉「先想叫什么」那一步 -->
    <div v-else-if="isEmpty && !loading" class="res-empty" id="res-empty">
      <div class="res-empty-icon">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>
      </div>
      <p class="res-empty-title">还没有收录任何入口</p>
      <p class="res-empty-hint">
        把团队常用的需求文档、设计稿、各环境（开发 / 测试 / 预发 / 生产）地址放进来，
        以后从这里点开，不用再翻聊天记录。
      </p>
      <div class="res-chips">
        <button v-for="s in SUGGESTIONS" :key="s" type="button" class="res-chip"
                @click="openAddGroup(s)">+ {{ s }}</button>
      </div>
    </div>

    <div v-else class="res-grid">
      <template v-for="g in board.groups" :key="g.id">
        <section v-if="groupVisible(g)" class="res-group" :data-group="g.id">
          <header class="res-group-head">
            <h3 class="res-group-title">{{ g.label }}</h3>
            <span class="res-group-count">{{ (g.items || []).length }}</span>
            <div class="res-group-actions">
              <button type="button" class="res-icon-btn res-add-link" title="添加链接"
                      @click="openAddItem(g)">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              </button>
              <button type="button" class="res-icon-btn res-rename" title="重命名分组"
                      @click="openRename(g)">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
              </button>
              <button type="button" class="res-icon-btn res-del" title="删除这个分组"
                      @click="onRemoveGroup(g)">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
              </button>
            </div>
          </header>

          <ul class="res-cards">
            <li v-for="it in itemsOf(g)" :key="it.id" class="res-card" :class="{ 'is-blank': !it.url }">
              <!-- 卡片整体可点，新页签打开（rel 不能省：外部页面拿到 window.opener 能反向导航） -->
              <a v-if="it.url" class="res-link" :href="it.url" :data-url="it.url"
                 target="_blank" rel="noopener noreferrer" :title="it.url">
                <span class="res-avatar" :style="avatar(it).style" aria-hidden="true">{{ avatar(it).char }}</span>
                <span class="res-main">
                  <span class="res-label">{{ it.label }}</span>
                  <span class="res-url mono">{{ prettyUrl(it.url) }}</span>
                  <span v-if="it.note" class="res-note">{{ it.note }}</span>
                </span>
                <span class="res-open" aria-hidden="true">↗</span>
              </a>
              <span v-else class="res-link is-blank" :title="'这条还没有填地址'">
                <span class="res-avatar" :style="avatar(it).style" aria-hidden="true">{{ avatar(it).char }}</span>
                <span class="res-main">
                  <span class="res-label">{{ it.label }}</span>
                  <span class="res-url mono">未填链接</span>
                </span>
              </span>

              <div class="res-item-actions">
                <button type="button" class="res-icon-btn res-copy" :class="{ done: copiedId === it.id }"
                        :disabled="!it.url" title="复制链接" @click="copyUrl(it)">
                  <svg v-if="copiedId !== it.id" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  <svg v-else viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                </button>
                <button type="button" class="res-icon-btn res-item-edit" title="编辑这条"
                        @click="openEditItem(g, it)">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
                </button>
                <button type="button" class="res-icon-btn res-item-del" title="删除这条"
                        @click="onRemoveItem(g, it)">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
            </li>
          </ul>

          <!-- 空分组：一句话 + 一个入口，点了就开「添加链接」弹窗 -->
          <button v-if="!(g.items || []).length" type="button" class="res-add" @click="openAddItem(g)">
            ＋ 添加链接
          </button>
          <p v-else-if="!itemsOf(g).length" class="res-blank-group">
            这个分组里没有匹配「{{ keyword }}」的链接
          </p>
        </section>
      </template>
    </div>

    <p v-if="keyword && !anyVisible" class="res-nomatch" id="res-nomatch">
      没有匹配「{{ keyword }}」的链接。
    </p>

    <!-- ── 新增 / 重命名分组 ─────────────────────────────────────── -->
    <el-dialog
      id="res-group-dialog"
      v-model="groupDlg.open"
      class="res-dialog"
      width="420px"
      append-to-body
      :close-on-click-modal="false"
      destroy-on-close
      align-center
    >
      <template #header>
        <div class="res-dlg-head">
          <h3 class="res-dlg-title">{{ groupDlg.mode === 'add' ? '新增分组' : '重命名分组' }}</h3>
          <p class="res-dlg-sub">分组就是便签本里的一栏，随手起个名就行。</p>
        </div>
      </template>
      <div class="res-form">
        <label class="res-field">
          <span class="res-field-label">分组名</span>
          <el-input
            id="res-group-name"
            v-model="groupDlg.label"
            size="large"
            maxlength="60"
            show-word-limit
            placeholder="例如：环境地址 / 需求文档 / 监控日志"
            @keyup.enter="saveGroup"
          />
        </label>
      </div>
      <template #footer>
        <el-button @click="groupDlg.open = false">取消</el-button>
        <el-button id="res-group-save" type="primary" @click="saveGroup">
          {{ groupDlg.mode === 'add' ? '创建分组' : '保存' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ── 添加 / 编辑链接 ───────────────────────────────────────── -->
    <el-dialog
      id="res-item-dialog"
      v-model="itemDlg.open"
      class="res-dialog"
      width="560px"
      append-to-body
      :close-on-click-modal="false"
      destroy-on-close
      align-center
    >
      <template #header>
        <div class="res-dlg-head">
          <h3 class="res-dlg-title">{{ itemDlg.mode === 'add' ? '添加链接' : '编辑链接' }}</h3>
          <p class="res-dlg-sub">{{ dlgGroup ? dlgGroup.label : '资源条目' }}</p>
        </div>
      </template>
      <div class="res-form">
        <label class="res-field">
          <span class="res-field-label">名称</span>
          <el-input id="res-item-label" v-model="itemDlg.label" size="large" maxlength="60"
                    placeholder="例如：订单需求 PRD" @keyup.enter="saveItem" />
        </label>
        <label class="res-field">
          <span class="res-field-label">地址</span>
          <el-input id="res-item-url" v-model="itemDlg.url" size="large" class="res-url-input"
                    placeholder="test.example.com/order"
                    @keyup.enter="saveItem" @blur="checkUrl" />
          <span class="res-field-hint" :class="{ bad: !!itemDlg.err }">
            {{ itemDlg.err || '内网地址不用写协议，保存时自动补 http://' }}
          </span>
        </label>
        <label class="res-field">
          <span class="res-field-label">备注 <span class="res-opt">可选</span></span>
          <el-input id="res-item-note" v-model="itemDlg.note" size="large" maxlength="240"
                    placeholder="谁能看、什么时候用" @keyup.enter="saveItem" />
        </label>
      </div>
      <template #footer>
        <el-button @click="itemDlg.open = false">取消</el-button>
        <el-button id="res-item-save" type="primary" @click="saveItem">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>
