<script setup>
// 问答页签（#pane-chat）。旧版 index.html:170-233 + app.js:3830-4337 的等价迁移。
//
// 与前一个页签（统计）不同的两处：
//   1. 有长驻副作用（消息流、附件上传），所以逻辑全放 useChat composable，
//      组件只做「事件 → composable 方法」的映射；
//   2. 有 DOM 时序要求：新消息到达要自动滚到底，但「用户在往上翻历史」时不能抢滚动，
//      这一条旧版用 chatNearBottom() 判定，这里原样保留。
import { nextTick, onMounted, ref, watch } from 'vue'
import { useChat } from '../composables/useChat.js'
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

const emit = defineEmits(['open-artifact', 'open-kb-doc'])

/** 判「贴近底部」的容差与旧版一致（app.js:4119 用 140px） */
const NEAR_BOTTOM_PX = 140

function nearBottom() {
  const box = streamEl.value
  if (!box) return true
  return box.scrollHeight - box.scrollTop - box.clientHeight < NEAR_BOTTOM_PX
}

/**
 * 把消息区滚到底。
 * @param {boolean} stick 强制滚到底（用户刚发了消息时用）
 */
function scrollStream(stick) {
  const box = streamEl.value
  if (box && stick) box.scrollTop = box.scrollHeight
}

// 消息 / 草稿变化后：贴底则跟随，否则保持用户当前阅读位置
watch(
  () => [messages.value.length, draft.out, draft.think, liveTools.value.length],
  async () => {
    const stick = nearBottom()
    await nextTick()
    scrollStream(stick)
  }
)

async function send() {
  const el = textEl.value
  const text = (el && el.value) || ''
  const stick = true
  await send0(text)
  if (el) el.value = ''
  await nextTick()
  scrollStream(stick)
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
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

onMounted(async () => {
  await loadSessions()
  // markdown 里的站内相对链接（分类/缺陷.md）交给上层路由到知识库弹窗
  wireKbLinks(streamEl.value, (rel) => emit('open-kb-doc', rel))
})
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.7 9.7 0 0 1-2.8-.4L3 21l1.6-4.6A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4Z" /></svg>
        问答 · 随便问，或顺手改点代码
      </h2>
      <div class="usage-controls">
        <label class="switch" title="开启后 AI 可以自己去仓库里列目录/读文件/正则搜索找证据">
          <input type="checkbox" id="chat-use-repo" v-model="useRepo" @change="persistPrefs">
          <span class="track" /><span class="switch-label">允许查仓库</span>
        </label>
        <label class="switch" title="开启后 AI 在你要求改代码时会附一段改动提案（落成产出物，采纳才写仓库）">
          <input type="checkbox" id="chat-allow-patch" v-model="allowPatch" @change="persistPrefs">
          <span class="track" /><span class="switch-label">可出改动提案</span>
        </label>
        <button class="btn-ghost" id="btn-chat-new" @click="newSession()">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
          新会话
        </button>
        <button class="btn-ghost" id="btn-chat-clear" title="清空当前会话的消息（保留会话）"
                @click="clearSession()">清空</button>
        <button class="btn-ghost" id="btn-chat-del" title="删除当前会话"
                @click="deleteSession()">删除会话</button>
      </div>
    </div>

    <ChatSessions :sessions="sessions" :current="current" :error="sessionsError"
                  @open="openSession" />

    <div class="chat-box">
      <div id="chat-stream" ref="streamEl" class="chat-stream"
           data-placeholder="在下面输入你的问题。可以问「这个项目的路由是怎么配的」，也可以直接说「把 xxx 组件的 loading 状态补上」。">
        <div v-if="!messages.length && !busy" class="chat-empty">
          <div class="chat-empty-title">随便问点什么，或者顺手让我改点代码</div>
          <div class="chat-empty-tips">
            <span>这个项目的路由是怎么配的？</span>
            <span>这个报错可能是什么原因？（把日志粘进来）</span>
            <span>把 xxx 组件的 loading 状态补上</span>
          </div>
          <div class="chat-empty-note">
            开启「允许查仓库」时，我会自己列目录 / 读文件 / 正则搜索去找证据；引用代码会带 <code>文件路径:行号</code>。
          </div>
        </div>

        <ChatMessage v-for="(m, i) in messages" :key="i" :msg="m"
                     @open-artifact="openArtifact" />
        <ChatPending v-if="busy" :think="draft.think" :out="draft.out" :tools="liveTools" />
      </div>
      <div class="chat-live" id="chat-live">
        <span class="chat-status" id="chat-status">{{ status }}</span>
        <span class="muted" id="chat-tools">{{ toolsText }}</span>
      </div>

      <!-- 输入区嵌在聊天容器内（.chat-box）底部，视觉上与消息区一体 -->
      <div class="chat-input" id="chat-input">
        <div class="chat-drop" id="chat-drop" ref="dropEl" :class="{ dragging }"
             @dragenter.prevent="onDragEnter" @dragover.prevent="onDragOver"
             @dragleave.prevent="onDragLeave" @drop.prevent="onDrop">
          <div class="chat-attach" id="chat-attach">
            <ChatAttachBar :atts="pendingAtts" :uploading="uploading" @remove="removeAtt" />
          </div>
          <textarea id="chat-text" ref="textEl" rows="3"
                    placeholder="Enter 发送，Shift+Enter 换行。支持直接粘贴截图（Ctrl/⌘+V），也可以点「上传文件」或把文件拖到这里。"
                    @keydown="onKeydown" @paste="onPaste" />
          <div class="chat-attach-bar">
            <input type="file" id="chat-file" ref="fileEl" multiple class="hidden"
                   accept="image/*,.txt,.log,.md,.csv,.json,.xml,.yml,.yaml,.ts,.tsx,.js,.jsx,.vue,.css,.scss,.py,.java,.go,.sh,.sql,.ini,.conf,.patch,.diff"
                   @change="onFileChange">
            <button class="btn-ghost btn-sm" id="btn-chat-upload" title="选择图片或文本文件（日志、代码、配置等）"
                    @click="pickFile">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>
              上传文件
            </button>
            <span class="chat-attach-hint muted" id="chat-attach-hint"
                  :class="{ hidden: !!pendingAtts.length }">可粘贴截图 / 上传日志与代码文件</span>
          </div>
        </div>
        <div class="chat-input-side">
          <button class="btn-primary" id="btn-chat-send" :disabled="sendDisabled" @click="send">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13" /><path d="M22 2l-7 20-4-9-9-4 20-7Z" /></svg>
            发送
          </button>
          <button class="btn-ghost" id="btn-chat-stop" :class="{ hidden: !busy }"
                  title="断开当前输出（服务端会跑完这一次调用，结果仍会存进会话）"
                  @click="stop()">停止</button>
        </div>
      </div>
    </div>

    <div class="info-note">
      问答<b>只读仓库</b>：AI 用列目录 / 读文件 / 正则搜索自己找证据，引用代码会带 <code>文件路径:行号</code>。<br>
      支持<b>粘贴截图</b>（Ctrl/⌘+V）与<b>上传文件</b>：图片会发给模型看图，文本类文件（日志、代码、配置）会作为附件读入。<br>
      要改代码时它只在回答末尾附一段<b>改动提案</b>，落成「产出物」——和别的任务一样，<b>人工采纳才会写工作区</b>（不 commit、不 push）。<br>
      会话保存在 <code>chat_sessions/</code>，附件保存在 <code>attachments/</code>，随时可以回看。
    </div>
  </section>
</template>
