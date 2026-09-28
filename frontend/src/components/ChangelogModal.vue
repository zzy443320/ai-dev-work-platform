<script setup>
// 更新日志弹窗（#chmodal）。旧版 index.html:1201-1216 骨架 + renderChangelog。
// 按日期分组、分类 chips 过滤；条目 HTML 与旧版 renderChEntry 逐字节一致。
import { useChangelog } from '../composables/useChangelog.js'

const {
  visible, data, filter, chips, groups, footLeft,
  close, setFilter, renderChEntry, chWeekday,
} = useChangelog()
</script>

<template>
  <div id="chmodal" class="modal chmodal" :class="{ hidden: !visible }" @click.self="close()">
    <div class="modal-box chmodal-box" @click.stop>
      <div class="modal-head">
        <h3>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.4"/><polyline points="3 4 3 9 8 9"/><path d="M12 7.5v5l3.5 2"/></svg>
          更新日志
        </h3>
        <button class="close" @click="close()">×</button>
      </div>
      <div class="modal-meta ch-filters" id="ch-filters">
        <button v-for="c in chips" :key="c.slug" class="ch-chip" :class="{ active: filter === c.slug }" @click="setFilter(c.slug)">
          {{ c.name }}<span>{{ c.count }}</span>
        </button>
      </div>
      <div class="modal-body ch-body" id="ch-body">
        <div v-if="!data" class="empty">加载中…</div>
        <div v-else-if="data.offline" class="empty">读不到更新日志：{{ data.reason || '未知原因' }}
          <div class="ch-hint">若是 404，说明后端还是旧代码——重启 <code>python -m web.server</code> 后刷新页面即可。</div>
        </div>
        <div v-else-if="!groups.length" class="empty">该分类下暂无记录</div>
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
      <div class="ch-foot" id="ch-foot">
        <template v-if="data && !data.offline">
          <span>{{ footLeft }} · {{ groups.length }} 天</span>
          <span v-if="data.updated_at">日志文件更新于 {{ String(data.updated_at).replace('T', ' ').slice(0, 16) }}</span>
          <span>来源 {{ data.source || 'CHANGELOG.md' }}</span>
        </template>
      </div>
    </div>
  </div>
</template>
