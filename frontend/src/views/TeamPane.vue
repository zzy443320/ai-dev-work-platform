<script setup>
// 长任务作业页签（#pane-team）。旧版 index.html:353-533 的骨架 +
// app.js:3136-3820 全部逻辑的等价迁移。
//
// 2026-10-15 第七轮改版（用户定稿）：
//   · 上半 **配置 | 运行监视** 左右两块，**等高、不可收起**；
//   · 下半 历史作业（产出物面板由 App.vue 挂在页签下方）**通栏**，不再分左右两列。
//   配置＝发起面板（说明 + 作业参数，参数轨 inline 嵌在说明下方）；
//   运行监视＝一整块「看板 / 中间段（时间线·工作项）/ 介入条」+ 角色输出浮层抽屉，
//   由 TeamCockpit 承载（为什么这么合、抽屉为何恢复，见其文件头）。
//
// 保留下来的 DOM 契约（界面用例按 id 找元素，顺序仍与旧版一致）：
//   #team-run-panel       配置（标题 / 范围 / 技术栈 / 轮次 / 描述 / 角色开关）
//   #team-cockpit-panel   运行监视（内含 #team-board-panel / #team-plan-panel /
//                         #team-timeline-panel / #team-intervene-panel 四段）
//   #team-history-panel   历史作业（可折叠）
//
// 与其它页签一样**必须解构 useTeam()**：`<script setup>` 只对顶层绑定做模板自动
// 解包，写 `const t = useTeam()` 再在模板里用 `t.task` 会拿到 ref 对象本身。
import { computed, onMounted, ref } from 'vue'
import { useTeam, RUN_STATUS, TEAM_ROLE_KEYS } from '../composables/useTeam.js'
import { useFold } from '../composables/useFold.js'
import { useParamModel } from '../composables/useParamModel.js'
import { relTime } from '../utils/format.js'
import ParamLayout from '../components/ParamLayout.vue'
import ParamRail from '../components/ParamRail.vue'
import ParamLocked from '../components/ParamLocked.vue'
import { paramModule } from '../composables/useParamSchema.js'
import TeamCockpit from '../components/TeamCockpit.vue'

const {
  roles, title, scope, framework, rounds, task, notes, roleOn,
  running, paused,
  start, intervene, askReplan, loadRoles,
  runs, runsError, loadRuns, openRun,
  ensure,
} = useTeam()

const { isCollapsed, toggleFold } = useFold()

/**
 * 作业参数（2026-10-14 第五轮起）：七条参数含任务描述长文本 → 嵌进**配置面板内部**
 * （说明文字下方，inline 模式，见模板内注释）。字段定义来自 /api/params/schema；
 * 读写落回 useTeam 的那几个 ref，start() 拼 payload 的逻辑一行没改。
 * 控件 id 沿用 #team-title / #team-task / #team-scope / #team-framework /
 * #team-rounds / #team-role-* / #team-notes。
 */
const params = useParamModel(
  { title, task, scope, framework, roleOn, rounds, notes })

const emit = defineEmits(['open-artifact'])

/** 角色格的显示名以后端 /api/team/roles 为准（emoji + 名字），schema 的 label 兜底。
    决策官锁死不可关，也在这里补上说明，免得界面只给一个点不动的开关。 */
function roleEmoji(id) {
  const r = roles.value.find((x) => x.id === id)
  return r ? r.emoji : ''
}
function roleName(id) {
  const r = roles.value.find((x) => x.id === id)
  return r ? r.name : id
}
function roleLabel(field, opt) {
  const named = roleName(opt.key)
  const base = named && named !== opt.key
    ? `${roleEmoji(opt.key) ? roleEmoji(opt.key) + ' ' : ''}${named}`
    : opt.label
  return opt.locked ? `${base}（始终参与）` : base
}

/* ---------------------------------------------------------------------------
 * 参数区 inline：作业参数**并入配置面板**
 * 说明下方那块空白原来一直空着（右侧独立参数轨把整行撑高），现在参数就放这里：
 * 空闲 = 可编辑的 ParamRail（开始作业 / 刷新历史在轨底动作条），
 * 跑动中同一位置换成「本批配置」定格卡。快照逻辑与 start() 一行没动。
 * ------------------------------------------------------------------------- */
const LOCK_NOTE = '流水线按启动时的参数推进，跑动中改这些值对这一批无效；本轮结束后表单恢复可编辑，改完就是下一批的配置。'
const paramTitle = computed(() => (paramModule('team') || {}).title || '作业参数')

/**
 * 本批配置定格：点「开始作业」的那一刻抓一份快照，跑动中用它替换可编辑表单。
 * 后端是按启动时的参数开跑的，跑动中再改界面上的表单对这一批无效 ——
 * 与其让人误以为改了会生效，不如把这一批到底用什么在跑明说。
 */
const batch = ref([])
const ROLE_SHORT = { planner: '决策官', coder: '编码工程师', tester: '测试工程师', reviewer: '复核官' }

/** 用户点了「开始作业」 */
function onStart() {
  const on = TEAM_ROLE_KEYS.filter((k) => roleOn.value[k])
    .map((k) => ROLE_SHORT[k] || k).join(' · ')
  batch.value = [
    { label: '任务标题', value: (title.value || '').trim() || '（未填）' },
    { label: '参与角色', value: on || '仅决策官' },
    { label: '改造范围', value: (scope.value || '').trim() || '不限，由决策官判断' },
    { label: '技术栈', value: framework.value || '未选' },
    { label: '最多返工', value: `${rounds.value ?? 0} 轮` },
    { label: '其他约定', value: (notes.value || '').trim() || '无' },
  ]
  start()
}

/** 介入入口（带确认的按旧版语义自己 toast） */
function onIntervene(kind) {
  if (kind === 'stop') {
    intervene('stop')
    return
  }
  intervene(kind)
}

/** 历史卡片的状态类名。旧版 teamRunCard 里 cls 的三分支 */
function runCls(x) {
  return x.status === 'done' ? 'applied' : (x.status === 'failed' ? 'apply_failed' : 'pending')
}
function runProg(x) {
  return x.item_total ? `${x.item_done}/${x.item_total} 项` : '—'
}
/** 历史卡片的 onClick —— 旧版是内联 onclick="openTeamRun('id')" */
function onRunClick(id) {
  openRun(id)
}

onMounted(async () => {
  await ensure()
})
</script>

<template>
  <ParamLayout name="team" :model="params" inline>
    <!-- ① 配置（左）：发起 + 作业参数。**不可收起** —— 它是这一页的输入口，
         收起来只会让人以为没地方填参数（用户 2026-10-15 定的口径）。 -->
    <section class="panel" id="team-run-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="7" r="3" /><circle cx="18" cy="7" r="3" /><circle cx="12" cy="18" r="3" /><path d="M9 7h6M8.3 9.4l2.5 6M15.7 9.4l-2.5 6" /></svg>
          长任务作业 · 多子 Agent 协作
        </h2>
      </div>
      <!-- 运行中控制（暂停 / 继续 / 重规划 / 跳过 / 终止）刻意**留在配置面板**：
           它们是跑起来之后对着右侧运行台用的运行中控制，不是输入参数。
           整行只在 running 时出现（空闲时五个禁用按钮是纯噪音）。 -->
      <div class="run-form team-run-actions" :class="{ hidden: !running }">
        <span class="scope-label">运行中干预</span>
        <el-button id="btn-team-pause" :disabled="!(running && !paused)" @click="onIntervene('pause')">暂停</el-button>
        <el-button id="btn-team-resume" :disabled="!(running && paused)" @click="onIntervene('resume')">继续</el-button>
        <el-button id="btn-team-replan" :disabled="!running" @click="askReplan()">要求重规划</el-button>
        <el-button id="btn-team-skip" :disabled="!running" @click="onIntervene('skip')">跳过当前项</el-button>
        <el-button type="danger" id="btn-team-stop" :disabled="!running" @click="onIntervene('stop')">终止</el-button>
      </div>
      <div class="info-note">
        把大任务拆给不同职责的子 Agent 去跑：<strong>决策官</strong>拆工作项定方案，<strong>编码工程师</strong>逐项实现，<strong>测试工程师</strong>补测试，<strong>复核官</strong>收口找问题。<br>
        跑的过程中右侧运行台会实时显示每个角色在做什么，你可以随时<strong>追问 / 纠偏 / 重规划 / 暂停 / 跳过 / 终止</strong>——注意模型调用无法中途打断，这些指令都在<strong>当前步骤结束后的安全边界</strong>生效，界面会如实标出。<br>
        全程<strong>不写目标仓库</strong>：结果汇总成一份产出物，人工采纳才写工作区（不 commit、不 push）。
      </div>

      <!-- 作业参数：空闲是可编辑轨（开始作业 / 刷新历史在轨底），
           跑动中同一位置换成「本批配置」定格卡。 -->
      <ParamRail v-if="!running" name="team" :model="params" :opt-label="roleLabel">
        <template #actions>
          <el-button type="primary" id="btn-team-run" :disabled="running" @click="onStart">开始作业</el-button>
          <el-button id="btn-team-history-refresh" @click="loadRuns()">刷新历史</el-button>
        </template>
      </ParamRail>
      <ParamLocked v-else :title="paramTitle" :items="batch" :note="LOCK_NOTE">
        <template #actions>
          <el-button type="primary" id="btn-team-run" :disabled="running" @click="onStart">开始作业</el-button>
          <el-button id="btn-team-history-refresh" @click="loadRuns()">刷新历史</el-button>
        </template>
      </ParamLocked>
    </section>

    <!-- ② 运行监视（右）：一整块卡 —— 看板 / 中间段（时间线·工作项）/ 介入条（+ 输出浮层抽屉） -->
    <TeamCockpit />

    <!-- ③ 历史作业（通栏）：计划、每个角色的动作轨迹与人工介入的留档 -->
    <section class="panel collapsible" id="team-history-panel"
             :class="{ collapsed: isCollapsed('team-history-panel') }">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.4" /><polyline points="3 4 3 9 8 9" /><path d="M12 7.5v5l3.5 2" /></svg>
          历史作业
          <span class="count-badge" :class="{ hidden: !runs.length }" id="team-history-count">{{ runs.length }}</span>
        </h2>
        <el-button text circle class="fold-btn" title="折叠 / 展开" @click="toggleFold('team-history-panel')">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
        </el-button>
      </div>
      <div id="team-history" class="proposal-grid">
        <div v-if="runsError" class="empty">历史作业加载失败: {{ runsError }}</div>
        <div v-else-if="!runs.length" class="empty">还没有作业记录。跑一次之后，计划、每个角色的动作轨迹与人工介入都会留在这里。</div>
        <!-- 历史作业卡同样要能键盘打开（理由见 ProposalCard 文件头那段注释） -->
        <el-card shadow="never" v-for="x in runs" :key="x.id" class="pcard" :class="`pc-${runCls(x)}`"
                 tabindex="0" role="button" :aria-label="`回放作业 ${x.title || x.id || ''}`"
                 @click="onRunClick(x.id)"
                 @keydown.enter.prevent="onRunClick(x.id)"
                 @keydown.space.prevent="onRunClick(x.id)">
          <div class="pcard-top">
            <span class="st" :class="`st-${runCls(x)}`">{{ RUN_STATUS[x.status] || x.status || '—' }}</span>
            <span v-if="x.running" class="team-running-chip">运行中</span>
            <span class="pcard-id">{{ x.id || '' }}</span>
            <span class="pcard-time">{{ relTime(x.updated || x.created) }}</span>
          </div>
          <div class="pcard-title">{{ x.title || '(无标题)' }}</div>
          <div class="pcard-meta">
            <span>工作项 {{ runProg(x) }}</span>
            <span>{{ x.file_count || 0 }} 个文件</span>
            <span>AI {{ x.ai_mode || '—' }}</span>
            <span v-if="x.interventions">人工介入 {{ x.interventions }} 次</span>
          </div>
          <div v-if="x.error" class="pcard-root" style="color:var(--danger)">{{ x.error }}</div>
          <div v-else-if="x.summary" class="pcard-root">{{ x.summary }}</div>
        </el-card>
      </div>
      <p class="panel-note">点开任意一次作业可以回看它的计划、每个角色的动作轨迹与人工介入记录（不含大模型逐字输出——那部分体量太大，不落盘）。</p>
    </section>
  </ParamLayout>
</template>
