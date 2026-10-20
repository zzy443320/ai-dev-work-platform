// 三个任务页签（req 需求开发 / reqdev Figma 转代码 / apidebug 接口联调 /
// codetest 代码测试）的表单状态与副作用。旧版 app.js:1826/1873-2001 的等价迁移。
//
// 三个页签共用一条「/api/tasks/run → 产出物 → 人工采纳」链路，差异只在表单
// 字段与校验提示。figmaDesign（拉回来的设计稿）是 reqdev 的隐藏输入，
// fetchFigma 与 runTask 各碰一头 —— 收进同一个 composable 才不会丢。
//
// ⚠️ 空白保真：#figma-tree / #test-preview 里的标题行（.log-title）与内容行
// （.log-html）是 innerHTML 结构，v-html 时不要加额外换行/空格。
//
// ⚠️ 这两段 HTML 的每一个外部字段都必须过 escapeHtml：内容来自 Figma（外部
// 站点）和目标仓库里的任意文件，一个 `<img src=x onerror=...>` 就是执行脚本。
// 其余拼 HTML 的地方（useDefect / proposalRender / useSettings）都是这么做的。
import { ref, watch } from 'vue'
import { api, uploadReqAttachment } from '../api/client.js'
import { escapeHtml } from '../utils/format.js'
import { useToast } from './useToast.js'
import { setRunStatus } from './useRunStatus.js'
import { useArtifacts } from './useArtifacts.js'
import { useHealth } from './useHealth.js'
import { schemaDefault } from './useParamSchema.js'

/** Figma 取稿通道的 localStorage 键（与旧版同源：figma-fetch-mode） */
const FIGMA_MODE_KEY = 'figma-fetch-mode'

/** 需求开发的分诊结论。**模块级单例**：切到别的页签再回来还在，
 *  「Figma 转代码」页也能读到它做上下文预填（见 useReqHandoff）。 */
const planResult = ref(null)

export function useTasks() {
  const { toast } = useToast()
  const { loadArtifacts, items, type } = useArtifacts()
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
  }
  // 落盘跟着**状态**走，不跟着某个调用方走：参数轨里的分段控件是直接写 ref 的
  // （渲染器不认识 setFigmaMode 这种模块专属 setter），写在 setter 里会漏掉那条路径。
  watch(figmaMode, (m) => {
    try { localStorage.setItem(FIGMA_MODE_KEY, m) } catch (e) { /* 隐私模式 */ }
  })

  /** 内嵌看稿「点选读属性」的结果落到设计稿结构区（AI 的输入依据之一）。
   *  只追加不覆盖：一次会话点多个节点，逐条累积；escapeHtml 走全文转义，
   *  面板文本是外部站点的内容，不能当 HTML 插进来。 */
  function appendFigmaInspection(text) {
    if (!text) return
    const block = `<div class="log-title">点选读属性 · ${escapeHtml(new Date().toLocaleTimeString())}</div>`
      + `<div class="log-html">${escapeHtml(text)}</div>`
    figmaTree.value = figmaTree.value ? figmaTree.value + block : block
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
      figmaTree.value = `<div class="log-title">${escapeHtml(head)}`
        + `${data.truncated ? '（内容过长，已截断）' : ''}</div>`
        + (data.note ? `<div class="info-note">${escapeHtml(data.note)}</div>` : '')
        + `<div class="log-html">${escapeHtml(data.tree || '')}</div>`
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

  // ---- reqdev 表单（默认值来自 /api/params/schema）----
  const figmaUrl = ref('')
  const figmaToken = ref('')
  const reqFramework = ref(schemaDefault('reqdev', 'reqFramework', 'React + TypeScript'))
  const reqDesc = ref('')
  const reqContext = ref('')
  const reqNotes = ref('')

  // ---- req 需求开发表单 ----
  // 字段 key 与 web/params_schema.py 的 "req" 模块一一对应；reqNotes 在这个模块里
  // 叫 reqPlanNotes，避免和上面的 reqdev.reqNotes（生成参数-其他约定）撞名。
  // 2026-10-20 收敛：技术栈由后端读仓库自动识别（不再手选）、需求来源并入自由
  // 文本 —— 核心输入只剩 需求内容 / Figma 链接 / 注意事项。
  const reqDoc = ref('')
  const reqFigma = ref('')
  const reqScope = ref('')
  const reqCodeContext = ref('')
  const reqPlanNotes = ref('')
  /** 交付方式：auto=能改就改（默认，把需求做完）；triage=只出方案；code=缺设计稿也出代码 */
  const reqDeliver = ref(String(schemaDefault('req', 'reqDeliver', 'auto')))

  // ---- 需求附件（md / txt / pdf）----
  // 需求文档在真实工作里常常是一份文件而不是一段可粘贴的文字：PRD 是 md、评审稿
  // 是 pdf、产品发来的是 txt。这里只保存**元数据 + 预览**，正文由服务端在运行时
  // 从附件读取并拼进 requirement_doc —— 单一数据源，不会出现「前端预填的文本」
  // 与「后端的附件正文」两份互相漂移。
  const reqAtts = ref([])
  const reqAttUploading = ref(false)
  /** 与后端 REQ_ATT_MAX 同值：一次最多 6 份，超出部分前端先拦，避免传完才报错 */
  const REQ_ATT_MAX = 6
  /** 与后端 classify() 的白名单对齐：md / txt / pdf（其它扩展名服务端会拒） */
  const REQ_ATT_ACCEPT = '.md,.markdown,.txt,.pdf,text/markdown,text/plain,application/pdf'

  async function uploadReqAttachments(files) {
    const list = Array.from(files || [])
    if (!list.length) return
    const room = REQ_ATT_MAX - reqAtts.value.length
    if (room <= 0) {
      toast(`一次最多带 ${REQ_ATT_MAX} 份需求文档，请先移除已上传的`, 'err')
      return
    }
    reqAttUploading.value = true
    try {
      for (const file of list.slice(0, room)) {
        try {
          const data = await uploadReqAttachment(file, file.name)
          const att = data.attachment || {}
          reqAtts.value.push({ ...att, preview: data.preview || '' })
          const chars = att.chars ? `，抽出 ${att.chars} 字符` : ''
          toast(`已读取「${att.name}」（${formatSize(att.size)}${chars}）`, 'ok')
        } catch (e) {
          // 单份失败不拖累其余文件：用户常一次选三四份，不该因为一份扫描版 PDF
          // 就整批丢掉。失败原因（比如「没有文字层」）原样透出。
          toast(`「${file.name}」未能读取: ${(e && e.message) || e}`, 'err')
        }
      }
      if (list.length > room) {
        toast(`一次最多 ${REQ_ATT_MAX} 份，另有 ${list.length - room} 份未上传`, 'warn')
      }
    } finally {
      reqAttUploading.value = false
    }
  }

  function removeReqAttachment(id) {
    reqAtts.value = reqAtts.value.filter((a) => a.id !== id)
  }

  function formatSize(n) {
    const v = Number(n) || 0
    if (v >= 1048576) return `${(v / 1048576).toFixed(1)}MB`
    if (v >= 1024) return `${Math.round(v / 1024)}KB`
    return `${v}B`
  }

  // ---- apidebug 表单（同上）----
  const apiDoc = ref('')
  const apiBase = ref('')
  const apiFramework = ref(schemaDefault('apidebug', 'apiFramework', 'React + TypeScript'))
  const apiContext = ref('')
  const apiNotes = ref('')

  // ---- codetest 表单 ----
  // 默认值来自 schema（web/params_schema.py），不在这里再写一份 'vitest'：
  // 参数一共三处描述（模板标签、composable 初值、后端 clamp），改版前各写各的，
  // 现在只剩「后端一处 + 这里的读取」。main.js 保证挂载前 schema 已到位。
  const testFile = ref(String(schemaDefault('codetest', 'testFile', '')))
  const testFramework = ref(schemaDefault('codetest', 'testFramework', 'vitest'))
  const testFocus = ref(String(schemaDefault('codetest', 'testFocus', '')))
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
      testPreview.value = `<div class="log-title">${escapeHtml(data.path)}`
        + `${data.truncated ? '（超过 2 万字符已截断）' : ''}</div>`
        + `<div class="log-html">${escapeHtml(data.content || '')}</div>`
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
    if (kind === 'req') {
      const doc = reqDoc.value.trim()
      body = {
        task: kind,
        requirement_doc: doc,
        // 附件正文由服务端拼进需求（PDF 在上传时就抽好了），这里只传引用；
        // 认不出的 id 服务端会静默丢弃，不会因为一个过期附件拦住整单。
        attachment_ids: reqAtts.value.map((a) => a.id),
        // Figma 链接（可选）：后端运行时自动拉稿，失败会记进自检「需要确认」
        figma_url: reqFigma.value.trim(),
        scope: reqScope.value.trim(),
        repo_context: reqCodeContext.value,
        notes: reqPlanNotes.value.trim(),
        deliver: reqDeliver.value,
        title: (doc || (reqAtts.value[0] && reqAtts.value[0].name) || '')
          .replace(/\s+/g, ' ').slice(0, 60),
      }
      if (!doc && !reqAtts.value.length) {
        toast('请先粘贴需求描述，或上传需求文档（md / txt / pdf）', 'err')
        return
      }
    } else if (kind === 'reqdev') {
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
      // 需求开发的分诊结论留在本页展示（切页签回来还在）；
      // 其余模块没有这几项，保持 null。带上产出物 id，「查看产出物」才有目标。
      if (kind === 'req') {
        planResult.value = { ...(data.result || {}),
                             id: (data.artifact && data.artifact.id) || '' }
      }
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

  /** 页面重载后把最近一次分诊结论拉回来 —— 否则「分诊卡」是空的，而产出物列表里
   *  明明躺着一条记录。只读详情，不改任何东西。 */
  async function restorePlan() {
    if (planResult.value) return
    if (type.value !== 'req' || !(items.value || []).length) await loadArtifacts('req')
    const first = (items.value || []).find((a) => a.type === 'req')
    if (!first) return
    try {
      const d = await api.artifact(first.id)
      if (!d || d.type !== 'req') return
      planResult.value = {
        id: d.id || '',
        change_type: d.change_type || '',
        change_type_label: d.change_type_label || '',
        needs_design: !!d.needs_design,
        design_brief: d.design_brief || '',
        impacts: d.impacts || [],
        impacts_corrected: 0,
        repo_file_count: 0,
        endpoints: d.endpoints || [],
        files: (d.files || []).map((f) => ({
          path: f.path, action: f.action || '',
          description: f.description || '',
          stats: f.stats || {},
          edits: (f.edits || []).length,
        })),
        stages: d.stages || [],
        changed: !!d.changed,
        selfcheck: d.selfcheck || {},
        gaps: d.gaps || [],
        verify: d.verify || [],
        unresolved: d.unresolved || [],
        deliver: d.deliver || '',
        framework: d.framework || '',
        stack_note: d.stack_note || '',
        summary: d.summary || '',
      }
    } catch (e) {
      // 列表已经在，详情失败不阻塞页面；下次跑一次就会覆盖这份结论
    }
  }

  return {
    // Figma（reqdev）
    figmaMode, setFigmaMode, figmaDesign, fetching, figmaTree, figmaError, fetchFigma,
    appendFigmaInspection,
    // reqdev
    figmaUrl, figmaToken, reqFramework, reqDesc, reqContext, reqNotes,
    // req 需求开发
    reqDoc, reqFigma, reqScope, reqCodeContext, reqPlanNotes, reqDeliver,
    planResult, restorePlan,
    reqAtts, reqAttUploading, uploadReqAttachments, removeReqAttachment,
    REQ_ATT_ACCEPT, REQ_ATT_MAX,
    // apidebug
    apiDoc, apiBase, apiFramework, apiContext, apiNotes,
    // codetest
    testFile, testFramework, testFocus, testLoading, testPreview, testError, loadTestFile,
    // 运行
    running, runTask,
  }
}
