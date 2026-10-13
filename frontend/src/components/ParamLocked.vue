<script setup>
// 跑动中的**本批配置**定格卡。
//
// 为什么要有它：参数改成常驻控件条 / 参数轨之后，出现一个改版前不存在的歧义 ——
// 流水线正在跑，界面上那份表单还是可编辑的，但改「参与角色 / 返工轮次 / 最多处理」
// 对这一批完全无效（后端早就按启动时的参数开跑了）。以前点运行会收起抽屉，
// 等于替你把这个歧义藏住了；藏住不等于没有。
//
// 口径（用户 2026-10-13 定档）：**跑动中把参数定格成只读摘要**，说清这一批用的是什么、
// 现在改无效；跑完自动回到可编辑表单（表单里的值本来就是这一批的值，不另存历史）。
// 数据全部由调用方在**点运行的那一刻**抓的快照，不是跑完之后回填的。
defineProps({
  /** 卡片标题，取 schema 的 module.title（作业参数 / 运行参数…） */
  title: { type: String, required: true },
  /** 定格项：[{ label, value }] */
  items: { type: Array, default: () => [] },
  /** 一句话说明「为什么现在改不了、什么时候能改」 */
  note: { type: String, default: '' },
})
</script>

<template>
  <aside class="plock pcol" :aria-label="`${title}（本批定格）`">
    <div class="plock-head">
      <span class="plock-live" aria-hidden="true" />
      <b>{{ title }}</b>
      <span class="plock-tag">本批已定格</span>
    </div>
    <dl class="plock-list">
      <div v-for="it in items" :key="it.label" class="plock-row">
        <dt>{{ it.label }}</dt>
        <dd :title="String(it.value)">{{ it.value }}</dd>
      </div>
    </dl>
    <div v-if="note" class="plock-note">{{ note }}</div>
    <slot name="actions" />
  </aside>
</template>
