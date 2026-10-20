<script setup>
// 需求开发页签（#pane-req）—— 2026-10-19 新建，2026-10-19 晚改为**完整流水线**。
//
// 第一版的教训：只做「分诊 + 影响面」，用户的原话是「我想要的是能把整个需求做完」。
// 分诊只是第一步，需求开发真正的工作量在于：找到真实文件 → 按锚点改代码 → 自检
// → 交出可采纳的补丁。所以这一页现在展示四个阶段：
//   triage     需求分诊   变更类型 / 要不要设计稿 / 影响面 / 验收标准
//   locate     定位代码   用仓库工具（grep/read/list）确认真实文件与锚点
//   implement  生成改动   逐文件产出 find/replace 或新文件，服务端预演通过才收下
//   selfcheck  自检       结合预演事实复核需求覆盖度，列出没做到的地方
//
// 产出物仍然落 artifacts，人工采纳才写工作区（不 commit、不 push）。
// 判「需要设计稿」时仍把需求带去「Figma 转代码」页——设计稿是输入，不是流程终点。
//
// ⚠️ 控件 id 前缀 rq-（需求），与 reqdev 的 req-* 分开：两页的参数都在 DOM 里，
// 同 id 会让界面用例与 getElementById 直接串页。
import { computed, onMounted, ref } from 'vue'
import { useTasks } from '../composables/useTasks.js'
import { useParamModel } from '../composables/useParamModel.js'
import { useReqHandoff } from '../composables/useReqHandoff.js'
import { useRunLease } from '../composables/useRunLease.js'
import { useToast } from '../composables/useToast.js'
import { useArtifacts } from '../composables/useArtifacts.js'
import ParamLayout from '../components/ParamLayout.vue'

const {
  reqDoc, reqFigma, reqScope, reqCodeContext, reqPlanNotes, reqDeliver,
  planResult, restorePlan, running, runTask,
  reqAtts, reqAttUploading, uploadReqAttachments, removeReqAttachment,
  REQ_ATT_ACCEPT, REQ_ATT_MAX,
} = useTasks()
const { send } = useReqHandoff()
const { run } = useRunLease()
const { toast } = useToast()
const { openArtifact } = useArtifacts()

// schema 里的 key 是 reqNotes，本页的 ref 叫 reqPlanNotes（避开 reqdev 的同名项）
const params = useParamModel({
  reqDoc, reqFigma, reqScope, reqCodeContext, reqNotes: reqPlanNotes,
  reqDeliver,
})

const CHANGE_LABEL = { modify: '改', create: '增', delete: '删' }
const CHECK_LABEL = { done: '自检通过', partial: '部分完成', blocked: '没做完' }

//: 与后端 REQ_STAGES 同序同 key；运行中用它渲染「当前卡在哪一步」
const STAGE_DEFS = [['triage', '需求分诊'], ['locate', '定位代码'],
  ['implement', '生成改动'], ['selfcheck', '自检']]

const hasPlan = computed(() => !!planResult.value)
const typeLabel = computed(() => planResult.value?.change_type_label || '未判定')
const impacts = computed(() => planResult.value?.impacts || [])
const endpoints = computed(() => planResult.value?.endpoints || [])
const files = computed(() => planResult.value?.files || [])
const unresolved = computed(() => planResult.value?.unresolved || [])
const gaps = computed(() => planResult.value?.gaps || [])
const verify = computed(() => planResult.value?.verify || [])
const check = computed(() => planResult.value?.selfcheck || {})
const checkLabel = computed(() => CHECK_LABEL[check.value.verdict] || '')
const needsDesign = computed(() => !!planResult.value?.needs_design)
// 「只改接口」与「混合」都要给接口联调的入口：混合里往往同时含接口改动
const showApi = computed(() => ['api_data', 'mixed'].includes(planResult.value?.change_type))
const docChars = computed(() => (reqDoc.value || '').trim().length)
const deliver = computed(() => reqDeliver.value || 'auto')

// ---- 需求文档附件（md / txt / pdf）----
// 上传即解析：PDF 的文字层由服务端抽出并回传预览，用户能当场确认「AI 读到的
// 是不是我要的那份内容」——扫描件在这里就会失败，而不是等流水线跑完才发现。
const fileInput = ref(null)
/** 当前展开预览的附件 id（一次只展开一份，避免整屏被文档正文占满） */
const openAtt = ref('')

function pickFiles() {
  if (running.value || reqAttUploading.value) return
  if (fileInput.value) fileInput.value.click()
}
function onFiles(e) {
  const files = e.target.files
  uploadReqAttachments(files).then(() => {
    // 清空 value：否则连续上传同一份文件不会再触发 change
    if (fileInput.value) fileInput.value.value = ''
  })
}
function toggleAtt(id) { openAtt.value = openAtt.value === id ? '' : id }
function attPreview(id) {
  const a = reqAtts.value.find((x) => x.id === id)
  return (a && a.preview) || ''
}
function formatSize(n) {
  const v = Number(n) || 0
  if (v >= 1048576) return `${(v / 1048576).toFixed(1)}MB`
  if (v >= 1024) return `${Math.round(v / 1024)}KB`
  return `${v}B`
}
function attKind(a) {
  const ext = String((a && a.ext) || '').toLowerCase()
  if (ext === 'pdf') return 'PDF'
  if (ext === 'md' || ext === 'markdown') return 'MD'
  return 'TXT'
}

/** 阶段条：运行中用租约里的实时 stage（后端 progress 推进），跑完用产出物里的结果 */
const displayStages = computed(() => {
  if (running.value && run.value.running) {
    const cur = String(run.value.stage || '')
    const idx = STAGE_DEFS.findIndex(([, label]) => label === cur)
    return STAGE_DEFS.map(([key, label], i) => ({
      key,
      label,
      status: idx < 0 ? 'pending' : (i < idx ? 'done' : (i === idx ? 'running' : 'pending')),
      detail: i === idx ? String(run.value.stage_detail || '') : '',
    }))
  }
  const rows = planResult.value?.stages || []
  if (!rows.length) return STAGE_DEFS.map(([key, label]) => ({ key, label, status: 'pending', detail: '' }))
  return rows
})
const showStages = computed(() => running.value || hasPlan.value)

/** 把需求原文与约定带到 Figma 转代码页（不传分诊结论：那是给人看的） */
function goDesign() {
  send({ requirement: reqDoc.value, notes: reqPlanNotes.value, source: reqFigma.value.trim() })
  if (window.switchTab) window.switchTab('reqdev')
  toast('已带上需求，去「Figma 转代码」填设计稿即可', 'ok')
}
function goApi() {
  if (window.switchTab) window.switchTab('apidebug')
  toast('已切到「接口联调」：贴接口文档即可', 'ok')
}

onMounted(restorePlan)
</script>

<template>
  <ParamLayout name="req" :model="params">
    <template #actions>
      <el-button type="primary" class="btn-primary" id="btn-req" :disabled="running"
                 @click="runTask('req')">
        {{ running ? '开发中…' : (deliver === 'triage' ? '生成方案'
          : (hasPlan ? '重新跑一遍' : '开始开发')) }}
      </el-button>
    </template>

    <section class="panel" id="rq-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6a1 1 0 0 1 1 1v1H8V4a1 1 0 0 1 1-1Z" /><rect x="5" y="5" width="14" height="16" rx="2" /><path d="M9 11h6M9 15h6M9 19h4" /></svg>
          需求开发 · 从需求到可采纳改动
        </h2>
        <span class="rq-v-meta mono" v-if="hasPlan">
          {{ [planResult.repo_file_count ? `仓库 ${planResult.repo_file_count} 个文件` : '',
              planResult.framework ? `技术栈 ${planResult.framework}` : '技术栈按仓库自动识别'].filter(Boolean).join(' · ') }}
        </span>
      </div>

      <!-- 四阶段进度条：运行中实时点亮（数据来自 /api/run/status 的 stage），
           跑完保留每个阶段的结论，便于回看「是定位没找到，还是改动没通过预演」。 -->
      <div class="rq-stages" id="rq-stages" :class="{ hidden: !showStages }">
        <div class="rq-stage-step" v-for="(s, i) in displayStages" :key="s.key"
             :class="`is-${s.status}`" :data-stage="s.key">
          <span class="rq-step-no">{{ s.status === 'done' ? '✓' : i + 1 }}</span>
          <span class="rq-step-label">{{ s.label }}</span>
          <span class="rq-step-detail">{{ s.detail }}</span>
        </div>
      </div>

      <!-- 需求原文回显：参数轨只有 294px，长需求在里面读不完；这里是能核对的全文。
           空态走 :empty::before 占位（与 #api-stage / #figma-tree 同一套约定）。 -->
      <div class="rq-stage">
        <div class="src-caption">
          <span class="src-title">需求原文（只读回显，AI 的判定依据）</span>
          <span class="src-state" :class="{ hidden: !running }">开发中…</span>
          <span class="src-state ok" :class="{ hidden: running || !docChars }">已贴入 {{ docChars }} 字符</span>
          <span class="pf-flex"></span>
          <button type="button" class="rq-att-add" id="btn-rq-att"
                  :disabled="running || reqAttUploading || reqAtts.length >= REQ_ATT_MAX"
                  :title="`支持 md / txt / pdf，一次最多 ${REQ_ATT_MAX} 份；PDF 会自动抽取文字`"
                  @click="pickFiles">
            {{ reqAttUploading ? '解析中…' : '上传需求文档' }}
          </button>
          <!-- 原生 file input：浏览器原生选择器比自绘弹层可靠，也便于界面用例
               set_input_files 直接喂文件（与 #btn-rq-att 的分工是「显示 / 触发」） -->
          <input ref="fileInput" id="rq-att-input" class="rq-att-input" type="file"
                 multiple :accept="REQ_ATT_ACCEPT" @change="onFiles">
        </div>

        <!-- 附件清单：名称 / 类型 / 大小 / 抽取字符数 + 可核对的解析预览。
             附件正文不塞进上面的 textarea —— 那是「人写的那几句约束」，
             两者混在一起既难改也难核对。运行时会由服务端合并成完整需求。 -->
        <div class="rq-atts" id="rq-atts" v-if="reqAtts.length">
          <div class="rq-att" v-for="a in reqAtts" :key="a.id">
            <span class="rq-att-kind" :class="`is-${a.ext || 'txt'}`">{{ attKind(a) }}</span>
            <code class="rq-att-name" :title="a.name">{{ a.name }}</code>
            <span class="rq-att-meta mono">
              {{ formatSize(a.size) }}<template v-if="a.chars"> · 抽出 {{ a.chars }} 字符</template>
            </span>
            <button type="button" class="rq-att-view" :data-att="a.id"
                    @click="toggleAtt(a.id)">{{ openAtt === a.id ? '收起' : '看解析结果' }}</button>
            <button type="button" class="rq-att-del" :disabled="running"
                    :title="`移除「${a.name}」`" @click="removeReqAttachment(a.id)">×</button>
          </div>
          <pre class="rq-att-preview" v-if="openAtt" id="rq-att-preview">{{ attPreview(openAtt) }}</pre>
        </div>

        <pre v-if="docChars" id="rq-doc-echo" class="log">{{ reqDoc }}</pre>
        <pre v-else id="rq-doc-echo" class="log"
             :data-placeholder="reqAtts.length
               ? `已上传 ${reqAtts.length} 份需求文档：正文由服务端在运行时读取并合并（点附件行的「看解析结果」可核对）。这里也可以再补几句你最在意的约束。`
               : '还没有需求。在右侧「需求参数」粘贴需求单正文或口述需求，也可以点右上角「上传需求文档」直接传 md / txt / pdf（只改接口字段、改数据映射的需求同样适用）。点「开始开发」后会依次分诊、定位代码、生成改动并自检。'" />
      </div>

      <!-- 分诊结论 + 实施结果。hidden 契约：常驻 DOM 只切 class。 -->
      <div class="rq-verdict" id="rq-verdict" :class="{ hidden: !hasPlan }">
        <div class="rq-v-head">
          <span class="rq-v-label">分诊结论</span>
          <el-tag id="rq-type" size="small" effect="light">{{ typeLabel }}</el-tag>
          <el-tag id="rq-design" size="small" effect="light"
                  :type="needsDesign ? 'warning' : 'info'">
            {{ needsDesign ? '需要设计稿' : '不需要设计稿' }}
          </el-tag>
          <el-tag v-if="checkLabel" id="rq-check-tag" size="small" effect="light"
                  :type="check.verdict === 'done' ? 'success' : (check.verdict === 'blocked' ? 'danger' : 'warning')">
            {{ checkLabel }}
          </el-tag>
          <span class="rq-v-meta mono">{{ impacts.length }} 个文件 · {{ endpoints.length }} 个接口 · {{ files.length }} 个可采纳文件</span>
        </div>

        <p class="rq-v-summary" id="rq-summary">{{ planResult && planResult.summary }}</p>

        <div class="rq-brief" v-if="needsDesign" id="rq-design-brief">
          <div class="rq-brief-title">给设计的需求说明</div>
          <pre class="rq-brief-text">{{ (planResult && planResult.design_brief) || '模型没有给出设计说明：可在需求原文里补「页面结构 / 状态 / 关键交互」，再跑一次。' }}</pre>
        </div>

        <div class="rq-impacts" id="rq-impacts" v-if="impacts.length">
          <div class="rq-impact" v-for="(it, i) in impacts" :key="i">
            <span class="rq-change" :class="`is-${it.change}`">{{ CHANGE_LABEL[it.change] || it.change }}</span>
            <code class="rq-path">{{ it.path }}</code>
            <span class="rq-reason">{{ it.reason }}</span>
            <span class="rq-missing" v-if="it.exists === false" title="AI 给的路径在仓库里找不到，可能是过时路径或新文件">仓库里没有</span>
            <span class="rq-fixed" v-else-if="it.corrected_from" :title="`原路径：${it.corrected_from}`">路径已校正</span>
          </div>
        </div>
      </div>

      <!-- 改动清单：预演通过的文件才出现在这里，带 +/- 行数与改动目的 -->
      <div class="rq-result" id="rq-files" :class="{ hidden: !files.length }">
        <div class="rq-result-head">
          <span class="rq-v-label">可采纳的改动</span>
          <span class="rq-v-meta mono">{{ files.length }} 个文件</span>
        </div>
        <div class="rq-file" v-for="f in files" :key="f.path">
          <span class="rq-change" :class="f.action === 'create' ? 'is-create' : 'is-modify'">
            {{ f.action === 'create' ? '新' : '改' }}
          </span>
          <code class="rq-path">{{ f.path }}</code>
          <span class="rq-reason">{{ f.description }}</span>
          <span class="rq-delta" v-if="f.stats && (f.stats.added || f.stats.removed)">
            <b class="add">+{{ f.stats.added }}</b><b class="del">-{{ f.stats.removed }}</b>
          </span>
          <span class="rq-delta mono" v-else-if="f.edits">{{ f.edits }} 处</span>
        </div>
        <div class="rq-file is-failed" v-for="u in unresolved" :key="`u-${u.path}`">
          <span class="rq-change is-delete">失败</span>
          <code class="rq-path">{{ u.path }}</code>
          <span class="rq-reason">{{ u.error }}</span>
        </div>
      </div>

      <!-- 自检与待确认项：把「没做成的部分」放在明面上，而不是让用户以为全好了 -->
      <div class="rq-check" id="rq-check" :class="{ hidden: !check.verdict }">
        <div class="rq-check-head">
          <span class="rq-v-label">自检</span>
          <span class="rq-check-badge" :class="`is-${check.verdict}`">{{ checkLabel }}</span>
        </div>
        <ul class="rq-check-list" v-if="(check.problems || []).length">
          <li v-for="(p, i) in check.problems" :key="`p-${i}`">{{ p }}</li>
        </ul>
        <ul class="rq-check-list is-follow" v-if="(check.followups || []).length">
          <li v-for="(f, i) in check.followups" :key="`f-${i}`">采纳后：{{ f }}</li>
        </ul>
        <div class="rq-check-sub" v-if="gaps.length">
          <span class="rq-check-sub-title">需要确认</span>
          <ul class="rq-check-list">
            <li v-for="(g, i) in gaps" :key="`g-${i}`">{{ g }}</li>
          </ul>
        </div>
        <div class="rq-check-sub" v-if="verify.length">
          <span class="rq-check-sub-title">验证方式</span>
          <ul class="rq-check-list is-mono">
            <li v-for="(v, i) in verify" :key="`v-${i}`">{{ v }}</li>
          </ul>
        </div>
      </div>

      <div class="rq-next">
        <el-button v-if="hasPlan && planResult.id" type="primary" class="btn-primary"
                   id="btn-rq-artifact" :disabled="!files.length"
                   @click="openArtifact(planResult.id)">
          审阅改动并采纳
        </el-button>
        <el-button v-if="needsDesign" class="btn-ghost" id="btn-goto-reqdev"
                   @click="goDesign">带需求去 Figma 转代码</el-button>
        <el-button v-if="showApi" class="btn-ghost" id="btn-goto-apidebug" @click="goApi">去接口联调</el-button>
        <span class="rq-next-note" v-if="files.length">采纳前可逐行看 diff；采纳只写工作区，不 commit、不 push</span>
        <span class="rq-next-note" v-else-if="hasPlan && deliver !== 'triage'">本次没有可采纳改动：看上面的自检与「需要确认」</span>
      </div>

      <div class="info-note">「自动完成」会走完整四步：<br>① 需求分诊 → 判变更类型、要不要设计稿；技术栈由 AI 读仓库依赖清单自动识别，不用手选；<br>② 定位代码 → 在仓库里找真实文件与改动锚点；<br>③ 生成改动 → 逐文件产出 find/replace，服务端预演匹配通过才收下；<br>④ 自检 → 复核需求覆盖度，列出没做到的地方。<br>填了 Figma 链接会自动拉取设计稿作为视觉依据（拉取失败不阻断，记进「需要确认」）；需要设计稿时也支持一键带到「Figma 转代码」；产出物人工采纳才写工作区。</div>
    </section>
  </ParamLayout>
</template>
