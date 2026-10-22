// 自测页签（2026-10-21）的表单状态与副作用。
//
// 与 useTasks 分开的原因：自测不走「/api/tasks/run → 补丁文件 → 采纳」那条链路，
// 它一次请求里跑三条链路、返回的是**逐条判定表**，也没有可采纳的文件。
// 共用的只有参数 schema、产出物列表与运行状态。
//
// 状态全部**模块级单例**：页签与产出物面板分处两个组件，各自调 useSelfTest()，
// 各自 new 一套 ref 的话，跑完写的是 A 套、渲染读的是 B 套，界面会一直空
// （useArtifacts 文件头记着同一个坑）。
//
// 环境地址与路由落 localStorage：它们是环境事实，不是每次都要重填的临时输入。
import { ref, watch } from 'vue'
import { api, uploadSelfTestAttachment } from '../api/client.js'
import { useToast } from './useToast.js'
import { setRunStatus } from './useRunStatus.js'
import { useArtifacts } from './useArtifacts.js'
import { useHealth } from './useHealth.js'
import { schemaDefault } from './useParamSchema.js'

const ENV_KEY = 'selftest-env'

/** 与后端 SELFTEST_ATT_MAX 同值：一次最多 6 份，超出部分前端先拦 */
const ST_ATT_MAX = 6
/** 与后端白名单一致（文本 / PDF）；图片在自测里没有消费方 */
const ST_ATT_ACCEPT = '.md,.markdown,.txt,.pdf,text/markdown,text/plain,application/pdf'

/** 最近一次判定结果（含 verdicts / counts / lanes / environment / notes） */
const result = ref(null)
const running = ref(false)
const pulling = ref(false)
const pulled = ref(null)
const error = ref('')

// ---- 实时过程（运行中轮询 /api/run/status 的内存读快照）--------------------
// 后端一次 POST 跑到底、结束才返回，但 progress 会镜像进运行租约——
// 轮询它就能让左侧过程区实时显示「现在在干什么、拆出了几条、判到第几批」。
const stage = ref('')
const stageDetail = ref('')
const elapsed = ref(0)
let _stageTimer = null
const STAGE_POLL_MS = 2000

function startStagePoll() {
  stopStagePoll()
  _stageTimer = setInterval(async () => {
    try {
      const r = await api.runStatus()
      if (!r || !r.running) return
      stage.value = String(r.stage || '')
      stageDetail.value = String(r.stage_detail || '')
      elapsed.value = Number(r.elapsed_seconds) || 0
    } catch (e) { /* 一次轮询失败不中断运行 */ }
  }, STAGE_POLL_MS)
}

function stopStagePoll() {
  if (_stageTimer) { clearInterval(_stageTimer); _stageTimer = null }
}

function loadEnv() {
  try {
    const raw = JSON.parse(localStorage.getItem(ENV_KEY) || '{}')
    return { url: String(raw.url || ''), routes: String(raw.routes || '') }
  } catch (e) {
    return { url: '', routes: '' }
  }
}

// ---- 表单状态（同样模块级单例）--------------------------------------------
// 页签是 `v-else-if` 挂的：切走就卸载，ref 建在 composable 里等于「换个页签
// 回来，刚贴的需求文档和上传的 PDF 全没了」。上传附件尤其不能丢 —— 用户要
// 重新找文件、重新解析一遍 PDF。这里与 result 一样提到模块级，跨页签保留。
const _env0 = loadEnv()
const stIssue = ref(String(schemaDefault('selftest', 'stIssue', '')))
const stUrl = ref(_env0.url || String(schemaDefault('selftest', 'stUrl', '')))
const stRoutes = ref(_env0.routes || String(schemaDefault('selftest', 'stRoutes', '/')))
const stLanes = ref({ ...(schemaDefault('selftest', 'stLanes', null)
  || { case: true, req: true, figma: true }) })
const stCases = ref('')
const stReqDoc = ref('')
const stFigma = ref('')
const stLimit = ref(Number(schemaDefault('selftest', 'stLimit', 12)) || 12)
/** 已上传的需求文档（只存元数据 + 解析预览，正文运行时由服务端读） */
const stAtts = ref([])
const stAttUploading = ref(false)

watch([stUrl, stRoutes], ([url, routes]) => {
  try {
    localStorage.setItem(ENV_KEY, JSON.stringify({ url, routes }))
  } catch (e) { /* 隐私模式：不落盘也不影响本次运行 */ }
})

export function useSelfTest() {
  const { toast } = useToast()
  const { loadArtifacts } = useArtifacts()
  const { loadHealth } = useHealth()

  /** 勾选中的链路，按后端认识的顺序输出 */
  function lanes() {
    const on = stLanes.value || {}
    return ['case', 'req', 'figma'].filter((k) => !!on[k])
  }

  /** 上传需求文档（md / txt / pdf）：服务端解析 PDF 文字层后返回元数据 + 预览 */
  async function uploadReqDocs(files) {
    const list = Array.from(files || [])
    if (!list.length) return
    const room = ST_ATT_MAX - stAtts.value.length
    if (room <= 0) {
      toast(`一次最多带 ${ST_ATT_MAX} 份需求文档，请先移除已上传的`, 'err')
      return
    }
    stAttUploading.value = true
    try {
      for (const file of list.slice(0, room)) {
        try {
          const data = await uploadSelfTestAttachment(file, file.name)
          const att = data.attachment || {}
          stAtts.value.push({ ...att, preview: data.preview || '' })
          const chars = att.chars ? `，抽出 ${att.chars} 字符` : ''
          toast(`已读取「${att.name}」（${attSize(att.size)}${chars}）`, 'ok')
        } catch (e) {
          // 单份失败不拖累其余：一次选三四份时，不该因为一份扫描版 PDF
          // 就把整批丢掉。失败原因（比如「没有文字层」）原样透出。
          toast(`「${file.name}」未能读取: ${(e && e.message) || e}`, 'err')
        }
      }
      if (list.length > room) {
        toast(`一次最多 ${ST_ATT_MAX} 份，另有 ${list.length - room} 份未上传`, 'warn')
      }
    } finally {
      stAttUploading.value = false
    }
  }

  function removeReqDoc(id) {
    stAtts.value = stAtts.value.filter((a) => a.id !== id)
  }

  function attSize(n) {
    const v = Number(n) || 0
    if (v >= 1048576) return `${(v / 1048576).toFixed(1)}MB`
    if (v >= 1024) return `${Math.round(v / 1024)}KB`
    return `${v}B`
  }

  /** 从 ONES「测试情况」tab 预览用例 —— 先看见拉到了什么，再决定跑不跑 */
  async function pullCases() {
    const issue = stIssue.value.trim()
    if (!issue) { toast('请先填 ONES 工单编号或 UUID', 'err'); return }
    pulling.value = true
    try {
      const data = await api.selftestCases(issue)
      pulled.value = data
      if (data.count) toast(`拉到 ${data.count} 条用例`, 'ok')
      else toast(`没有拉到用例：${data.note || '换个方式手贴用例'}`, 'warn')
    } catch (e) {
      pulled.value = null
      toast(`拉取用例失败: ${e}`, 'err')
    } finally {
      pulling.value = false
    }
  }

  async function runSelfTest() {
    const picks = lanes()
    if (!picks.length) { toast('至少勾选一个检查项', 'err'); return }
    const hasInput = stCases.value.trim() || stIssue.value.trim()
      || stReqDoc.value.trim() || stAtts.value.length || stFigma.value.trim()
    if (!hasInput) {
      toast('三条链路都没有输入：填工单、用例、需求文档（粘贴或上传）或 Figma 链接', 'err')
      return
    }
    if (!stUrl.value.trim()) { toast('请填本地环境地址（如 http://localhost:3000）', 'err'); return }
    running.value = true
    error.value = ''
    stage.value = ''
    stageDetail.value = ''
    elapsed.value = 0
    startStagePoll()
    setRunStatus('自测中…', 'running')
    try {
      const data = await api.selftestRun({
        lanes: picks,
        url: stUrl.value.trim(),
        routes: stRoutes.value.trim(),
        issue: stIssue.value.trim(),
        cases_text: stCases.value,
        req_doc: stReqDoc.value,
        // 附件只传引用，正文由服务端在运行时读取并合并（PDF 在上传时就抽好了）；
        // 认不出的 id 服务端会静默丢弃，不会因为一个过期附件拦住整次自测。
        attachment_ids: stAtts.value.map((a) => a.id),
        figma_url: stFigma.value.trim(),
        limit: Number(stLimit.value) || 12,
      })
      result.value = data.result || null
      const c = (data.result || {}).counts || {}
      setRunStatus(`自测完成 · 不通过 ${c.fail || 0} / 无法判断 ${c.unknown || 0}`, 'ok')
      const failed = Number(c.fail || 0)
      toast(`自测完成：不通过 ${failed} 项，无法判断 ${Number(c.unknown || 0)} 项`,
        failed ? 'warn' : 'ok')
      await Promise.all([loadArtifacts('selftest'), loadHealth()])
    } catch (e) {
      error.value = String(e)
      setRunStatus('自测失败', 'error')
      toast(`自测失败: ${e}`, 'err')
    } finally {
      running.value = false
      stopStagePoll()
    }
  }

  /** 页面重载后把最近一份自测报告拉回来（判定表不该只活在一次会话里） */
  async function restore() {
    if (result.value) return
    try {
      await loadArtifacts('selftest')
      const list = (useArtifacts().items.value || []).filter((a) => a.type === 'selftest')
      if (!list.length) return
      const d = await api.artifact(list[0].id)
      if (!d || d.type !== 'selftest') return
      result.value = {
        summary: d.summary || '',
        counts: d.verdict_counts || {},
        lanes: d.lanes || [],
        verdicts: d.verdicts || [],
        environment: d.environment || {},
        notes: d.notes || [],
        error: d.error || '',
      }
    } catch (e) { /* 列表在就够用，详情失败不阻塞页面 */ }
  }

  return {
    stIssue, stUrl, stRoutes, stLanes, stCases, stReqDoc, stFigma, stLimit,
    stAtts, stAttUploading, ST_ATT_MAX, ST_ATT_ACCEPT,
    running, pulling, pulled, error, result,
    stage, stageDetail, elapsed,
    pullCases, runSelfTest, restore,
    uploadReqDocs, removeReqDoc, attSize,
  }
}
