<script setup>
// Boss 战场视图 —— 运行中替换掉控制台里的日志区，跑完自动退回。
//
// 为什么值得单独一个视图：流水线跑起来时人真正在等的是三件事 ——
// 「打到哪了」「还要多久」「有没有卡住」。日志是这四样里信息密度最低的一种：
// 三十行等宽文本里找当前阶段，不如一眼看见血条掉了一格。
//
// ⚠️ 每个数字都必须挂在真实信号上，不许编：
//   HP    = 5 个阶段里还没打通的占比（stageStates，与顶部阶段细轨同源）
//   目标  = 流里 defect 事件的 index/total/title（useDefect.runTarget）
//   护盾  = 配置里的闸门形态（gate.degraded → 破甲）
//   施法  = 大模型当前正在输出的思考/回复（live.think / live.out）
//   播报  = 原样的日志行，图标按 kind 映射，**不换算成伤害数值**
//   计时  = 本轮开始时刻到现在的真实耗时 + 配置里的限时预算
// 刻意没有「回合蓝条」：每轮修复用掉几轮并没有流式计数可读，编一个等于骗人。
import { computed } from 'vue'
import { useDefect } from '../composables/useDefect.js'
import { useSettings } from '../composables/useSettings.js'

const { stageStates, lines, live, hasLive, running, runTarget, runStartedAt, runKind } = useDefect()
const { form } = useSettings()

const STAGE_LABELS = ['拉取', '定位', '生成提案', '闸门', '沉淀']
const TOTAL = STAGE_LABELS.length

/** 已打通的阶段数。active 不算打通 —— 血条要的是「还剩几关」，不是「碰过几关」 */
const doneCount = computed(() =>
  (stageStates.value || []).filter((s) => s === 'done').length)
const remaining = computed(() => TOTAL - doneCount.value)
const hp = computed(() => Math.max(0, Math.round((remaining.value / TOTAL) * 100)))
/** 卡在告警/失败上时血条变色，避免「血还很满但其实打不动」的错觉 */
const hpTone = computed(() => {
  const st = stageStates.value || []
  if (st.includes('fail')) return 'er'
  if (st.includes('warn')) return 'wn'
  return 'ok'
})

const target = computed(() => runTarget.value || {})
const isProbe = computed(() => runKind.value === 'probe')

/** 闸门形态 → 护盾。degraded 为真表示仓库没有可执行验收命令，只能走语法检查 */
const shield = computed(() => {
  const g = (form.value && form.value.gate) || {}
  const cmds = String(g.commands_text || '').split('\n').filter((x) => x.trim()).length
  if (g.degraded !== false || !cmds) return { tone: 'wn', text: '护盾：破甲（闸门降级为语法检查）' }
  return { tone: 'ok', text: `护盾：真跑闸门（${cmds} 条命令）` }
})

const budget = computed(() => {
  const a = (form.value && form.value.agent) || {}
  return Number(a.deadline_seconds) || 0
})
const elapsed = computed(() => {
  if (!running.value || !runStartedAt.value) return ''
  const sec = Math.max(0, Math.round((Date.now() - runStartedAt.value) / 1000))
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
})

/** 日志行 kind → 播报符号。文案一个字不改，只是换个更易扫的头部 */
const KIND_MARK = {
  ok: { cls: 'hit', glyph: '✓', word: '命中' },
  warn: { cls: 'deg', glyph: '!', word: '降级' },
  bad: { cls: 'miss', glyph: '✗', word: '失手' },
  head: { cls: 'turn', glyph: '▶', word: '目标' },
  dim: { cls: 'say', glyph: '·', word: '进展' },
}
const feed = computed(() => (lines.value || []).slice(-9).map((l) => ({
  ...l, m: KIND_MARK[l.kind] || KIND_MARK.dim,
})))

/** 施法条：大模型正在说什么。截断只为不撑爆版面，全文仍在控制台的 AI 流里 */
const casting = computed(() => {
  const t = (live.value.think || '').trim() || (live.value.out || '').trim()
  if (!t) return ''
  return t.length > 150 ? `${t.slice(0, 150)}…` : t
})
</script>

<template>
  <div class="battle" id="battle-view">
    <div class="bt-head">
      <div class="bt-boss">
        <div class="bt-tag">
          <span class="bt-mode">{{ isProbe ? '试运行 · 只定位' : '运行中' }}</span>
          <span class="bt-idx" v-if="target.total">本层第 {{ target.index }} / {{ target.total }} 只</span>
        </div>
        <div class="bt-name">
          <span class="bt-glyph">👹</span>
          <span class="bt-title">{{ target.title || '等待流水线报出第一个目标…' }}</span>
        </div>
        <div class="bt-id mono" v-if="target.id">{{ target.id }}</div>
        <div class="bt-hp" :class="hpTone">
          <i :style="{ width: hp + '%' }" />
          <em class="mono">剩余阶段 {{ remaining }}/{{ TOTAL }} · HP {{ hp }}</em>
        </div>
        <div class="bt-shield" :class="shield.tone">{{ shield.text }}</div>
      </div>

      <div class="bt-side">
        <div class="bt-stages">
          <span v-for="(lab, i) in STAGE_LABELS" :key="lab" class="bt-st"
                :class="(stageStates || [])[i] || 'idle'">{{ lab }}</span>
        </div>
        <div class="bt-timer mono">
          <span>已用 {{ elapsed || '—' }}</span>
          <span v-if="budget">限时 {{ Math.floor(budget / 60) }} 分</span>
        </div>
      </div>
    </div>

    <div class="bt-cast" :class="{ idle: !casting }">
      <span class="bt-cast-label">大模型正在输出</span>
      <span class="bt-cast-text">{{ casting || '（这一阶段没有流式文本，看下方播报）' }}</span>
    </div>

    <div class="bt-feed" id="battle-feed">
      <div v-for="(l, i) in feed" :key="i" class="bt-line" :class="l.m.cls">
        <span class="bt-g mono">{{ l.m.glyph }}</span>
        <span class="bt-w mono">{{ l.m.word }}</span>
        <span class="bt-t mono">{{ l.t }}</span>
        <span class="bt-x">{{ l.text }}</span>
      </div>
      <div v-if="!feed.length" class="bt-line empty">等待第一批事件…</div>
    </div>
  </div>
</template>
