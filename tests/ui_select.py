# -*- coding: utf-8 -*-
"""界面自检用的下拉选择助手。

页面上所有原生 `<select>` 都被「自绘下拉」组件接管了（`initCustomSelects()` 给它们加了
`cs-native` 类把它藏起来，并在旁边插入 `cs-trigger` 按钮）。隐藏后 Playwright 的
`page.select_option()` 会一直等「元素可见」直到 30s 超时——这个坑在 check_pager.py 里
已经踩过并写了同样绕过办法，这里抽成公共函数给其它用例复用。

绕过方式与真实点击路径等价：写 `value` → 派发 `change` → 刷新触发器文案。
"""


def pick_select(page, selector, value):
    """选中某个原生下拉的值（等价于用户点自绘下拉里的那一项）。"""
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
