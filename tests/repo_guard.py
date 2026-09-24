# -*- coding: utf-8 -*-
"""定位类回归的「目标仓库形状」闸门。

`check_locate_regression*.py` 断言的是「关键词抽取与读取窗口要不要落在这个前端
monorepo 的某个目录」，它们读 `ui_settings.json` 里配的目标仓库、并直接检查仓库里
文件的内容。一旦当前配置指向的是别的仓库（mock 仓库、朋友的项目……），用例不会
"跳过"，而是抛出一串「某某文件没进窗口」的红——看着像工具坏了，其实只是跑错了仓库。

所以先判形状：没有 `packages/` 目录就明确说明原因并跳过（退出码 0）。
"""
from pathlib import Path

MARKER = "packages"


def require_frontend_monorepo(repo: str, *, what: str = "本用例") -> bool:
    root = Path(repo or "")
    if not repo or not root.is_dir():
        print(f"  [SKIP] {what} 需要真实目标仓库，当前路径不存在：{repo or '(未配置)'}")
        return False
    if not (root / MARKER).is_dir():
        print(f"  [SKIP] {what} 验证的是「{MARKER}/ 下的前端 monorepo」里的定位行为，"
              f"但当前目标仓库没有 {MARKER}/ 目录：\n"
              f"         {root}\n"
              f"         换个仓库跑只会得到一堆看不懂的红，已跳过（未做任何断言）。")
        return False
    return True
