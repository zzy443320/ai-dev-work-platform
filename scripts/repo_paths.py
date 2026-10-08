# -*- coding: utf-8 -*-
"""把「相对仓库根的路径」解析成仓库内的绝对路径，越界一律拒。

写盘前的最后一道纵深防御。提案 / 产出物里的 file_path 归根到底是模型产出的字符串：
`patch_engine._norm_path` 在归一化阶段已经洗过一轮，但**写盘那一步不应该只依赖上游**
——哪天多出一条新的生成路径忘了洗，这里就是最后一道闸。

artifact.py 里原本就有一份同语义的 `safe_rel_path`（返回 Optional[Path]）。这份的行为
一致，差别只在：越界时抛出带原因UnsafePath，让调用方能直接把「为什么拒」回给用户看，
而不是笼统一句「路径不合法」。
"""
from pathlib import Path
from typing import Union


class UnsafePath(ValueError):
    """路径会写到仓库外面：绝对路径、盘符、``..`` 逃逸、或经符号链接跳出仓库。"""


def resolve_in_repo(repo_root: Union[str, Path], rel: str) -> Path:
    """返回 ``repo_root`` 内的绝对路径。

    任何跳出仓库的写法都抛 :class:`UnsafePath`，消息里带上原始值便于排查。
    """
    root = Path(repo_root).resolve()
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw:
        raise UnsafePath("文件路径为空")

    # 前导斜杠必须在剥掉空段**之前**拦掉。Windows 上 `Path("/etc/passwd").is_absolute()`
    # 是 False（它被解释成「当前盘根下的相对路径」），只依赖 is_absolute 的话，
    # "/etc/passwd" 会被洗成 "etc/passwd" 当成仓内路径放行——这是本模块最初的洞。
    # 顺带也盖住了 UNC（//server/share 同样以前导斜杠开头）。
    if raw.startswith("/"):
        raise UnsafePath(f"文件路径必须是仓库内相对路径: {rel!r}")

    # 丢掉 "." 与重复斜杠：a/./b、a//b 都等价于 a/b，不算逃逸
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafePath(f"文件路径解析后为空: {rel!r}")

    if any(p == ".." for p in parts):
        raise UnsafePath(f"文件路径不允许跳出仓库: {rel!r}")
    # 盘符（D:/x）：必须是仓库内相对路径
    if len(parts[0]) >= 2 and parts[0][1] == ":":
        raise UnsafePath(f"文件路径必须是仓库内相对路径，不能带盘符: {rel!r}")
    if Path(raw).is_absolute():
        raise UnsafePath(f"文件路径必须是仓库内相对路径: {rel!r}")

    target = (root / "/".join(parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        # 走到这里说明中间某一级是符号链接，真实落点在仓库外
        raise UnsafePath(f"文件路径经符号链接后落到了仓库外: {rel!r}")
    return target
