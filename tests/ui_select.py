# -*- coding: utf-8 -*-
"""界面自检用的下拉选择助手。

页面上有两类下拉，必须用两套驱动方式：

1. **原生 `<select>`**（旧版「自绘下拉」接管后加了 cs-native 类，以及个别刻意
   保留原生的下拉）：写 `value` → 派发 `change`。隐藏后 Playwright 的
   `page.select_option()` 会一直等「元素可见」直到超时，这个坑在 check_pager.py
   里踩过，所以这里手写事件派发绕过。

2. **Element Plus 的 `<el-select>`**：它不是原生控件，没有 `.value` 可写，
   必须模拟「点开 → 点选项」。四个要点（每条都是踩过的坑）：

   a. **id 落在哪一层要看 Element 版本**：Element Plus 2.x 把透传的 `id` 绑在
      **内层 `<input class="el-select__input">`** 上（不是根 `.el-select`）。
      该 input 被右侧 `.el-select__suffix`（箭头图标）/ 已选值文本盖住，直接点它
      会被判成「pointer events 被拦截」，重试到 30s 超时。所以先
      `ancestor-or-self` 上溯到 `.el-select` 根，再点它的 `.el-select__wrapper`。

   b. **选项列表按「本下拉」限定**：页面上但凡展开过一次的 el-select，它的
      `.el-select-dropdown__item` 都会留在 body 里（关掉只是 display:none）。
      直接 `wait_for_selector('.el-select-dropdown__item', state='visible')` 会
      盯着**第一个**（属于别的下拉、永远不可见）等到超时。Element 把每个下拉的
      选项列表挂在它 `aria-controls` 指向的 id 上，所以先取这个 id，再
      `#<id> .el-select-dropdown__item` 精确等待与点击。

   c. el-option **不会**把 value 渲染到 DOM（没有 data-value），所以选项优先按
      `data-value` 定位，退化用「文案包含」。组件里的选项文案必须包含调用方传的
      值（例如 'bearer' ⊂ 'Authorization: Bearer'），否则给 el-option 显式加
      `data-value`。找不到时会**抛错并列出可见选项**，避免用例假通过。

   d. **选完等一拍**：Element 的选中态与联动渲染是异步的，`_pick_el_select` 结尾
      会 `wait_for_timeout(150)`；调用方断言联动结果前再 `wait_for_timeout(200)`
      即可，不要用更长的固定等待去猜。
"""
from playwright.sync_api import Error


def _is_el_select(page, selector):
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el || el.tagName === 'SELECT') return false;
            return !!el.closest('.el-select');
        }""", selector)


def _el_select_root(page, selector):
    """统一到 `.el-select` 根元素（兼容 id 在根 / 在内层 input 两种版本）。"""
    return page.locator(selector).first.locator(
        "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '),"
        " ' el-select ')][1]")


def _el_list_id(page, selector):
    """本下拉展开后挂选项列表的那个 popper id（在 input 的 aria-controls 上）。"""
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return '';
            const host = el.closest('.el-select') || el;
            const inp = host.querySelector('input[aria-controls]') || el;
            return (inp && inp.getAttribute('aria-controls')) || '';
        }""", selector)


def _pick_el_select(page, selector, value):
    el_root = _el_select_root(page, selector)
    wrapper = el_root.locator('.el-select__wrapper')
    target = wrapper.first if wrapper.count() else el_root.first
    try:
        target.click(timeout=5000)
    except Error:
        # 某些尺寸 / 主题下 wrapper 的命中点仍被内部节点吃掉。el-select 的展开
        # 逻辑挂在 wrapper 的 click 上，JS 派发冒泡 click 同样能触发。
        target.evaluate(
            "el => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))")

    list_id = _el_list_id(page, selector)
    if list_id:
        page.wait_for_selector(f'#{list_id} .el-select-dropdown__item',
                               state='visible', timeout=5000)
    else:
        page.wait_for_function(
            """() => [...document.querySelectorAll('.el-select-dropdown__item')]
                     .some(i => i.offsetParent !== null)""", timeout=5000)

    hit = page.evaluate(
        """([listId, val]) => {
            const v = String(val).toLowerCase();
            const root = listId ? document.getElementById(listId) : document;
            if (!root) return { ok: false, seen: [] };
            const items = Array.from(root.querySelectorAll('.el-select-dropdown__item'))
                .filter(i => i.offsetParent !== null);
            const it = items.find(i => (i.getAttribute('data-value') || '').toLowerCase() === v)
                    || items.find(i => (i.textContent || '').trim().toLowerCase().includes(v));
            if (!it) return { ok: false, seen: items.map(i => (i.textContent || '').trim()) };
            it.click();
            return { ok: true, seen: [] };
        }""", [list_id, value])
    if not hit.get('ok'):
        raise AssertionError(
            'el-select %s 里找不到 value 为 %r 的选项（优先认 data-value，'
            '否则选项文案需包含该值）；可见选项=%r' % (selector, value, hit.get('seen')))
    # 等选择生效（Element 会异步更新选中态并触发 change）
    page.wait_for_timeout(150)


def el_select_values(page, selector):
    """打开 el-select 读一遍选项，返回 [data-value, 文案] 列表，读完按 Esc 收起。

    用途：用例要断言「下拉的选项按某数据源填充」。原生 <select> 时代直接读
    `option` 的 value；el-select 的选项列表是惰性渲染（首次展开才挂到 body），
    所以必须真的点开一次再读。
    """
    el_root = _el_select_root(page, selector)
    wrapper = el_root.locator('.el-select__wrapper')
    target = wrapper.first if wrapper.count() else el_root.first
    try:
        target.click(timeout=5000)
    except Error:
        target.evaluate(
            "el => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))")

    list_id = _el_list_id(page, selector)
    if list_id:
        page.wait_for_selector(f'#{list_id} .el-select-dropdown__item',
                               state='visible', timeout=5000)
    else:
        page.wait_for_function(
            """() => [...document.querySelectorAll('.el-select-dropdown__item')]
                     .some(i => i.offsetParent !== null)""", timeout=5000)

    items = page.evaluate(
        """(listId) => {
            const root = listId ? document.getElementById(listId) : document;
            return Array.from((root || document)
                    .querySelectorAll('.el-select-dropdown__item'))
                .filter(i => i.offsetParent !== null)
                .map(i => [i.getAttribute('data-value') || '',
                           (i.textContent || '').trim()]);
        }""", list_id)
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    return items


def pick_select(page, selector, value):
    """选中某个下拉的值（等价于用户操作对应下拉控件）。"""
    if _is_el_select(page, selector):
        _pick_el_select(page, selector, value)
        return

    page.evaluate(
        """([sel, val]) => {
            const el = document.querySelector(sel);
            if (!el) throw new Error('找不到下拉: ' + sel);
            el.value = val;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            if (el._csSync) el._csSync();   // 自绘下拉的触发器文案
        }""",
        [selector, value],
    )
