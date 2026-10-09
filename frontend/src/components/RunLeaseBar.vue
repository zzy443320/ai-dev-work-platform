<script setup>
// 流水线占用条：谁占着槽位、跑到哪一单、跑了多久，以及「停止 / 强制解除占用」。
//
// 它替代的现场是：界面只有一句「已有流水线在运行，请等待完成」，既看不出是哪条
// 工单（可能是别人 / 外部脚本 / 上一次刷新前留下的线程），也没有任何停止入口，
// 唯一出路是重启进程。占用信息现在来自 /api/run/status（见 useRunLease）。
//
// 两个按钮是两种语义，故意分开：
//   停止   —— 协作式，在阶段边界收口，不留半截沙箱与半截提案；
//   强制   —— 只解除占用（逃生口），后台线程仍会收尾并落它自己的提案。
import { useRunLease } from '../composables/useRunLease.js'

const { busy, leaseLine, leaseDetail, stopping, stopRun } = useRunLease()
</script>

<template>
  <div class="run-lease" :class="{ 'is-stopping': stopping }" id="run-lease"
       v-show="busy" :aria-live="'polite'">
    <span class="run-lease-dot" aria-hidden="true" />
    <div class="run-lease-text">
      <div class="run-lease-line">{{ leaseLine }}</div>
      <div class="run-lease-detail" v-if="leaseDetail">{{ leaseDetail }}</div>
    </div>
    <div class="run-lease-acts">
      <el-button size="small" id="btn-run-stop" :disabled="stopping"
                 @click="stopRun(false)"
                 title="在阶段边界收口：不砍正在跑的模型调用与沙箱命令，不留半截提案">
        {{ stopping ? '正在停止…' : '停止' }}
      </el-button>
      <el-button size="small" text id="btn-run-force" v-if="stopping"
                 @click="stopRun(true)"
                 title="逃生口：立刻解除占用好发起下一次。后台线程仍会收尾并落它自己的提案">
        强制解除占用
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.run-lease {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 12px; margin-bottom: 12px; border-radius: 10px;
  background: var(--warn-soft); color: var(--warn);
  border: 1px solid rgba(251, 191, 36, 0.3);
}
.run-lease.is-stopping { opacity: .82; }
.run-lease-dot {
  width: 8px; height: 8px; border-radius: 50%; flex: none; margin-top: 5px;
  background: currentColor; animation: run-lease-pulse 1.4s ease-in-out infinite;
}
@keyframes run-lease-pulse { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
.run-lease-text { flex: 1; min-width: 0; }
.run-lease-line { font-size: 12.5px; line-height: 1.6; font-weight: 600; }
.run-lease-detail {
  font-size: 12px; line-height: 1.55; color: var(--text-dim);
  overflow-wrap: anywhere;
}
.run-lease-acts { display: flex; align-items: center; gap: 4px; flex: none; }
@media (prefers-reduced-motion: reduce) {
  .run-lease-dot { animation: none; }
}
</style>
