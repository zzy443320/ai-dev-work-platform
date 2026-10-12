<template>
  <div class="stage-ring" :class="{ running, 'is-big': size >= 240 }" :style="ringStyle" aria-label="流水线阶段进度">
    <svg :viewBox="`0 0 ${size} ${size}`">
      <g :transform="`rotate(-90 ${size/2} ${size/2})`">
        <circle
          v-for="(seg, i) in segs" :key="i"
          :cx="size/2" :cy="size/2" :r="R" fill="none"
          :class="`sr-seg sr-${seg.state}`"
          :stroke-dasharray="`${seg.arc} ${C}`"
          :stroke-dashoffset="-seg.off"
        />
      </g>
    </svg>
    <div class="sr-center">
      <div class="sr-num mono">
        <template v-if="running || hasSignal">{{ doneCount }}<span class="sr-of">/5</span></template>
        <template v-else>—</template>
      </div>
      <div class="sr-label">{{ centerLabel }}</div>
    </div>
  </div>
</template>

<script>
/**
 * StageRing —— 缺陷修复驾驶舱的五段环形阶段盘。
 *
 * 数据只吃真实信号：states 就是 useDefect 的 stageStates（与 #stage-flow
 * 的 .stage 状态类同源），不自己推导、不预摆假状态。running 只影响中心
 * 文案与呼吸灯，不改变段颜色 —— 段颜色永远反映各阶段真实结果。
 *
 * 刻意不做百分比旋转动画：阶段完成是离散事件，一段一段亮起来比一条
 * 平滑假进度更诚实。
 */
import { STAGES } from './tabs.js'

export default {
  name: 'StageRing',
  props: {
    /** stageStates：done / process / warn / fail / off / idle */
    states: { type: Array, default: () => [] },
    running: { type: Boolean, default: false },
    /** 直径 px：驾驶舱主舞台用 260 大环（环是画面主角），窄容器可传小值 */
    size: { type: Number, default: 160 },
  },
  computed: {
    R() { return this.size * 0.45 },
    C() { return 2 * Math.PI * this.R },
    ringStyle() { return { width: this.size + 'px', height: this.size + 'px' } },
    segs() {
      const gap = this.C * 0.012
      const arc = this.C / 5 - gap
      return STAGES.map((s, i) => ({
        state: this.states[i] || 'idle',
        arc,
        off: i * (arc + gap),
      }))
    },
    doneCount() {
      return this.states.filter((s) => s === 'done').length
    },
    hasSignal() {
      // 跑过一次（有 done/失败/警告痕迹）才配显示分数，全 idle 就老实说没数据
      return this.states.some((s) => s !== 'idle' && s !== 'off')
    },
    activeName() {
      const i = this.states.indexOf('process')
      return i >= 0 ? STAGES[i].label : ''
    },
    centerLabel() {
      if (this.running && this.activeName) return `正在${this.activeName}`
      if (this.hasSignal) return '阶段完成'
      return '待命 · 未运行'
    },
  },
}
</script>

<style>
/* 样式放全局层：desk.css 的加载顺序保证这里的规则能落到最终产物。
   段颜色只认真实状态，idle/off 是同一档的「没到」。 */
.stage-ring { position: relative; flex: none; }
.stage-ring svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.sr-seg { stroke-width: 12; stroke-linecap: butt; }
.stage-ring.is-big .sr-seg { stroke-width: 15; }
.sr-done { stroke: var(--success); }
.sr-process {
  stroke: var(--ac);
  filter: drop-shadow(0 0 6px rgba(76, 195, 255, 0.5));
  animation: sr-breathe 2.2s ease-in-out infinite;
}
.sr-warn { stroke: var(--warn); }
.sr-fail { stroke: var(--danger); }
.sr-idle, .sr-off { stroke: var(--surf3); }
@keyframes sr-breathe { 50% { opacity: 0.55; } }
.sr-center {
  position: absolute; inset: 0; display: grid; place-content: center; text-align: center;
}
.sr-num { font-size: 30px; font-weight: 700; color: var(--text); letter-spacing: -0.02em; }
.stage-ring.is-big .sr-num { font-size: 54px; }
.stage-ring.is-big .sr-label { font-size: 13px; }
.sr-num :deep(.sr-of), .sr-num .sr-of { font-size: 15px; color: var(--txt3); margin-left: 2px; }
.sr-label { margin-top: 3px; font-size: 11px; color: var(--txt3); white-space: nowrap; }
</style>
