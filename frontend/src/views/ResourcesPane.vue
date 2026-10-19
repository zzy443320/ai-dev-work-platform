<script setup>
// 资源汇总页签（#pane-resources）：需求文档链接、各环境地址、常用入口的便签板。
//
// 这个模块**不接 AI、不碰仓库**：它是「东西都放哪」的那一页。因此没有参数条
// （/api/params/schema 里也就没有它），只有一份可以随时增删的列表。
//
// 两种状态，共用同一份 DOM 结构（不靠 v-if 整块替换，编辑态切回来不会丢滚动位置）：
//   - 浏览态：每条是一个 <a target="_blank" rel="noopener noreferrer">，点一下就新开页签；
//   - 编辑态：每条展开成「名称 / 地址 / 备注」三格，改完失焦即回存（没有保存按钮）。
//
// 地址的合法性由后端兜底（scripts/resource_board.py 只放行 http/https，
// 因为 javascript: 这类地址写在 href 里是真能执行的）；这里只把「看起来像地址」
// 的东西补成真地址（test.example.com → http://test.example.com）并提示错在哪。
import { computed, onMounted, ref } from 'vue'
import { useResources } from '../composables/useResources.js'

const {
  board, loading, error, savedLabel, counts, loaded,
  load, flush, addGroup, removeGroup, addItem, removeItem, commitUrl,
  normalizeUrl, prettyUrl,
} = useResources()

const editing = ref(false)
const keyword = ref('')
const copiedId = ref('')

/** 空板时的建议分组：点一下就有分组，省掉「先想叫什么」这一步 */
const SUGGESTIONS = ['需求文档', '环境地址', '接口文档', '设计稿', '监控 / 日志']

const k = computed(() => keyword.value.trim().toLowerCase())

function matchItem(it) {
  if (!k.value) return true
  return `${it.label} ${it.url} ${it.note}`.toLowerCase().includes(k.value)
}

/** 过滤只在浏览态生效：编辑时藏起一半条目，人会以为自己的数据丢了 */
function itemsOf(g) {
  const items = g.items || []
  return editing.value ? items : items.filter(matchItem)
}

function groupVisible(g) {
  if (editing.value || !k.value) return true
  return (g.label || '').toLowerCase().includes(k.value) || (g.items || []).some(matchItem)
}

const anyVisible = computed(() => board.value.groups.some(groupVisible))
const isEmpty = computed(() => !board.value.groups.length)

/** 地址填了但不合法（只支持 http/https）：就地标红，别等保存后才发现被丢了 */
function urlBad(it) {
  return !!String(it.url || '').trim() && !normalizeUrl(it.url)
}

function toggleEdit() {
  editing.value = !editing.value
  if (!editing.value) flush()
}

function addGroupWith(label) {
  addGroup(label)
  editing.value = true
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
        <el-button id="btn-res-edit" size="small" :type="editing ? 'primary' : ''" @click="toggleEdit">
          {{ editing ? '完成' : '编辑' }}
        </el-button>
        <el-button id="btn-res-add-group" size="small" @click="addGroupWith('新分组')">新增分组</el-button>
      </div>
    </div>

    <div class="res-meta">
      <span class="res-count-text">{{ counts.groups }} 个分组 · {{ counts.items }} 条链接</span>
      <span class="res-state" :class="{ bad: !!error }">{{ error || savedLabel }}</span>
      <span class="res-tip">点击条目在新页签打开</span>
    </div>

    <!-- 读不到板子：说清是「读失败」而不是「还没有内容」——后者会让人以为数据丢了 -->
    <div v-if="error && !loaded" class="res-empty" id="res-error">
      <p class="res-empty-title">没能读到资源板</p>
      <p class="res-empty-hint">{{ error }}<br>确认本机服务在跑，然后刷新页面重试。</p>
    </div>

    <!-- 空板引导：给几个现成的分组名，省掉「先想叫什么」那一步 -->
    <div v-else-if="isEmpty && !loading" class="res-empty" id="res-empty">
      <p class="res-empty-title">还没有收录任何入口</p>
      <p class="res-empty-hint">
        把团队常用的需求文档、设计稿、各环境（开发 / 测试 / 预发 / 生产）地址放进来，
        以后从这里点开，不用再翻聊天记录。
      </p>
      <div class="res-chips">
        <button v-for="s in SUGGESTIONS" :key="s" type="button" class="res-chip"
                @click="addGroupWith(s)">+ {{ s }}</button>
      </div>
    </div>

    <div v-else class="res-grid">
      <template v-for="g in board.groups" :key="g.id">
        <section v-if="groupVisible(g)" class="res-group" :data-group="g.id">
          <header class="res-group-head">
            <el-input v-if="editing" v-model="g.label" size="small" class="res-group-input"
                      placeholder="分组名，例如：环境地址" @change="flush" />
            <h3 v-else class="res-group-title">{{ g.label }}</h3>
            <span class="res-group-count">{{ (g.items || []).length }}</span>
            <button v-if="editing" type="button" class="res-del" title="删除这个分组"
                    @click="onRemoveGroup(g)">删除分组</button>
          </header>

          <ul class="res-list">
            <li v-for="it in itemsOf(g)" :key="it.id" class="res-item">
              <!-- 浏览态：整行可点，新页签打开（rel 不能省：外部页面拿到 window.opener 能反向导航） -->
              <div v-if="!editing" class="res-row">
                <a v-if="it.url" class="res-link" :href="it.url" :data-url="it.url"
                   target="_blank" rel="noopener noreferrer" :title="it.url">
                  <span class="res-label">{{ it.label }}</span>
                  <span class="res-open">新页签 ↗</span>
                  <span class="res-url mono">{{ prettyUrl(it.url) }}</span>
                  <span v-if="it.note" class="res-note">{{ it.note }}</span>
                </a>
                <span v-else class="res-link is-blank" :title="'这条还没有填地址'">
                  <span class="res-label">{{ it.label }}</span>
                  <span class="res-open">未填链接</span>
                  <span class="res-url mono">点右上「编辑」补上地址后就能打开</span>
                </span>
                <button type="button" class="res-copy" :class="{ done: copiedId === it.id }"
                        :disabled="!it.url" title="复制链接" @click="copyUrl(it)">
                  {{ copiedId === it.id ? '已复制' : '复制' }}
                </button>
              </div>

              <!-- 编辑态：改完失焦即回存（@change 在失焦 / 回车时触发） -->
              <div v-else class="res-edit" :class="{ bad: urlBad(it) }">
                <el-input v-model="it.label" size="small" placeholder="名称，例如：订单需求 PRD"
                          @change="flush" />
                <el-input v-model="it.url" size="small" class="res-url-input"
                          placeholder="地址，例如：test.example.com/order"
                          @change="commitUrl(it)" />
                <el-input v-model="it.note" size="small" placeholder="备注（可选）：谁能看、什么时候用"
                          @change="flush" />
                <div class="res-edit-foot">
                  <span v-if="urlBad(it)" class="res-bad">只支持 http / https 地址</span>
                  <button type="button" class="res-del" @click="onRemoveItem(g, it)">删除这条</button>
                </div>
              </div>
            </li>
          </ul>

          <!-- 空分组：浏览态给一句说明，编辑态给「添加链接」（新增空行不落盘，
               等它被填进内容后随失焦一起回存，免得板上攒一堆空行） -->
          <p v-if="!editing && !(g.items || []).length" class="res-blank-group">
            这个分组还是空的 —— 点右上「编辑」再加条目。
          </p>
          <button v-if="editing" type="button" class="res-add" @click="addItem(g.id)">
            + 添加链接
          </button>
        </section>
      </template>
    </div>

    <p v-if="keyword && !anyVisible" class="res-nomatch" id="res-nomatch">
      没有匹配「{{ keyword }}」的链接。
    </p>
  </section>
</template>
