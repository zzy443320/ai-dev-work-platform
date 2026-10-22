<script setup>
// 自测页签（#pane-selftest，2026-10-21）：开发完需求后的自测环节。
//
// 三条链路（测试用例 / 需求一致性 / 设计稿一致性）共用一次浏览器采证，
// 产出**逐条判定表**而不是代码补丁 —— 这是它独立成页签的理由：
// 代码测试输出测试文件、缺陷修复输出补丁，两者都不是「这份实现到底对不对」的报告。
//
// 判定三态（pass / fail / unknown）直接来自后端，前端**不做任何美化性改写**：
// 把 unknown 显示成「通过」会让整份报告失去意义。
//
// 参数形态：8 个字段 + 长文本 → schema 分档判给常驻参数轨（rail）。
// 控件 id 由 web/params_schema.py 定义（#st-issue / #st-lanes / #st-doc …）；
// 本页按钮 id：#btn-selftest / #st-btn-cases；结果区 id：见模板。
import { computed, onMounted, ref } from 'vue'
import { useSelfTest } from '../composables/useSelfTest.js'
import { useParamModel } from '../composables/useParamModel.js'
import { elapsedText } from '../composables/useRunLease.js'
import ParamLayout from '../components/ParamLayout.vue'

const {
  stIssue, stUrl, stRoutes, stLanes, stCases, stReqDoc, stFigma, stLimit,
  stAtts, stAttUploading, ST_ATT_MAX, ST_ATT_ACCEPT,
  running, pulling, pulled, error, result,
  stage, stageDetail, elapsed,
  pullCases, runSelfTest, restore,
  uploadReqDocs, removeReqDoc, attSize,
} = useSelfTest()

const params = useParamModel({
  stIssue, stUrl, stRoutes, stLanes, stCases, stReqDoc, stFigma, stLimit,
})

onMounted(restore)

/** 展开查看某份附件的解析正文（核对 PDF 到底抽出了什么） */
const openAtt = ref('')
function toggleAtt(id) { openAtt.value = openAtt.value === id ? '' : id }
function attPreview(id) {
  const a = stAtts.value.find((x) => x.id === id)
  return (a && a.preview) || ''
}
function attKind(a) {
  const ext = String((a && a.ext) || '').toLowerCase()
  if (ext === 'pdf') return 'PDF'
  if (ext === 'md' || ext === 'markdown') return 'MD'
  return 'TXT'
}

/** 原生 file input：比自绘选择器可靠，界面用例也能直接 set_input_files */
const fileInput = ref(null)
function pickDocs() { if (fileInput.value) fileInput.value.click() }
function onFiles(e) {
  uploadReqDocs(e.target.files).then(() => {
    // 清空 value：否则连续上传同一份文件不会再触发 change
    if (fileInput.value) fileInput.value.value = ''
  })
}

const STATUS_LABEL = { pass: '通过', fail: '不通过', unknown: '无法判断' }
const LANE_LABEL = { case: '测试用例', req: '需求一致性', figma: '设计稿一致性' }

const verdicts = computed(() => (result.value && result.value.verdicts) || [])
const counts = computed(() => (result.value && result.value.counts) || {})
const laneRows = computed(() => (result.value && result.value.lanes) || [])
const env = computed(() => (result.value && result.value.environment) || {})
const notes = computed(() => (result.value && result.value.notes) || [])

/** 采到空白页的路由：这类结论最容易被质疑「我本地明明是好的」，
    所以在环境摘要里直接给出工具看到的落点与截图入口。 */
const blankPages = computed(() =>
  ((env.value.pages || [])).filter((p) => p && p.blank))
function shortUrl(u) {
  const s = String(u || '')
  return s.length > 46 ? s.slice(0, 46) + '…' : s
}

/** 参数定格卡：跑起来时主区还在变，参数不该还能改 */
const lockedItems = computed(() => [
  { label: '环境', value: stUrl.value || '（未填）' },
  { label: '检查项', value: ['case', 'req', 'figma']
    .filter((k) => (stLanes.value || {})[k]).map((k) => LANE_LABEL[k]).join(' / ') || '（未选）' },
  { label: '工单', value: stIssue.value || '—' },
  { label: '需求文档', value: stAtts.value.length
    ? `已上传 ${stAtts.value.length} 份（${stAtts.value.map((a) => a.name).join('、')}）`
    : (stReqDoc.value.trim() ? '手贴正文' : '—') },
])

/** 证据截图：后端存的是绝对路径，静态服务挂在 /screenshots 下 */
function shotUrl(path) {
  const name = String(path || '').split(/[\\/]/).pop()
  return name ? `/screenshots/${encodeURIComponent(name)}` : ''
}

// ---- 实时过程区 ------------------------------------------------------------
// 运行中：阶段时间线（后端 progress 镜像进运行租约，2s 轮询内存读）；
// 结果回来：采证截图缩略条 + 拆分清单。与「Figma 转代码」同构：右轨参数、
// 左侧过程可见。
const STAGES = ['采证', '拆解', '判定']
const stageIdx = computed(() => {
  const i = STAGES.indexOf(String(stage.value || ''))
  return i
})
/** 环境采到的页面截图（去重：同一截图只显示一次） */
const evidenceShots = computed(() => {
  const seen = new Set()
  const rows = []
  for (const p of (env.value.pages || [])) {
    const u = shotUrl(p && p.screenshot)
    if (!u || seen.has(u)) continue
    seen.add(u)
    rows.push({ url: u, route: (p && p.route) || '', blank: !!(p && p.blank),
                chars: (p && p.chars) || 0, settled: (p && p.settled_ms) || 0 })
  }
  return rows
})
/** 拆分出的检查项（判定表的标题列单独抽出来当「拆分清单」看） */
const splitItems = computed(() => {
  const by = { case: [], req: [], figma: [] }
  for (const v of verdicts.value) {
    if (by[v.lane]) by[v.lane].push(v.title)
  }
  return by
})
</script>

<template>
  <ParamLayout name="selftest" :model="params" :locked="running" :locked-items="lockedItems"
               locked-note="自测跑完约 1–3 分钟（采证 + 三条链路判定）；期间参数只读，跑完即可改。">
    <template #actions>
      <el-button class="btn-ghost" id="st-btn-doc"
                 :disabled="running || stAttUploading || stAtts.length >= ST_ATT_MAX"
                 :title="`支持 md / txt / pdf，一次最多 ${ST_ATT_MAX} 份；PDF 会自动抽取文字`"
                 @click="pickDocs">{{ stAttUploading ? '解析中…' : '上传需求文档' }}</el-button>
      <el-button class="btn-ghost" id="st-btn-cases" :disabled="pulling || running"
                 title="从 ONES 工单「测试情况」下拉关联用例（只预览，不判定）"
                 @click="pullCases">{{ pulling ? '拉取中…' : '拉取用例' }}</el-button>
      <el-button type="primary" class="btn-primary" id="btn-selftest" :disabled="running"
                 @click="runSelfTest">{{ running ? '自测中…' : '开始自测' }}</el-button>
    </template>

    <!-- 附件区挂在参数轨的 notes 位：它讲的就是「需求文档这个参数怎么给」。
         原生 file input 常驻 DOM（只是视觉隐藏），界面用例能直接 set_input_files。 -->
    <template #notes>
      <div class="st-atts" id="st-atts" v-if="stAtts.length">
        <div class="st-att" v-for="a in stAtts" :key="a.id">
          <span class="st-att-kind" :class="`is-${(a.ext || 'txt').toLowerCase()}`">{{ attKind(a) }}</span>
          <code class="st-att-name" :title="a.name">{{ a.name }}</code>
          <span class="st-att-meta mono">
            {{ attSize(a.size) }}<template v-if="a.chars"> · {{ a.chars }} 字符</template>
          </span>
          <button type="button" class="st-att-view" :data-att="a.id"
                  @click="toggleAtt(a.id)">{{ openAtt === a.id ? '收起' : '看解析' }}</button>
          <button type="button" class="st-att-del" :disabled="running"
                  :title="`移除「${a.name}」`" @click="removeReqDoc(a.id)">×</button>
        </div>
        <pre class="st-att-preview" v-if="openAtt" id="st-att-preview">{{ attPreview(openAtt) }}</pre>
      </div>
      <div class="st-att-hint" v-if="!stAtts.length">
        需求文档可以直接传文件：md / txt / pdf（PDF 上传时就抽出文字，扫描件没有文字层会当场提示）。
      </div>
      <input ref="fileInput" id="st-att-input" class="st-att-input" type="file"
             multiple :accept="ST_ATT_ACCEPT" @change="onFiles" />
    </template>

    <section class="panel" id="st-panel">
      <div class="panel-head">
        <h2>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>
          自测 · 用例 / 需求 / 设计稿
        </h2>
        <span v-if="result" class="st-sum" id="st-summary">{{ result.summary }}</span>
      </div>

      <div v-if="error" class="st-error" id="st-error">{{ error }}</div>

      <!-- ── 实时过程区：运行中看阶段，跑完看采证截图与拆分清单 ── -->
      <div v-if="running" class="st-process" id="st-process">
        <div class="st-steps">
          <span v-for="(s, i) in STAGES" :key="s" class="st-step"
                :class="{ done: stageIdx > i, active: stageIdx === i }">
            <i>{{ i + 1 }}</i>{{ s }}
          </span>
          <span class="st-step live"><i>✓</i>汇总</span>
        </div>
        <div class="st-process-detail">
          <span class="mono">{{ elapsedText(elapsed) }}</span>
          <span v-if="stageDetail">{{ stageDetail }}</span>
          <span v-else class="st-dim">正在启动自测浏览器…（重型页面首屏采证约需 20–40 秒）</span>
        </div>
      </div>

      <template v-if="evidenceShots.length">
        <div class="src-caption">
          <span class="src-title">采证截图（自测浏览器实际看到的页面）</span>
          <span class="src-state ok">{{ evidenceShots.length }} 张</span>
        </div>
        <div class="st-shots" id="st-shots">
          <a v-for="s in evidenceShots" :key="s.url" class="st-shot" :href="s.url"
             target="_blank" rel="noopener" :title="`路由 ${s.route} · ${s.chars} 字符`">
            <img :src="s.url" alt="" loading="lazy" />
            <span class="st-shot-meta">
              <code>{{ s.route }}</code>
              <span :class="s.blank ? 'bad' : 'ok'">{{ s.blank ? '空白' : `${s.chars} 字符` }}</span>
            </span>
          </a>
        </div>
      </template>

      <div v-if="verdicts.length" class="st-counts" id="st-counts">
        <span class="st-chip pass">通过 {{ counts.pass || 0 }}</span>
        <span class="st-chip fail">不通过 {{ counts.fail || 0 }}</span>
        <span class="st-chip unknown">无法判断 {{ counts.unknown || 0 }}</span>
        <span v-for="l in laneRows" :key="l.lane" class="st-chip lane" :class="{ bad: l.fail }">
          {{ l.label }} {{ l.pass }}/{{ l.total }}
        </span>
      </div>

      <div v-if="env.url" class="st-env" id="st-env">
        <span>环境 <b>{{ env.url }}</b></span>
        <span>路由 {{ (env.routes || []).join(' , ') || '—' }}</span>
        <span v-if="env.ok" class="ok">已采证 {{ (env.pages || []).length }} 个页面</span>
        <span v-else class="bad">未采到证据：{{ env.error || '环境不可达' }}</span>
        <span v-for="p in blankPages" :key="p.route" class="bad">
          路由 {{ p.route }} 空白（停在 {{ shortUrl(p.final_url || p.url) }}，
          <a :href="shotUrl(p.screenshot)" target="_blank" rel="noopener">看工具看到的截图</a>）
        </span>
      </div>

      <table v-if="verdicts.length" class="st-table" id="st-verdicts">
        <thead>
          <tr><th>链路</th><th>检查项</th><th>结论</th><th>依据</th><th>证据</th></tr>
        </thead>
        <tbody>
          <tr v-for="v in verdicts" :key="`${v.lane}-${v.id}`" :class="`row-${v.status}`">
            <td class="st-lane">{{ LANE_LABEL[v.lane] || v.lane }}</td>
            <td class="st-title">
              {{ v.title }}
              <div v-if="v.expected" class="st-expect">预期：{{ v.expected }}</div>
            </td>
            <td><span class="st-badge" :class="v.status">{{ STATUS_LABEL[v.status] || v.status }}</span></td>
            <td class="st-reason">{{ v.reason || '—' }}</td>
            <td>
              <a v-if="v.evidence && v.evidence[0]" :href="shotUrl(v.evidence[0])"
                 target="_blank" rel="noopener">截图</a>
              <span v-else class="st-dim">—</span>
            </td>
          </tr>
        </tbody>
      </table>
      <pre v-else id="st-verdicts" class="log"
           :data-placeholder="'还没有判定结果。在右侧「自测参数」里填环境地址，并给三条链路之一提供输入（工单 / 用例 / 需求文档 / Figma 链接），再点「开始自测」。'" />

      <ul v-if="notes.length" class="st-notes" id="st-notes">
        <li v-for="(n, i) in notes" :key="i">{{ n }}</li>
      </ul>

      <div v-if="pulled" class="st-pulled" id="st-pulled">
        <div class="st-pulled-head">
          工单 {{ pulled.issue }} · {{ pulled.count }} 条用例
          <span class="st-dim">{{ pulled.note }}</span>
        </div>
        <ol>
          <li v-for="(c, i) in (pulled.items || []).slice(0, 8)" :key="i">
            <b>{{ c.title }}</b>
            <span v-if="c.expected" class="st-dim"> · 预期：{{ c.expected }}</span>
          </li>
        </ol>
        <div v-if="!pulled.count" class="st-dim">
          ONES 的测试情况接口在各部署下形状不同，没拉到就用手动粘贴用例兜底。
        </div>
      </div>

      <div class="info-note">
        三条链路只读采证：不开分支、不改代码、不写目标仓库（唯一落盘是报告 JSON 与截图）。<br>
        环境不可达 / 页面空白 / 证据不足一律记「无法判断」，<b>不计为通过</b>；设计稿只比字号 / 字重 / 颜色 / 宽度，不比位置。
      </div>
    </section>
  </ParamLayout>
</template>
