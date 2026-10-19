// 资源汇总板的状态（需求文档链接 / 各环境地址）。
//
// **模块级单例**：页签切走再切回、以及将来别处想读一眼入口（命令面板 / 统计页），
// 拿到的必须是同一份。工厂里 new 一组 ref 会让两处各持一份，保存后互相看不见 ——
// 这个坑在本仓的其他模块上踩过，所以这里从一开始就写成模块级。
//
// 保存策略：**改一处就整份回存**，不设「保存」按钮。
// 便签板最容易的失败是「改完忘了点保存」，而这个模块没有别的写入口，也就不存在
// 需要攒批次的场景。并发用「在飞 + 排队」两态收口：连改三个字段最多发两次请求，
// 且后发的永远带最新的整板 —— 否则旧请求的回包会把新内容盖掉（丢更新且无报错）。
import { computed, ref } from 'vue'
import { api } from '../api/client.js'

const board = ref({ groups: [] })
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const savedAt = ref('')
const loaded = ref(false)

/** 在飞标记与排队标记：模块级，不是组件级（组件重挂不能打断正在进行的回存） */
let inFlight = false
let queued = false
/** 本地是否已经动过：load 的回包晚于用户第一次编辑时，不能把编辑盖掉 */
let touched = false

function uid(prefix) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
}

/**
 * 把用户写的东西收敛成可点的地址。与后端 `scripts/resource_board.py::normalize_url`
 * 同一套规则，但**这里只负责「让人当场看见会变成什么」**，真正的协议白名单在后端
 * （前端校验只影响显示，绝不能当成安全边界）。
 *
 * 注意 `host:8080/path` 这类写法：冒号后面紧跟数字就是端口，不是协议 ——
 * 按「有冒号就是 scheme」判断会把内网地址整条丢掉（踩过）。
 */
export function normalizeUrl(raw) {
  let url = String(raw || '').trim().replace(/^["']+|["']+$/g, '')
  if (!url) return ''
  if (url.startsWith('//')) url = 'https:' + url
  else if (/^[a-z][a-z0-9+.-]*:(?!\d)/i.test(url)) {
    // 别的协议（javascript: / data: / mailto: / ftp://）不放行
    if (!/^https?:\/\//i.test(url)) return ''
  } else if (!/^https?:\/\//i.test(url)) {
    url = 'http://' + url.replace(/^\/+/, '')
  }
  return url
}

/** 展示用：去掉协议头与末尾斜杠，一行里露出最有辨识度的部分（host + path）。 */
export function prettyUrl(url) {
  return String(url || '').replace(/^https?:\/\//i, '').replace(/\/$/, '')
}

export function useResources() {
  const counts = computed(() => {
    const groups = board.value.groups || []
    return {
      groups: groups.length,
      items: groups.reduce((n, g) => n + (g.items ? g.items.length : 0), 0),
    }
  })
  const savedLabel = computed(() => {
    if (saving.value) return '保存中…'
    if (error.value) return '保存失败'
    const raw = savedAt.value
    if (!raw) return ''
    const m = /T(\d{2}:\d{2})/.exec(raw)
    return m ? `已保存 ${m[1]}` : '已保存'
  })

  async function load(force = false) {
    if (loaded.value && !force) return
    loading.value = true
    try {
      const data = await api.resources()
      const b = (data && data.board) || {}
      // 用户可能在等回包的这一两百毫秒里已经点了「+ 环境地址」：这时用服务端的
      // 旧内容整块替换，会把他刚建的分组吞掉。动过就只标记"已加载"，不覆盖。
      if (!touched) {
        board.value = { ...b, groups: Array.isArray(b.groups) ? b.groups : [] }
      }
      loaded.value = true
      error.value = ''
    } catch (e) {
      error.value = (e && e.message) || String(e)
    } finally {
      loading.value = false
    }
  }

  async function flush() {
    if (inFlight) { queued = true; return }
    inFlight = true
    saving.value = true
    try {
      do {
        queued = false
        const data = await api.saveResources({
          board: JSON.parse(JSON.stringify(board.value)),
        })
        // 只取服务端的时间戳，**不覆盖本地 board**：每次回存都换成服务端对象，
        // 会让正在输入的框丢焦点、光标跳回开头（表现为「打一半被打断」）。
        savedAt.value = (data && data.board && data.board.updated) || ''
        error.value = ''
      } while (queued)
    } catch (e) {
      error.value = (e && e.message) || String(e)
    } finally {
      inFlight = false
      saving.value = false
    }
  }

  function addGroup(label = '新分组') {
    touched = true
    const g = { id: uid('g'), label, items: [] }
    board.value.groups.push(g)
    return g
  }

  function removeGroup(gid) {
    touched = true
    board.value.groups = board.value.groups.filter((g) => g.id !== gid)
  }

  function addItem(gid) {
    touched = true
    const g = board.value.groups.find((x) => x.id === gid)
    if (!g) return
    if (!Array.isArray(g.items)) g.items = []
    g.items.push({ id: uid('i'), label: '', url: '', note: '' })
  }

  function removeItem(gid, iid) {
    touched = true
    const g = board.value.groups.find((x) => x.id === gid)
    if (!g) return
    g.items = g.items.filter((it) => it.id !== iid)
  }

  /** 从输入框失焦时调用：把「看起来像地址」的东西补成真地址，再回存。 */
  function commitUrl(item) {
    const fixed = normalizeUrl(item.url)
    if (fixed) item.url = fixed
    flush()
  }

  return {
    board, loading, saving, error, savedAt, savedLabel, counts, loaded,
    load, flush, addGroup, removeGroup, addItem, removeItem, commitUrl,
    normalizeUrl, prettyUrl,
  }
}
