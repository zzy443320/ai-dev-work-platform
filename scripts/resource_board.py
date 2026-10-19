# -*- coding: utf-8 -*-
"""资源汇总板（`resources.json`）：需求文档链接 + 各环境地址 + 随手记的入口。

一句话：**这是用户自己的便签板，不是配置**。所以它单独一个文件、单独一组端点：

* 不进 ``ui_settings.json`` —— 那边整份配置会被 ``/api/settings`` 读改写回一遍
  （默认值合并 + 密钥掩码），把便签混进去，一次「保存设置」就可能把它们覆盖掉；
* 不建目录、不一条一个文件 —— 它是一份**有序列表**，顺序与分组本身就是内容。
  一条一个文件还得额外存顺序，读一次要 glob 整个目录；
* 写入必须原子（临时文件 + ``replace``）：这是唯一一份，写坏了就没了。

安全：``url`` 只接受 http/https。界面上这些条目是 ``<a target="_blank">``，而
``javascript:`` 这类协议在 href 里是**能执行**的 —— 便签内容多半是从聊天窗口
粘过来的，所以协议白名单在后端做一次（唯一的收口），前端只负责显示。

清洗（:func:`sanitize_board`）刻意做成**幂等**的：同一份数据反复过一遍结果不变。
这样「前端回存整板 → 后端清洗 → 下次再回存」不会慢慢漂移。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

# 上限：便签板是「眼前要用的一屏入口」，不是书签管理器。
# 给个硬上限是为了让界面不会因为一次误粘（几千行）而卡住。
MAX_GROUPS = 40
MAX_ITEMS_PER_GROUP = 120
MAX_LABEL = 60
MAX_NOTE = 240
MAX_URL = 2000

ALLOWED_SCHEMES = ("http", "https")

_ID_JUNK = re.compile(r"[^0-9A-Za-z_-]+")
# 「像协议头」的形态：`javascript:` / `data:` / `mailto:` / `ftp://` 都算。
# 但要排掉 `host:8080/path` 这种「内网地址 + 端口」—— 冒号后面紧跟数字就是端口，
# 不是协议（踩过：order.test.local:8080/prd 被当成 scheme 直接丢掉）。
_FOREIGN_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(?!\d)")
_HTTP = re.compile(r"(?i)^https?://")


def _text(value: Any, limit: int) -> str:
    """压平空白并截断。便签是单行文本：换行会把一条链接的排版顶散。"""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def normalize_url(raw: Any) -> str:
    """把用户写的东西收敛成一个可点的 http(s) 地址；不可用返回 ``""``。

    - ``10.1.2.3:8080/order`` → ``http://10.1.2.3:8080/order``（内网地址最常见的写法）
    - ``order.test.local:8080/prd`` → 同上：冒号后面是端口，不是协议
    - ``//host/path`` → ``https://host/path``
    - ``javascript:`` / ``data:`` / ``file:`` / ``mailto:`` 一律拒绝
      （href 里能执行脚本 / 读本地文件；要发邮件请直接贴地址）
    """
    url = _text(raw, MAX_URL).strip("\"'")
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif _HTTP.match(url):
        pass                      # 已经是 http(s)，原样保留
    elif _FOREIGN_SCHEME.match(url):
        return ""
    else:
        # 没有协议头（且不像别的协议）：按内网地址处理。不猜 https —— 内网服务九成
        # 是 http，猜错的表现是「点开一片空白」，比多点一次「不安全提示」更难查。
        url = "http://" + url.lstrip("/")

    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.hostname:
            return ""
        _ = parts.port            # 非法端口（host:abc）在这里抛 ValueError
    except ValueError:
        return ""
    return url


def _clean_id(raw: Any, prefix: str, taken: set) -> str:
    """id 只用于前端 :key 与删除定位，不需要好看，但必须唯一且稳定。"""
    rid = _ID_JUNK.sub("-", str(raw or "")).strip("-")[:40]
    if not rid or rid in taken:
        rid = f"{prefix}-{uuid.uuid4().hex[:8]}"
        while rid in taken:
            rid = f"{prefix}-{uuid.uuid4().hex[:8]}"
    taken.add(rid)
    return rid


def _clean_items(raw_items: Any, taken: set) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(raw_items, list):
        return out
    for raw in raw_items[:MAX_ITEMS_PER_GROUP]:
        if not isinstance(raw, dict):
            continue
        url = normalize_url(raw.get("url"))
        label = _text(raw.get("label"), MAX_LABEL)
        note = _text(raw.get("note"), MAX_NOTE)
        if not url and not label and not note:
            continue                 # 完全空白的一行：不留
        if not label:
            # 只填了地址就先拿地址当名字（去掉协议头，省得一行被撑满）
            label = _text(url.split("://", 1)[-1], MAX_LABEL) or "未命名链接"
        out.append({"id": _clean_id(raw.get("id"), "i", taken),
                    "label": label, "url": url, "note": note})
    return out


def sanitize_board(payload: Any) -> Dict[str, Any]:
    """把任意载荷收敛成合法板子。空输入 → 空板（``groups: []``）。"""
    groups_in = payload.get("groups") if isinstance(payload, dict) else None
    groups: List[Dict[str, Any]] = []
    taken: set = set()
    if isinstance(groups_in, list):
        for raw in groups_in[:MAX_GROUPS]:
            if not isinstance(raw, dict):
                continue
            groups.append({
                "id": _clean_id(raw.get("id"), "g", taken),
                "label": _text(raw.get("label"), MAX_LABEL) or "未命名分组",
                "items": _clean_items(raw.get("items"), taken),
            })
    board: Dict[str, Any] = {"version": 1, "groups": groups}
    if isinstance(payload, dict) and payload.get("updated"):
        board["updated"] = _text(payload.get("updated"), 40)
    return board


class ResourceBoard:
    """一份 JSON 文件的读写。锁是**类级**的：服务端每个请求都新建实例
    （见 ``web/state.py`` 的 ``_rboard``），实例级锁跨请求不共享，等于没加。"""

    _lock = threading.RLock()

    def __init__(self, path):
        self.path = Path(path)

    # ------------------------------------------------------------------ 读
    def load(self) -> Dict[str, Any]:
        with self._lock:
            if not self.path.is_file():
                return sanitize_board({})
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # 坏文件按空板处理，但**不落盘覆盖** —— 用户下次保存才会替换它。
                # 直接写回会把可能还能手工抢救的内容悄悄清掉。
                return sanitize_board({})
            return sanitize_board(raw)

    # ------------------------------------------------------------------ 写
    def save(self, payload: Any) -> Dict[str, Any]:
        board = sanitize_board(payload)
        board["updated"] = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                tmp.write_text(json.dumps(board, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                tmp.replace(self.path)
            finally:
                if tmp.exists():          # replace 成功时它已经不在了
                    with suppress(OSError):
                        tmp.unlink()
        return board
