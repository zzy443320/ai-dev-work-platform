<script setup>
// 更新日志弹窗（#chmodal）。旧版 index.html:1201-1216 骨架 + renderChangelog。
// 按日期分组、分类 chips 过滤；条目 HTML 与旧版 renderChEntry 逐字节一致。
//
// 组件已换成 <el-dialog>；分类筛选用 <el-check-tag>（就是「可点亮的筛选胶囊」）。
// 外层常驻 div 承载稳定 id 与 hidden 语义，原因见 KbModal.vue 的说明。
import { useChangelog } from '../composables/useChangelog.js'

const {
  visible, data, filter, chips, groups, footLeft,
  close, setFilter, renderChEntry, chWeekday,
} = useChangelog()
</script>

<template>
  <div id="chmodal" class="chmodal-host" :class="{ hidden: !visible }">
    <el-dialog
      class="chmodal-dialog"
      :model-value="visible"
      width="min(920px, 94vw)"
      top="6vh"
      :show-close="false"
      @update:model-value="close()"
      @close="close()"
    >
      <template #header>
        <div class="modal-head">
          <h3>
            <el-icon><Clock /></el-icon>
            更新日志
          </h3>
          <el-button class="close" text @click="close()">×</el-button>
        </div>
      </template>

      <div class="modal-meta ch-filters" id="ch-filters">
        <el-check-tag
          v-for="c in chips"
          :key="c.slug"
          class="ch-chip"
          :class="{ active: filter === c.slug }"
          :checked="filter === c.slug"
          @change="setFilter(c.slug)"
        >
          {{ c.name }}<span>{{ c.count }}</span>
        </el-check-tag>
      </div>

      <div class="modal-body ch-body" id="ch-body">
        <el-empty v-if="!data" description="加载中…" :image-size="70" />
        <el-empty v-else-if="data.offline" :image-size="70"
                  :description="`读不到更新日志：${data.reason || '未知原因'}`">
          <p class="ch-hint">若是 404，说明后端还是旧代码——重启 <code>python -m web.server</code> 后刷新页面即可。</p>
        </el-empty>
        <el-empty v-else-if="!groups.length" description="该分类下暂无记录" :image-size="70" />
        <template v-else>
          <div v-for="g in groups" :key="g.date" class="ch-day">
            <div class="ch-day-label">
              <span class="ch-day-date">{{ g.date }}</span>
              <span class="ch-day-week">{{ chWeekday(g.date) }}</span>
              <span class="ch-day-count">{{ g.entries.length }} 条</span>
            </div>
            <div class="ch-items" v-html="g.entries.map(renderChEntry).join('')" />
          </div>
        </template>
      </div>

      <template #footer>
        <div class="ch-foot" id="ch-foot">
          <template v-if="data && !data.offline">
            <span>{{ footLeft }} · {{ groups.length }} 天</span>
            <span v-if="data.updated_at">日志文件更新于 {{ String(data.updated_at).replace('T', ' ').slice(0, 16) }}</span>
            <span>来源 {{ data.source || 'CHANGELOG.md' }}</span>
          </template>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
#ch-body {
  max-height: 62vh;
  overflow: auto;
}
</style>
