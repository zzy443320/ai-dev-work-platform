<script setup>
// Boss 战场视图 —— 运行中**整屏接管**工作区，跑完（或手动退出）自动退回控制台日志。
//
// 为什么是整屏而不是日志区里的一条窄带：流水线跑起来时人在等的就三件事 ——
// 打到哪了、还要多久、有没有卡住。窄带里塞不下「Boss 名牌 + 阶段条 + 我方单位 +
// 播报」这四组信息，硬塞的结果就是谁都不显眼，等于把日志换了个配色。
//
// ⚠️ 每个数字都必须挂在真实信号上，不许编：
//   HP      = 5 个阶段里还没打通的占比（stageStates，与顶部阶段细轨同源）。
//             active 不算打通 —— 血条要的是「还剩几关」，不是「碰过几关」。
//             出现 fail/warn 时血条变色，避免「血还很满但其实打不动」的错觉。
//   目标    = defect 事件的 index/total/title（useDefect.runTarget）。
//   护盾环  = 闸门形态。gate.degraded 为真 → 破甲（画成断裂的琥珀色环）。
//   弱点栏  = 大模型此刻的 reasoning 流（live.think）。跑动中根因还没落定，
//             所以这里显示的是「正在成形中的判断」，不预先摆几个假标签。
//   播报    = 原样的日志行，只按 kind 换图标（ok 命中 / bad 失手 / warn 降级 / head 目标），
//             文案一个字不改，也不换算成伤害数值。
//   计时    = 本轮开始时刻到现在的真实耗时；限时预算读配置 agent.deadline_seconds。
// 刻意没有「回合蓝条」：每轮修复实际用掉几轮并没有流式计数可读，凭空画一条 MP 就是骗人。
import { computed, ref, watch } from 'vue'
import { useDefect } from '../composables/useDefect.js'
import { useSettings } from '../composables/useSettings.js'
import { useRunLease } from '../composables/useRunLease.js'

const { stageStates, lines, live, hasLive, running, runTarget, runStartedAt, runKind, skipVerify } = useDefect()
const { form } = useSettings()
const { stopRun, stopping } = useRunLease()

const STAGE_LABELS = ['拉取', '定位', '生成提案', '闸门', '沉淀']
const TOTAL = STAGE_LABELS.length

/** 手动退出战场（本轮内有效）：不想被接管的人有个出口，下次运行会再进来 */
const dismissed = ref(false)
watch(running, (v) => { if (v) dismissed.value = false })
const show = computed(() => running.value && !dismissed.value)

const doneCount = computed(() =>
  (stageStates.value || []).filter((s) => s === 'done').length)
const remaining = computed(() => TOTAL - doneCount.value)
const hp = computed(() => Math.max(0, Math.round((remaining.value / TOTAL) * 100)))
const hpTone = computed(() => {
  const st = stageStates.value || []
  if (st.includes('fail')) return 'er'
  if (st.includes('warn')) return 'wn'
  return 'ok'
})
/** 当前正在打的那一关（active）。没有就是还没开跑或已经收口 */
const curStage = computed(() => {
  const st = stageStates.value || []
  const i = st.indexOf('active')
  return i >= 0 ? STAGE_LABELS[i] : ''
})

const target = computed(() => runTarget.value || {})
const isProbe = computed(() => runKind.value === 'probe')

const cfg = computed(() => (form && form.agent) || {})
const gateCfg = computed(() => (form && form.gate) || {})
const gateCmds = computed(() =>
  String(gateCfg.value.commands_text || '').split('\n').filter((x) => x.trim()).length)
/** 破甲 = 没有真跑闸门命令，只能走降级语法检查。护盾环因此画成断口 */
const broken = computed(() => gateCfg.value.degraded !== false || !gateCmds.value)

/** 我方三个单位。状态全部来自配置与开关，不猜 */
const party = computed(() => {
  const sandboxOn = (cfg.value.sandbox || 'auto') !== 'off'
  const reproOn = (cfg.value.repro || 'auto') !== 'off'
  const shotOn = !skipVerify.value
  return [
    { key: 'ai', glyph: '🧙', name: 'AI Fix Agent', role: `${(form.ai || {}).model || '未配置'} · 主力输出`,
      state: hasLive.value ? '施法中' : '待命', tone: hasLive.value ? 'cast' : 'idle',
      bar: hasLive.value ? 'cast' : 'ready' },
    { key: 'sandbox', glyph: '🧪', name: '沙箱副本', role: 'worktree + 仓库外真跑验收',
      state: sandboxOn ? '已挂载' : '未启用', tone: sandboxOn ? 'ok' : 'off' },
    { key: 'shots', glyph: '📷', name: '页面截图验收', role: '浏览器打开被测页面',
      state: shotOn ? '参战' : '本次跳过', tone: shotOn ? 'ok' : 'off' },
    { key: 'repro', glyph: '🎯', name: '复现用例', role: '先在未修复代码上跑红',
      state: reproOn ? '先手待命' : '未启用', tone: reproOn ? 'ok' : 'off' },
  ]
})
const budget = computed(() => Number(cfg.value.deadline_seconds) || 0)
const elapsed = computed(() => {
  if (!running.value || !runStartedAt.value) return '00:00'
  const sec = Math.max(0, Math.round((Date.now() - runStartedAt.value) / 1000))
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
})

const KIND_MARK = {
  ok: { cls: 'hit', glyph: '✓', word: '命中' },
  warn: { cls: 'deg', glyph: '!', word: '降级' },
  bad: { cls: 'miss', glyph: '✗', word: '失手' },
  head: { cls: 'turn', glyph: '▶', word: '目标' },
  dim: { cls: 'say', glyph: '·', word: '进展' },
}
const feed = computed(() => (lines.value || []).slice(-14).map((l) => ({
  ...l, m: KIND_MARK[l.kind] || KIND_MARK.dim,
})))

const weakness = computed(() => {
  const t = (live.value.think || live.value.out || '').trim()
  if (!t) return '弱点尚未暴露 —— 等第一条 reasoning 流过来'
  return t.length > 260 ? `${t.slice(0, 260)}…` : t
})

async function onStop() { await stopRun(false) }
</script>

<template>
  <Transition name="bs">
    <div v-if="show" class="battle-stage" id="battle-view">
      <!-- ── 顶：Boss 名牌 + 血条 + 五段阶段条 ── -->
      <header class="bs-bossbar">
        <div class="bs-bb-l">
          <div class="bs-bname">
            <span class="bs-lv mono">{{ isProbe ? 'PROBE · 只定位' : 'RUN · 全流程' }}</span>
            <h2>{{ target.title || '等待流水线报出第一个目标…' }}</h2>
            <span class="bs-sub mono" v-if="target.id">{{ target.id }}
              <template v-if="target.total"> · 本层 {{ target.index }}/{{ target.total }}</template></span>
          </div>
          <div class="bs-hp" :class="hpTone">
            <i :style="{ width: hp + '%' }" />
            <em class="mono">剩余阶段 {{ remaining }}/{{ TOTAL }} · HP {{ hp }}
              <template v-if="curStage"> · 正在打「{{ curStage }}」</template></em>
          </div>
        </div>
        <div class="bs-bb-r">
          <div class="bs-pips">
            <span v-for="(lab, i) in STAGE_LABELS" :key="lab" class="bs-pip"
                  :class="(stageStates || [])[i] || 'idle'" :title="lab">
              <b class="bs-pd"><span>{{ (stageStates || [])[i] === 'done' ? '✓' : i + 1 }}</span></b>
              <u>{{ lab }}</u>
            </span>
          </div>
          <div class="bs-clock mono">
            <span>已用 {{ elapsed }}</span><span v-if="budget"> / 限时 {{ Math.floor(budget / 60) }} 分</span>
          </div>
        </div>
      </header>

      <div class="bs-mid">
        <!-- ── 左：我方单位 ── -->
        <aside class="bs-party">
          <div class="bs-ucap">我方单位</div>
          <div v-for="u in party" :key="u.key" class="bs-uc" :class="u.tone">
            <div class="bs-uc-h">
              <span class="bs-av">{{ u.glyph }}</span>
              <div class="bs-uc-n"><b>{{ u.name }}</b><i>{{ u.role }}</i></div>
              <span class="bs-uc-s">{{ u.state }}</span>
            </div>
            <div v-if="u.key === 'ai'" class="bs-cast" :class="{ off: !hasLive }">
              <i /><em>{{ hasLive ? '正在输出 reasoning / 回复…' : '待机' }}</em>
            </div>
          </div>
        </aside>

        <!-- ── 中：Boss 立绘 + 法阵 + 护盾 + 弱点 ── -->
        <section class="bs-arena">
          <div class="bs-boss">
            <div class="bs-ring r1" /><div class="bs-ring r2" /><div class="bs-ring r3" />
            <div class="bs-shield" :class="{ broken }" />
            <div class="bs-glyph">👹</div>
          </div>
          <div class="bs-shieldline" :class="broken ? 'wn' : 'ok'">
            {{ broken ? '护盾：破甲 —— 闸门降级为语法检查' : '护盾：真跑闸门（' + gateCmds + ' 条命令）' }}
          </div>
          <div class="bs-weak">
            <div class="bs-weak-h">弱点分析中 · WEAKPOINT FORMING</div>
            <p class="bs-weak-t">{{ weakness }}</p>
          </div>
        </section>

        <!-- ── 右：战斗播报 ── -->
        <aside class="bs-feedwrap">
          <div class="bs-ucap">战斗播报<span class="bs-live" :class="{ off: !running }">LIVE</span></div>
          <div class="bs-feed" id="battle-feed">
            <div v-for="(l, i) in feed" :key="i" class="bs-line" :class="l.m.cls">
              <span class="bs-g">{{ l.m.glyph }}</span>
              <span class="bs-w mono">{{ l.m.word }}</span>
              <span class="bs-t mono">{{ l.t }}</span>
              <span class="bs-x" :title="l.text">{{ l.text }}</span>
            </div>
            <div v-if="!feed.length" class="bs-line empty">等待第一批事件…</div>
          </div>
        </aside>
      </div>

      <!-- ── 底：操作栏 ── -->
      <footer class="bs-hotbar">
        <button type="button" class="bs-k" id="btn-battle-stop" :disabled="stopping" @click="onStop">
          <span class="bs-kk mono">S</span><span class="bs-ki">■</span>
          <span class="bs-kl">{{ stopping ? '正在停止…' : '停止运行' }}</span>
          <span class="bs-kd mono">api/run/cancel</span>
        </button>
        <button type="button" class="bs-k" id="btn-battle-exit" @click="dismissed = true">
          <span class="bs-kk mono">Esc</span><span class="bs-ki">⤺</span>
          <span class="bs-kl">退出战场看日志</span>
          <span class="bs-kd mono">流水线继续跑</span>
        </button>
        <div class="bs-hint mono">停止是协作式的：在阶段边界收口，不留半截沙箱与半截提案</div>
      </footer>
    </div>
  </Transition>
</template>
