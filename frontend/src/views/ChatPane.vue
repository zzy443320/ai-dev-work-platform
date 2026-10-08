<script setup>
// 问答页签（#pane-chat）。旧版 index.html:170-233 + app.js:3830-4337 的等价迁移。
//
// 与前一个页签（统计）不同的两处：
//   1. 有长驻副作用（消息流、附件上传），所以逻辑全放 useChat composable，
//      组件只做「事件 → composable 方法」的映射；
//   2. 有 DOM 时序要求：新消息到达要自动滚到底，但「用户在往上翻历史」时不能抢滚动，
//      这一条旧版用 chatNearBottom() 判定，这里原样保留。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useChat } from '../composables/useChat.js'
import { useArtifacts } from '../composables/useArtifacts.js'
import { useChatRails } from '../composables/useChatRails.js'
import ArtifactsPanel from '../components/ArtifactsPanel.vue'
import ChatSessions from '../components/ChatSessions.vue'
import ChatMessage from '../components/ChatMessage.vue'
import ChatPending from '../components/ChatPending.vue'
import ChatAttachBar from '../components/ChatAttachBar.vue'
import { wireKbLinks } from '../utils/format.js'

// 注意：必须解构，不能写成 `const c = useChat()` 再到模板里用 `c.xxx`。
// `<script setup>` 只对**顶层绑定**的 ref 做模板自动解包；`c` 是个普通对象，
// `c.useRepo` 在模板里拿到的是 ref 对象本身，`v-model="c.useRepo"` 会绑错东西
// （表现就是勾选框能点、但 localStorage 永远不写）。这个坑在统计页签没踩到，
// 是因为那里本来就是解构的。
const {
  // 偏好
  useRepo, allowPatch, persistPrefs,
  // 会话
  sessions, sessionsError, current, messages,
  loadSessions, openSession, newSession, clearSession, deleteSession,
  // 发送
  busy, draft, liveTools, status, toolsText,
  send: send0, stop,
  // 附件
  pendingAtts, uploading, removeAtt, uploadFiles, onPaste, onDrop: onDropFiles,
  sendDisabled,
} = useChat()

const textEl = ref(null)
const fileEl = ref(null)
const streamEl = ref(null)
const dropEl = ref(null)
const dragging = ref(false)

// 会话历史侧栏 + 两个浮层（产出物抽屉 / 使用说明浮窗）的开合状态。
// 模块级单例 + localStorage 记忆，见 useChatRails。
const { historyCollapsed, artOpen, helpOpen,
        toggleHistory, toggleArt, toggleHelp, closeFloats } = useChatRails()

// 产出物计数给标题栏按钮上的小角标用（useArtifacts 是单例，与抽屉下面板同一份数据）
// loadArtifacts 也取自这里：问答生成提案后要刷新那份共享列表（见 send()）。
const { sorted: artifactList, badge: artifactBadge, loadArtifacts } = useArtifacts()
const artifactCount = computed(() => artifactList.value.length)

const emit = defineEmits(['open-artifact', 'open-kb-doc'])

/**
 * 吸附式跟随：stick=true 时输出增长自动滚到最新；用户往上翻历史就解除吸附，
 * 翻回底部再恢复。旧版在每次内容变化时用一次性 nearBottom() 判断 —— 模型一次
 * 吐出一大段（>140px）就会误判「用户在翻历史」，之后再也不跟随，表现就是
 * 「大模型输出后不会自动滚到最新」。吸附状态只在用户真实滚动时变化，不受
 * 单次输出体量影响。
 */
const stick = ref(true)
/** 距底部多少像素内算「贴着底」 */
const BOTTOM_EPS = 60

/** 用户滚动 #chat-stream 时更新吸附状态（程序滚到底也会触发本事件，方向一致无副作用） */
function onStreamScroll() {
  const box = streamEl.value
  if (!box) return
  stick.value = box.scrollHeight - box.scrollTop - box.clientHeight < BOTTOM_EPS
}

/** 吸附时把消息区滚到底 */
function scrollStream() {
  const box = streamEl.value
  if (box && stick.value) box.scrollTop = box.scrollHeight
}

// 消息 / 草稿 / 状态变化后：吸附则跟随，否则保持用户当前阅读位置
watch(
  () => [messages.value.length, draft.out, draft.think, liveTools.value.length,
         status.value, toolsText.value],
  async () => {
    await nextTick()
    scrollStream()
  }
)

async function send() {
  const el = textEl.value
  const text = (el && el.value) || ''
  stick.value = true // 刚发消息必然要看最新回复
  const res = await send0(text)
  if (el) el.value = ''
  await nextTick()
  scrollStream()
  // 这次问答生成了改动提案 → 产出物浮窗里的列表立刻刷新。
  // 不刷的表现：浮窗里停在发送前的空列表，得切页签才看得到新提案。
  // 刻意**不自动弹开浮窗**：浮窗带遮罩，自动弹会打断对话（用户正要看回答，却被
  // 一层遮罩盖住）；标题栏按钮上的计数角标足以提示「有新的产出物」。
  if (res && res.artifact) await loadArtifacts('chat')
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

/** 空态建议问题：点一下直接替用户提问（填进输入框 → 走同一条 send 链路） */
async function askSample(text) {
  const el = textEl.value
  if (!el || busy.value) return
  el.value = text
  await send()
}

function pickFile() {
  const fi = fileEl.value
  if (fi) fi.click()
}

function onFileChange(e) {
  const fi = e.target
  uploadFiles(fi.files)
  fi.value = '' // 清空，同一个文件再选一次也要能触发 change
}

/* 拖拽：dragover 必须 preventDefault，否则 drop 事件根本不会触发 */
function onDragEnter(e) {
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
  dragging.value = true
}

function onDragOver(e) {
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
  dragging.value = true
}

function onDragLeave(e) {
  // 只有真正离开本元素时才收高亮（子元素间移动也会触发 dragleave）
  const el = dropEl.value
  if (el && e.relatedTarget && el.contains(e.relatedTarget)) return
  dragging.value = false
}

function onDrop(e) {
  dragging.value = false
  onDropFiles(e)
}

async function openArtifact(id) {
  emit('open-artifact', id)
}

/** Esc 收起浮层（浮层没有焦点陷阱，键盘用户也得能关掉） */
function onEsc(e) {
  if (e.key === 'Escape') closeFloats()
}

onMounted(async () => {
  await loadSessions()
  // markdown 里的站内相对链接（分类/缺陷.md）交给上层路由到知识库弹窗
  wireKbLinks(streamEl.value, (rel) => emit('open-kb-doc', rel))
  window.addEventListener('keydown', onEsc)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onEsc)
})
</script>

<template>
  <!-- 布局：左（会话历史） / 中（对话区）。
       历史与产出物原先各占一条可收起的侧栏，但右栏只有 320px —— 产出物卡片和
       四行使用说明都挤在里面，还顺手把对话区压窄。现在右栏拆成两个**浮层**：
       产出物 = 右侧浮窗抽屉、使用说明 = 居中浮窗，都由对话区标题栏的按钮打开。
       对话区因此独占剩余宽度；高度也不再是 `100vh - 写死的常量`，而是顺着
       style.css 顶部那条高度链吃满主列剩余空间（内容多到一屏装不下时才整页滚）。 -->
  <section class="panel chat-pane">
    <div class="chat-grid" :class="{ 'rail-history-collapsed': historyCollapsed }">
      <!-- ── 左栏：会话历史 ── -->
      <aside class="chat-rail" id="chat-rail-history" :class="{ collapsed: historyCollapsed }">
        <!-- 收起后的竖排展开条（整栏高度让它跟着长满） -->
        <button type="button" class="rail-expand" id="chat-history-expand"
                title="展开会话历史" @click="toggleHistory">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 6l6 6-6 6" /></svg>
          会话
        </button>

        <div class="rail-card">
          <div class="rail-card-head">
            <span class="rail-card-title">会话历史</span>
            <div class="rail-card-actions">
              <el-button id="btn-chat-new" size="small" @click="newSession()">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
                新会话
              </el-button>
              <button type="button" class="rail-fold" id="chat-history-fold"
                      title="收起会话历史" @click="toggleHistory">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 6l-6 6 6 6" /></svg>
              </button>
            </div>
          </div>
          <!-- vertical：侧栏里改成竖排可滚的列表（横排 chips 是旧版堆在顶部的形态） -->
          <ChatSessions class="vertical" :sessions="sessions" :current="current"
                        :error="sessionsError" @open="openSession" />
        </div>
      </aside>

      <!-- ── 中栏：对话区（标题栏 + 消息流 + 输入条，输入条固定在底部） ── -->
      <div class="chat-main">
        <div class="chat-main-head">
          <h2>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.7 9.7 0 0 1-2.8-.4L3 21l1.6-4.6A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4Z" /></svg>
            问答 · 随便问，或顺手改点代码
            <!-- 边界提示：使用说明搬进浮窗后，这里留一句常驻的（也是「只读」这个
                 界面用例断言的可见来源 —— 浮窗默认关着，可见文本里读不到它） -->
            <span class="chat-head-hint" id="chat-head-hint">只读仓库 · 改动要人工采纳才写入</span>
          </h2>
          <div class="usage-controls">
            <!-- 偏好开关：保留原生 checkbox（id 被 Playwright 直接 check/uncheck/读 checked） -->
            <label class="switch" title="开启后 AI 可以自己去仓库里列目录/读文件/正则搜索找证据">
              <input type="checkbox" id="chat-use-repo" v-model="useRepo" @change="persistPrefs">
              <span class="track" /><span class="switch-label">允许查仓库</span>
            </label>
            <label class="switch" title="开启后 AI 在你要求改代码时会附一段改动提案（落成产出物，采纳才写仓库）">
              <input type="checkbox" id="chat-allow-patch" v-model="allowPatch" @change="persistPrefs">
              <span class="track" /><span class="switch-label">可出改动提案</span>
            </label>

            <!-- 两个浮层入口：产出物（右侧抽屉）/ 使用说明（居中浮窗） -->
            <el-button id="btn-chat-artifacts" size="small" :class="{ 'is-on': artOpen }"
                       title="查看本次问答产出的改动提案（右侧浮窗，采纳才会写工作区）"
                       @click="toggleArt">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l9 5v10l-9 5-9-5V7Z" /><path d="M12 12l9-5M12 12v10M12 12L3 7" /></svg>
              产出物
              <span class="mini-count" id="chat-art-count"
                    :class="{ alert: artifactBadge.alert, hidden: !artifactCount }">{{ artifactCount }}</span>
            </el-button>
            <el-button id="btn-chat-help" size="small" :class="{ 'is-on': helpOpen }"
                       title="问答能做什么、不能做什么（浮窗）" @click="toggleHelp">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4v.4" /><path d="M12 17h.01" /></svg>
              使用说明
            </el-button>

            <el-button id="btn-chat-clear" size="small" title="清空当前会话的消息（保留会话）"
                       @click="clearSession()">清空</el-button>
            <el-button id="btn-chat-del" size="small" title="删除当前会话"
                       @click="deleteSession()">删除</el-button>
          </div>
        </div>

        <div class="chat-box">
          <div id="chat-stream" ref="streamEl" class="chat-stream" @scroll="onStreamScroll"
               data-placeholder="在下面输入你的问题。可以问「这个项目的路由是怎么配的」，也可以直接说「把 xxx 组件的 loading 状态补上」。">
            <div v-if="!messages.length && !busy" class="chat-empty">
              <div class="chat-empty-logo" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.7 9.7 0 0 1-2.8-.4L3 21l1.6-4.6A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4Z" /></svg>
              </div>
              <div class="chat-empty-title">随便问点什么，或者顺手让我改点代码</div>
              <!-- 建议问题可直接点击发起（askSample），像主流 AI 对话的开场引导 -->
              <div class="chat-empty-tips">
                <button type="button" class="chat-tip" @click="askSample('这个项目的路由是怎么配的？')">这个项目的路由是怎么配的？</button>
                <button type="button" class="chat-tip" @click="askSample('这个报错可能是什么原因？（把日志粘进来）')">这个报错可能是什么原因？（把日志粘进来）</button>
                <button type="button" class="chat-tip" @click="askSample('把 xxx 组件的 loading 状态补上')">把 xxx 组件的 loading 状态补上</button>
              </div>
              <div class="chat-empty-note">
                开启「允许查仓库」时，我会自己列目录 / 读文件 / 正则搜索去找证据；引用代码会带 <code>文件路径:行号</code>。
              </div>
            </div>

            <ChatMessage v-for="(m, i) in messages" :key="i" :msg="m"
                         @open-artifact="openArtifact" />
            <ChatPending v-if="busy" :think="draft.think" :out="draft.out" :tools="liveTools" />

            <!-- 运行状态行：放进消息流内部（空闲时整个隐藏），不再用一条带分隔线的
                 状态条横在消息区与输入框中间 —— 那条分隔带就是把「会话区」和
                 「对话框」隔开的东西。 -->
            <div class="chat-live" id="chat-live" :class="{ hidden: !status && !toolsText }">
              <span class="chat-status" id="chat-status">{{ status }}</span>
              <span class="muted" id="chat-tools">{{ toolsText }}</span>
            </div>
          </div>

          <!-- 输入区嵌在聊天容器内（.chat-box）底部，视觉上与消息区一体 -->
          <div class="chat-input" id="chat-input">
            <div class="chat-drop" id="chat-drop" ref="dropEl" :class="{ dragging }"
                 @dragenter.prevent="onDragEnter" @dragover.prevent="onDragOver"
                 @dragleave.prevent="onDragLeave" @drop.prevent="onDrop">
              <div class="chat-attach" id="chat-attach">
                <ChatAttachBar :atts="pendingAtts" :uploading="uploading" @remove="removeAtt" />
              </div>
              <!-- 保留原生 textarea：id 被 Playwright fill/press/读 value，且 send() 直接读 el.value -->
              <textarea id="chat-text" ref="textEl" rows="3"
                        placeholder="Enter 发送，Shift+Enter 换行。支持直接粘贴截图（Ctrl/⌘+V），也可以点「上传文件」或把文件拖到这里。"
                        @keydown="onKeydown" @paste="onPaste" />
              <div class="chat-attach-bar">
                <!-- 保留原生 file input：Element 的 el-upload 事件模型与原生差别大，硬套会丢 paste/drop 链路 -->
                <input type="file" id="chat-file" ref="fileEl" multiple class="hidden"
                       accept="image/*,.txt,.log,.md,.csv,.json,.xml,.yml,.yaml,.ts,.tsx,.js,.jsx,.vue,.css,.scss,.py,.java,.go,.sh,.sql,.ini,.conf,.patch,.diff"
                       @change="onFileChange">
                <el-button id="btn-chat-upload" size="small" title="选择图片或文本文件（日志、代码、配置等）"
                           @click="pickFile">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>
                  上传文件
                </el-button>
                <span class="chat-attach-hint muted" id="chat-attach-hint"
                      :class="{ hidden: !!pendingAtts.length }">可粘贴截图 / 上传日志与代码文件</span>
                <!-- 发送/停止收进输入卡片内部的右下角（主流 AI 对话的输入条形态） -->
                <span class="chat-send-area">
                  <el-button id="btn-chat-stop" size="small" :class="{ hidden: !busy }"
                             title="断开当前输出（服务端会跑完这一次调用，结果仍会存进会话）"
                             @click="stop()">停止</el-button>
                  <el-button type="primary" id="btn-chat-send" :disabled="sendDisabled" @click="send">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13" /><path d="M22 2l-7 20-4-9-9-4 20-7Z" /></svg>
                    发送
                  </el-button>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 浮层：产出物抽屉 + 使用说明浮窗 ──
         这两块原来占着右栏（320px 挤产出物卡片和四行说明）。改成浮层后：
           · 关着时用 .hidden（display:none）而不是 v-if —— 页签切走时 el-tab-pane
             本身就是 display:none，浮层跟着一起藏，不会串到别的页签；
             display:none 也让 #pane-chat 的可见文本保持干净（用例读它当正文）。
           · 都挂在 section 内（position:fixed），遮罩点击 / Esc 一起收。 -->
    <div class="chat-float-mask" id="chat-float-mask"
         :class="{ hidden: !artOpen && !helpOpen }" @click="closeFloats" />

    <aside class="chat-drawer" id="chat-art-drawer" :class="{ hidden: !artOpen }"
           aria-label="问答产出物">
      <div class="chat-float-head">
        <span class="chat-float-title">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l9 5v10l-9 5-9-5V7Z" /><path d="M12 12l9-5M12 12v10M12 12L3 7" /></svg>
          产出物
        </span>
        <button type="button" class="chat-float-close" id="chat-art-close"
                title="收起产出物浮窗" @click="toggleArt">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
      <div class="chat-drawer-body">
        <!-- 产出物：问答侧的实例。idSuffix 必须传 —— App.vue 的全局面板在其它页签
             仍在 DOM 里（el-tab-pane 不卸载），两个实例同 id 会串。 -->
        <ArtifactsPanel label="问答" :visible="true" id-suffix="chat"
                        empty-hint="还没有产出物。提问时让它改代码（回答末尾会附一段改动提案），采纳后才会写工作区。"
                        @open="openArtifact" />
      </div>
    </aside>

    <div class="chat-help" id="chat-help-pop" :class="{ hidden: !helpOpen }"
         role="dialog" aria-label="使用说明">
      <div class="chat-float-head">
        <span class="chat-float-title">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4v.4" /><path d="M12 17h.01" /></svg>
          使用说明
        </span>
        <button type="button" class="chat-float-close" id="chat-help-close"
                title="关闭使用说明" @click="toggleHelp">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
      <!-- hidden 而不是 v-if：用例按 #pane-chat .info-note 取 <br> 数量 -->
      <div class="info-note">
        问答<b>只读仓库</b>：AI 用列目录 / 读文件 / 正则搜索自己找证据，引用代码会带 <code>文件路径:行号</code>。<br>
        支持<b>粘贴截图</b>（Ctrl/⌘+V）与<b>上传文件</b>：图片会发给模型看图，文本类文件（日志、代码、配置）会作为附件读入。<br>
        要改代码时它只在回答末尾附一段<b>改动提案</b>，落成「产出物」——和别的任务一样，<b>人工采纳才会写工作区</b>（不 commit、不 push）。提案生成后右侧浮窗会自动弹出。<br>
        会话保存在 <code>chat_sessions/</code>，附件保存在 <code>attachments/</code>，随时可以回看。
      </div>
    </div>
  </section>
</template>
