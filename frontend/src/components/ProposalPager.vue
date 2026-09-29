<script setup>
// 提案分页条。旧版 app.js:185 `renderPropPager` 的等价复刻。
//
// 自绘分页 → <el-pagination>：props / emit 名字保持不变（沿用既有用例的契约）。
// 用 layout="prev, pager, next" 复刻原来的「上一页 / 页码 / 下一页」，
// .pg-info 文本节点保留，用例可能读它。
defineProps({
  info: { type: Object, required: true },   // { total, from, to }
  nums: { type: Array, default: () => [] }, // 保留契约：父组件仍会传，但 el-pagination 自带页码，不再手动拼
  current: { type: Number, default: 1 },
  totalPages: { type: Number, default: 1 }, // 保留契约：由 page-size 推导，不再手动拼
  // 每页条数必须是**常量**（父级 useDefect 的 PROP_PAGE_SIZE）。曾经用「当前页
  // 切片长度」推导：第 1 页 8 条 → 8；翻到末页只剩 6 条 → 6，于是总页数从 4 变
  // 5、末页「下一页」不再禁用，跟父级 totalPages 打架（check_vue_defect_ui
  // 第 5 步抓到过）。
  pageSize: { type: Number, default: 8 },
})

const emit = defineEmits(['goto'])
</script>

<template>
  <div class="prop-pager">
    <span class="pg-info">共 {{ info.total }} 条 · 第 {{ info.from }}-{{ info.to }} 条</span>
    <el-pagination
      layout="prev, pager, next"
      :current-page="current"
      :page-size="pageSize"
      :total="info.total"
      :hide-on-single-page="false"
      @current-change="(n) => emit('goto', n)"
    />
  </div>
</template>
