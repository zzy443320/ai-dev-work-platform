// 全局快捷键。刻意做成一个独立的 composable 而不是散在 App.vue 的 onEsc 里：
// 「哪些键被占用」这件事必须一眼看全，否则加一个键就悄悄顶掉别处的输入。
//
// 与命令面板的分工：面板自己管它打开时的 ↑↓↵Esc（见 CommandPalette.vue 的 onKey）；
// 这里只管「页面态」的键。两条规则避免打架：
//   1. 面板开着时这里直接 return（除了 ⌘K 本身用于收起）；
//   2. 焦点在输入框 / textarea / contenteditable 里时，**字母与数字键一律放行**，
//      只有带修饰键的组合（⌘K、⌘↵、`）仍然生效 —— 否则打字会跳选中行、
//      或者把「决策备注」里的回车变成「采纳」。
import { usePalette } from './usePalette.js'
import { useDefect } from './useDefect.js'
import { useProposalModal } from './useProposalModal.js'
import { useFold } from './useFold.js'
import { TABS } from '../components/tabs.js'

/** 焦点是否落在可输入的地方（含 Element Plus 的 el-input 内层原生 input） */
function isTypingTarget(el) {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return el.isContentEditable === true
}

export function useHotkeys({ activeTab }) {
  const { open: paletteOpen, toggle: togglePalette } = usePalette()
  const { pageSlice, proposals } = useDefect()
  const { pinnedId, selectInline, detail, loadDetail, onApprove, onReject } = useProposalModal()
  const { isCollapsed, toggleFold } = useFold()

  /** 当前页可见的提案 id（j/k 只在看得见的那一屏里走，不会跳到看不见的页去） */
  function ids() {
    return (pageSlice.value || []).map((p) => p.id || p.defect_id).filter(Boolean)
  }

  function move(delta) {
    const list = ids()
    if (!list.length) return
    const at = list.indexOf(pinnedId.value)
    // 没选中时 j 从第一条开始、k 从最后一条开始（和邮件客户端一致）
    const next = at < 0
      ? (delta > 0 ? 0 : list.length - 1)
      : (at + delta + list.length) % list.length
    selectInline(list[next])
    if (activeTab.value !== 'defect' && window.switchTab) window.switchTab('defect')
  }

  /** ⌘↵ / ⌘⌫：检视器没加载完详情就先加载再动手（否则 decide 拿不到 id） */
  async function act(kind) {
    const id = pinnedId.value
    if (!id) return
    if (!detail.value || detail.value.id !== id) await loadDetail(id)
    if (kind === 'approve') onApprove()
    else onReject()
  }

  function onKey(e) {
    const mod = e.metaKey || e.ctrlKey
    const typing = isTypingTarget(document.activeElement)

    // ⌘K 全局生效，包括正在打字时 —— 它就是「打断当前、去搜索」的键
    if (mod && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault()
      togglePalette()
      return
    }
    if (paletteOpen.value) return

    // ⌃` 开合运行日志（IDE 的 console 键位）
    if (e.ctrlKey && (e.key === '`' || e.key === '~')) {
      e.preventDefault()
      toggleFold('run-console')
      return
    }
    if (typing) return

    // 决策键必须带修饰：裸回车属于「跑一次」而不是「采纳这份补丁」
    if (mod && e.key === 'Enter') { e.preventDefault(); act('approve'); return }
    if (mod && (e.key === 'Backspace' || e.key === 'Delete')) { e.preventDefault(); act('reject'); return }
    if (mod) return

    if (e.key === 'j' || e.key === 'J') { e.preventDefault(); move(1); return }
    if (e.key === 'k' || e.key === 'K') { e.preventDefault(); move(-1); return }
    // 数字键切模块：与命令面板里「导航」那组显示的快捷键同一张表，不各说各话
    const n = Number(e.key)
    if (n >= 1 && n <= TABS.length && window.switchTab) {
      e.preventDefault()
      window.switchTab(TABS[n - 1].name)
    }
  }

  function attach() { window.addEventListener('keydown', onKey) }
  function detach() { window.removeEventListener('keydown', onKey) }

  return { attach, detach, move, act, _proposals: proposals }
}
