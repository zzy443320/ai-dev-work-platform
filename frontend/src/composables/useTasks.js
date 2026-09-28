// 三个任务页签（reqdev 需求开发 / apidebug 接口联调 / codetest 代码测试）的
// 表单状态与副作用。旧版 app.js:1826/1873-2001 的等价迁移。
//
// 三个页签共用一条「/api/tasks/run → 产出物 → 人工采纳」链路，差异只在表单
// 字段与校验提示。figmaDesign（拉回来的设计稿）是 reqdev 的隐藏输入，
// fetchFigma 与 runTask 各碰一头 —— 收进同一个 composable 才不会丢。
//
// ⚠️ 空白保真：#figma-tree / #test-preview 里的标题行（.log-title）与内容行
// （.log-html）是 innerHTML 结构，v-html 时不要加额外换行/空格。
import { ref } from 'vue'
import { api } from '../api/client.js'
import { useToast } from './useToast.js'
import { setRunStatus } from './useRunStatus.js'
import { useArtifacts } from './useArtifacts.js'
import { useHealth } from './useHealth.js'

/** Figma 取稿通道的 localStorage 键（与旧版同源：figma-fetch-mode） */
const FIGMA_MODE_KEY = 'figma-fetch-mode'

export function useTasks() {
  const { toast } = useToast()
  const { loadArtifacts } = useArtifacts()
  const { loadHealth } = useHealth()

  // ---- Figma 取稿通道（reqdev）----
  function loadFigmaMode() {
    try { return localStorage.getItem(FIGMA_MODE_KEY) === 'browser' ? 'browser' : 'token' } catch (e) { return 'token' }
  }
  const figmaMode = ref(loadFigmaMode())
  /** 设计稿内容（拉取成功才有）；reqdev 提交时作为 design 字段发给后端 */
  const figmaDesign = ref('')
  const fetching = ref(false)
  /** #figma-tree 的 HTML 内容（空 = 走 :empty 占位） */
  const figmaTree = ref('')

  function setFigmaMode(mode) {
    figmaMode.value = mode === 'browser' ? 'browser' : 'token'
    try { localStorage.setItem(FIGMA_MODE_KEY, figmaMode.value) } catch (e) { /* 隐私模式 */ }
  }

  async function fetchFigma() {
    if (fetching.value) return
    fetching.value = true
    try {
      const data = await api.figmaFetch({
        url: figmaUrl.value.trim(),
        token: figmaMode.value === 'token' ? figmaToken.value.trim() : '',
        browser_fallback: figmaMode.value === 'browser',
      })
      figmaDesign.value = data.tree || ''
      const head = data.source === 'browser'
        ? `浏览器通道拉取 · 「${data.file_name || ''}」 · 页面可见文本（无图层结构）`
        : `已拉取「${data.file_name || ''}」· 节点「${data.node_name || ''}」· ${data.node_count} 个节点`
      figmaTree.value = `<div class="log-title">${head}`
        + `${data.truncated ? '（内容过长，已截断）' : ''}</div>`
        + (data.note ? `<div class="info-note">${data.note}</div>` : '')
        + `<div class="log-html">${data.tree || ''}</div>`
      toast(data.source === 'browser' ? '设计稿已通过浏览器通道拉取（结构信息不全）' : '设计稿拉取成功',
        data.source === 'browser' ? 'warn' : 'ok')
    } catch (e) {
      figmaDesign.value = ''
      figmaTree.value = ''
      figmaError.value = String((e && e.message) || e)
      toast(`Figma 拉取失败: ${e}`, 'err')
    } finally {
      fetching.value = false
    }
  }
  /** 拉取失败时 #figma-tree 的纯文本（旧版是 textContent，错误后 :empty 不生效） */
  const figmaError = ref('')

  // ---- reqdev 表单 ----
  const figmaUrl = ref('')
  const figmaToken = ref('')
  const reqFramework = ref('React + TypeScript')
  const reqDesc = ref('')
  const reqContext = ref('')
  const reqNotes = ref('')

  // ---- apidebug 表单 ----
  const apiDoc = ref('')
  const apiBase = ref('')
  const apiFramework = ref('React + TypeScript')
  const apiContext = ref('')
  const apiNotes = ref('')

  // ---- codetest 表单 ----
  const testFile = ref('')
  const testFramework = ref('vitest')
  const testFocus = ref('')
  const testLoading = ref(false)
  const testPreview = ref('')
  const testError = ref('')

  async function loadTestFile() {
    const rel = testFile.value.trim()
    if (!rel) { toast('请先填目标文件路径（相对仓库根）', 'err'); return }
    testLoading.value = true
    try {
      const data = await api.repoFile(rel)
      testError.value = ''
      testPreview.value = `<div class="log-title">${data.path}${data.truncated ? '（超过 2 万字符已截断）' : ''}</div>`
        + `<div class="log-html">${data.content || ''}</div>`
    } catch (e) {
      testPreview.value = ''
      testError.value = String((e && e.message) || e)
      toast(`读取失败: ${e}`, 'err')
    } finally {
      testLoading.value = false
    }
  }

  // ---- 三大任务运行 ----
  const running = ref(false)

  async function runTask(kind) {
    let body
    if (kind === 'reqdev') {
      const req = reqDesc.value.trim()
      body = {
        task: kind,
        requirement: req,
        framework: reqFramework.value,
        design: figmaDesign.value,
        repo_context: reqContext.value,
        notes: reqNotes.value.trim(),
        title: (req || 'Figma 需求').slice(0, 60),
      }
      if (!figmaDesign.value && !req) { toast('请先拉取设计稿或填写需求描述', 'err'); return }
    } else if (kind === 'apidebug') {
      const doc = apiDoc.value
      body = {
        task: kind, api_doc: doc, base_url: apiBase.value.trim(),
        framework: apiFramework.value, repo_context: apiContext.value,
        notes: apiNotes.value.trim(),
      }
      if (!doc.trim()) { toast('请粘贴接口文档', 'err'); return }
    } else {
      const rel = testFile.value.trim()
      body = {
        task: kind, file_path: rel, framework: testFramework.value,
        requirements: testFocus.value.trim(),
      }
      if (!rel) { toast('请填写目标文件路径（相对仓库根）', 'err'); return }
    }

    running.value = true
    setRunStatus('AI 生成中…', 'running')
    try {
      const data = await api.taskRun(body)
      setRunStatus(`产出物已生成 · AI:${data.ai_mode || '?'}`, 'ok')
      if (data.artifact && data.artifact.error) {
        toast(`产出物已保存，但 AI 输出不完整：${data.artifact.error}`, 'warn')
      } else {
        toast('产出物已生成，请在下方审阅后采纳', 'ok')
      }
      await Promise.all([loadArtifacts(kind), loadHealth()])
    } catch (e) {
      setRunStatus('任务失败', 'error')
      toast(`任务失败: ${e}`, 'err')
    } finally {
      running.value = false
    }
  }

  return {
    // Figma（reqdev）
    figmaMode, setFigmaMode, figmaDesign, fetching, figmaTree, figmaError, fetchFigma,
    // reqdev
    figmaUrl, figmaToken, reqFramework, reqDesc, reqContext, reqNotes,
    // apidebug
    apiDoc, apiBase, apiFramework, apiContext, apiNotes,
    // codetest
    testFile, testFramework, testFocus, testLoading, testPreview, testError, loadTestFile,
    // 运行
    running, runTask,
  }
}
