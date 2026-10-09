// 流水线占用租约：回答「现在是谁占着槽位、跑到哪一单、跑了多久、能不能停」。
//
// 为什么不塞进 useHealth：/api/health 每次都要跑一遍仓库预检（内含 git 子进程），
// 拿它当 3 秒轮询的载体会把整页拖慢；/api/run/status 是纯内存读，专为轮询而生。
//
// 这个 composable 存在的直接原因是那次现场：一个外部脚本 POST /api/run 跑批量修复，
// 界面上一整段时间只显示「已有流水线在运行，请等待完成」——不知道是哪条工单、
// 不知道跑了多久、也没有任何停止入口，唯一出路是重启进程。占用标记从此不再是
// 一个裸布尔，前端也就有了可展示、可操作的对象。
import { computed, ref } from 'vue'
import { api } from '../api/client.js'
import { toast } from './useToast.js'

/** 轮询周期：比 SSE 心跳慢、比人眼刷新快，且这一路是内存读，不打扰流水线 */
export const LEASE_POLL_MS = 3000

const run = ref({ running: false })
const leaseError = ref('')
let timer = null

export function elapsedText(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分 ${String(s % 60).padStart(2, '0')} 秒`
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`
}

const busy = computed(() => !!run.value.running)

/** 一行说清占用者：类型 · 工单（第 i/N 条）· 当前阶段 · 来源 · 已运行多久 */
const leaseLine = computed(() => {
  const r = run.value || {}
  if (!r.running) return ''
  const bits = [r.kind_label || r.kind || '任务']
  if (r.defect) {
    bits.push(r.index && r.total
      ? `工单 ${r.defect}（第 ${r.index}/${r.total} 条）`
      : `工单 ${r.defect}`)
  }
  if (r.defect_title) bits.push(String(r.defect_title).slice(0, 30))
  if (r.stage) bits.push(`阶段 ${r.stage}`)
  if (r.label) bits.push(r.label)
  if (r.source) bits.push(`来源 ${r.source}`)
  bits.push(`已运行 ${elapsedText(r.elapsed_seconds)}`)
  return bits.filter(Boolean).join(' · ')
})

/** 顶栏药丸用的短版：只到「什么在跑 + 多久」，细节留给占用条 */
const shortLine = computed(() => {
  const r = run.value || {}
  if (!r.running) return ''
  return `${r.kind_label || r.kind || '任务'}运行中 · ${elapsedText(r.elapsed_seconds)}`
})

/** 第二行：阶段细节（流水线的中文进度描述），没有就不占位 */
const leaseDetail = computed(() => {
  const r = run.value || {}
  return r.running ? String(r.stage_detail || '') : ''
})

const stopping = computed(() => !!(run.value || {}).cancel_requested)

async function refresh() {
  try {
    run.value = (await api.runStatus()) || { running: false }
    leaseError.value = ''
  } catch (e) {
    leaseError.value = (e && e.message) ? e.message : String(e)
  }
}

function startLeasePoll() {
  refresh()
  if (timer) return
  timer = setInterval(refresh, LEASE_POLL_MS)
}

function stopLeasePoll() {
  if (!timer) return
  clearInterval(timer)
  timer = null
}

/**
 * 请求停止当前运行。默认协作式（在阶段边界收口）；force 立刻解除占用。
 * @param {boolean} force
 */
async function stopRun(force = false) {
  try {
    const r = await api.runCancel({ force })
    toast(r.detail || (r.ok ? '已请求停止' : '当前没有运行中的任务'), r.ok ? 'warn' : 'ok')
    await refresh()
    return r
  } catch (e) {
    toast(`停止失败：${(e && e.message) || e}`, 'err')
    return null
  }
}

export function useRunLease() {
  return {
    run, busy, leaseLine, leaseDetail, shortLine, stopping, leaseError,
    refresh, startLeasePoll, stopLeasePoll, stopRun, elapsedText,
  }
}
