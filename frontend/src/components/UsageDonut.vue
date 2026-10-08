<script setup>
// 模型占比环形图。旧版 app.js:3035 `usageDonut` 的口径复刻，绘制交给 ECharts：
// 带扫出动画、hover 扇区放大 + 阴影、跟随光标的 tooltip，点图例可临时隐藏某个模型。
//
// 与旧版的差异只有一处**实现**：单模型时不再需要「画整圈」的特判——旧版是因为
// 一条 arc 首尾重合会退化成空路径才分开写，ECharts 的饼图天然支持 100% 单扇区。
// 中心的总量文字保留成 HTML 覆盖层（`.usage-donut-total`），一是配色跟得上主题，
// 二是既有界面用例按这个 class 断文案。
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
// 与趋势图一致：ECharts 走异步 chunk，不并进入口 app.js
const EChart = defineAsyncComponent(() => import('./EChart.vue'))
import { fmtTok, fmtInt } from '../utils/format.js'
import { readChartColors, resolveColor } from '../utils/chartTheme.js'
import { useTheme } from '../composables/useTheme.js'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  palette: { type: Array, default: () => [] },
})

const { theme } = useTheme()

const CHART_H = 220

const wrap = ref(null)
const colors = ref(null)
/** 调色板解析结果（ECharts 只吃具体色值，var(--primary) 要先解析）。
 *  不能叫 palette：`<script setup>` 里同名顶层绑定会**遮蔽同名 prop**，
 *  模板里将来写 palette 就悄悄拿到解析后的色值数组而不是父组件传的词表。 */
const resolvedPalette = ref([])

function readColors() {
  if (!wrap.value) return
  colors.value = readChartColors(wrap.value)
  resolvedPalette.value = props.palette.map((c) => resolveColor(wrap.value, c))
}
onMounted(readColors)
watch(theme, readColors)
// 调色板来自 props，父组件不变就不变；跟着主题一起重算最省心
watch(() => props.palette, readColors, { deep: true })

const colorAt = (i) => resolvedPalette.value[i] || props.palette[i] || ''

const total = computed(() => props.rows.reduce((s, r) => s + r.total, 0))

/** 每个模型一行：tooltip 文案 + 占比（文案与旧版一致） */
const rows = computed(() =>
  props.rows.map((r, i) => {
    const pct = total.value ? ((r.total / total.value) * 100).toFixed(1) : '0.0'
    return {
      key: r.key,
      i,
      total: r.total,
      pct,
      tip: `${r.key}：${fmtInt(r.total)} tokens（${pct}%）`,
    }
  })
)

// 图例点击隐藏：保留原始下标 i（颜色要对得上图例），允许隐藏到只剩一个
const hidden = ref([])
function toggle(key) {
  if (hidden.value.includes(key)) {
    hidden.value = hidden.value.filter((k) => k !== key)
  } else if (rows.value.length - hidden.value.length > 1) {
    hidden.value = [...hidden.value, key]
  }
}

const visible = computed(() => rows.value.filter((r) => !hidden.value.includes(r.key)))

const option = computed(() => {
  if (!colors.value) return {}
  const c = colors.value
  return {
    animationType: 'scale',
    animationDuration: 700,
    tooltip: {
      trigger: 'item',
      confine: true,
      backgroundColor: c.panel,
      borderColor: c.axisLine,
      textStyle: { color: c.text, fontSize: 11.5 },
      formatter: (p) => {
        const r = visible.value[p.dataIndex]
        return r ? r.tip.replace(/\n/g, '<br/>') : ''
      },
    },
    series: [
      {
        id: 'models',
        type: 'pie',
        radius: ['48%', '76%'],
        center: ['50%', '50%'],
        startAngle: 90,          // 12 点方向起画，与旧版一致
        clockwise: true,
        avoidLabelOverlap: false,
        label: { show: false },  // 名称/占比走右侧自定义图例
        labelLine: { show: false },
        emphasis: {
          scale: true,
          scaleSize: 6,
          itemStyle: { shadowBlur: 12, shadowColor: 'rgba(0, 0, 0, 0.28)' },
        },
        data: visible.value.map((r) => ({
          name: r.key,
          value: r.total,
          itemStyle: { color: colorAt(r.i) },
        })),
      },
    ],
  }
})

/** 给界面用例读的语义化数据（EChart.vue 头部的 __probe 约定） */
const probe = computed(() => ({
  kind: 'donut',
  total: total.value,
  rows: rows.value,
  sectors: visible.value.length,
  hidden: [...hidden.value],
}))

defineExpose({ toggle, hidden })
</script>

<template>
  <div ref="wrap" class="usage-donut">
    <EChart :option="option" :height="CHART_H" :probe="probe" />
    <div class="usage-donut-center">
      <span class="usage-donut-total">{{ fmtTok(total) }}</span>
      <span class="usage-donut-cap">tokens</span>
    </div>
  </div>
</template>
