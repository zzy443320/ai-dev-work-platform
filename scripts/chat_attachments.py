# -*- coding: utf-8 -*-
"""问答附件：用户粘贴 / 上传的图片与文本文件。

为什么单独做一块：问答页签原本只吃一段纯文本，但真实排障里十有八九是
「截图 + 一句话」——报错截图、设计稿截图、控制台日志、几百行的报错栈。
把这些都塞进 textarea 是不现实的（图片根本贴不进去），所以这里做一层
**落盘 + 归一化**：

1. `ChatAttachmentStore.save()`  把上传的字节落到 `attachments/`，文件名自己生成
   （绝不用用户给的名字当路径），返回一份可安全回显的元数据；
2. `image_payload()`  图片 → 多模态消息里的 image 片段（base64 data URI）；
3. `text_payload()`   文本 → 直接拼进提示词的一段文字（带来源标注与截断）。

安全契约与其它模块一致：**本模块只写自己的数据目录**，不碰目标仓库。
路径安全靠两道锁：文件名由服务端生成（`att-<uuid>.<ext>`），扩展名走白名单。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 允许的图片类型 → 落盘扩展名（白名单，不做任何嗅探式猜测）
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
}
# 允许的文本类型 → 落盘扩展名。日志类文件在浏览器里的 MIME 经常是空的
# （.log/.txt 很常见），所以扩展名兜底是必需的，不能只看 content-type。
TEXT_TYPES = {
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/csv": "csv",
    "text/html": "html",
    "text/css": "css",
    "text/xml": "xml",
    "application/json": "json",
    "application/xml": "xml",
    "application/x-yaml": "yaml",
    "application/yaml": "yaml",
    "application/javascript": "js",
    "application/x-javascript": "js",
    "application/typescript": "ts",
    "application/x-sh": "sh",
    "application/sql": "sql",
    "application/octet-stream": "",   # 交给扩展名兜底
    "": "",
}
# 扩展名兜底：浏览器给不出 type 时按后缀认（值 = 归一后的后缀）
EXT_TYPES = {
    ".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".gif": "gif", ".webp": "webp",
    ".bmp": "bmp", ".svg": "svg",
    ".txt": "txt", ".log": "log", ".md": "md", ".markdown": "md", ".csv": "csv",
    ".tsv": "tsv", ".json": "json", ".jsonc": "json", ".xml": "xml", ".yml": "yaml",
    ".yaml": "yaml", ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".less": "less", ".js": "js", ".jsx": "jsx", ".mjs": "mjs", ".cjs": "cjs",
    ".ts": "ts", ".tsx": "tsx", ".vue": "vue", ".py": "py", ".java": "java",
    ".go": "go", ".rs": "rs", ".c": "c", ".h": "h", ".cpp": "cpp", ".cs": "cs",
    ".php": "php", ".rb": "rb", ".sh": "sh", ".bash": "sh", ".sql": "sql",
    ".ini": "ini", ".conf": "conf", ".env": "env", ".properties": "properties",
    ".gradle": "gradle", ".toml": "toml", ".patch": "patch", ".diff": "diff",
}

# 单个附件上限。图片按字节卡（太大模型侧也吃不下），文本另按字符截断。
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
# 送进模型时单个文本附件最多留多少字符：贴一整份日志进来也只会留头尾，
# 不然一次提问就能把上下文预算烧光（与 chat.py 的历史预算同一考虑）。
MAX_TEXT_CHARS = 20000
MAX_COUNT_PER_MESSAGE = 12

# Windows 保留名：用户真拿这些名字当文件名时不许直接建目录项
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} \
            | {f"lpt{i}" for i in range(1, 10)}


class AttachmentError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    """展示用文件名：只保留基名，去掉路径与危险字符。**不用于落盘**。"""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", base).lstrip(".")
    if not base:
        base = "附件"
    stem = base.rsplit(".", 1)[0].lower()
    if stem in _RESERVED:
        base = f"_{base}"
    return base[:120]


def _display_name(name: str, ext: str) -> str:
    """展示用名，与**实际落盘后缀**对齐。

    用户给的名字只是素材，真正决定类型的是 `classify()`/`ext_for()`。
    两者可能不一致（例如把 `evil.sh` 当纯文本上传 → 落盘成 `.txt`），
    这时若照抄原名，界面会显示 `evil.sh` 而链接打开的是 txt，自相矛盾。
    所以这里统一成 `<原主干>.<落盘后缀>`。
    """
    base = safe_name(name)
    if not ext:
        return base
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem}.{ext}" if stem else f"附件.{ext}"


def classify(content_type: str, name: str) -> Optional[str]:
    """认出「图片」还是「文本」。认不出一律返回 None（调用方据此拒绝）。

    顺序很重要：先看 MIME，再看扩展名。浏览器对 .log/.patch 这类文件经常
    给 application/octet-stream，只看 MIME 会把日志误判成二进制丢掉。
    """
    ct = str(content_type or "").split(";")[0].strip().lower()
    ext = Path(safe_name(name)).suffix.lower()
    if ct in IMAGE_TYPES:
        return "image"
    if ct in TEXT_TYPES and ct not in ("", "application/octet-stream"):
        return "text"
    if ext in EXT_TYPES:
        return "image" if EXT_TYPES[ext] in IMAGE_TYPES.values() else "text"
    # 最后按 MIME 大类兜底（text/* 一律当文本）
    if ct.startswith("text/"):
        return "text"
    if ct.startswith("image/"):
        return "image"
    return None


def ext_for(content_type: str, name: str, kind: str) -> str:
    ct = str(content_type or "").split(";")[0].strip().lower()
    ext = Path(safe_name(name)).suffix.lower()
    if kind == "image":
        if ct in IMAGE_TYPES:
            return IMAGE_TYPES[ct]
        if ext in EXT_TYPES:
            return EXT_TYPES[ext]
        return "png"
    if ct in TEXT_TYPES and TEXT_TYPES[ct]:
        return TEXT_TYPES[ct]
    if ext in EXT_TYPES:
        return EXT_TYPES[ext]
    return "txt"


def _looks_binary(data: bytes) -> bool:
    """NUL 字节是二进制最可靠的信号（文本附件绝不该带 NUL）。"""
    return b"\x00" in data[:4096]


class ChatAttachmentStore:
    """问答附件的落盘账本：一个附件一个文件 + 一份同名 `.meta.json`。

    与 ChatStore 同风格：普通文件、人可读、可手工删，没有数据库。
    """

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- 落盘
    def save(self, name: str, data: bytes, content_type: str = "") -> Dict:
        """保存一个附件，返回可安全回显的元数据 dict。"""
        data = bytes(data or b"")
        if not data:
            raise AttachmentError(f"「{safe_name(name)}」是空文件，已跳过")
        kind = classify(content_type, name)
        if kind is None:
            raise AttachmentError(
                f"不支持的文件类型：{safe_name(name)}。"
                "目前只能上传图片（png/jpg/gif/webp/bmp/svg）与文本类文件"
                "（日志、代码、配置、csv、json…）")
        limit = MAX_IMAGE_BYTES if kind == "image" else MAX_TEXT_BYTES
        if len(data) > limit:
            raise AttachmentError(
                f"「{safe_name(name)}」有 {len(data) / 1048576:.1f}MB，"
                f"超过上限 {limit // 1048576}MB")
        if kind == "text" and _looks_binary(data):
            raise AttachmentError(
                f"「{safe_name(name)}」看起来是二进制文件（含 NUL 字节），"
                "不能作为文本附件读入。请粘贴相关文本，或改成图片上传。")

        aid = f"att-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        ext = ext_for(content_type, name, kind)
        target = self.base / f"{aid}.{ext}"
        target.write_bytes(data)

        info = {
            "id": aid,
            "name": _display_name(name, ext),
            "kind": kind,
            "ext": ext,
            "size": len(data),
            "content_type": str(content_type or "").split(";")[0].strip().lower()
                            or mimetypes.guess_type(safe_name(name))[0] or "",
            "created": _now(),
            "file": target.name,
            "path": str(target),
        }
        self._meta_path(aid).write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return info

    # ------------------------------------------------------------- 读取
    def _meta_path(self, aid: str) -> Path:
        return self.base / f"{Path(str(aid or '')).stem}.meta.json"

    def get(self, aid: str) -> Optional[Dict]:
        """按 id 取元数据。id 里的路径片段一律剥掉（防 ../ 穿越）。"""
        aid = Path(str(aid or "")).stem
        if not aid:
            return None
        path = self._meta_path(aid)
        if not path.is_file():
            return None
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        f = self.base / Path(str(info.get("file") or "")).name
        if not f.is_file():
            return None
        info["path"] = str(f)
        return info

    def resolve(self, aid: str) -> Optional[Path]:
        info = self.get(aid)
        return Path(info["path"]) if info else None

    def many(self, ids: List[str]) -> List[Dict]:
        """按给定顺序取多个附件；认不出的 id 直接丢弃（不报错打断提问）。"""
        out: List[Dict] = []
        seen = set()
        for aid in ids or []:
            aid = str(aid or "").strip()
            if not aid or aid in seen:
                continue
            seen.add(aid)
            info = self.get(aid)
            if info:
                out.append(info)
        return out[:MAX_COUNT_PER_MESSAGE]

    def public(self, info: Dict) -> Dict:
        """回显给前端 / 存进会话消息的版本（带上可预览的 url）。"""
        return {
            "id": info.get("id", ""),
            "name": info.get("name", ""),
            "kind": info.get("kind", ""),
            "size": int(info.get("size") or 0),
            "content_type": info.get("content_type", ""),
            "url": f"/attachments/{info.get('file', '')}",
        }

    # --------------------------------------------------- 送给模型的两种形态
    def image_payload(self, info: Dict) -> Optional[Dict]:
        """图片 → 多模态片段（base64 data URI，不带文件路径）。"""
        if info.get("kind") != "image":
            return None
        path = Path(str(info.get("path") or ""))
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()
        except Exception:
            return None
        mime = info.get("content_type") or ""
        if not mime.startswith("image/"):
            mime = f"image/{info.get('ext') or 'png'}"
            if mime.endswith("/jpg"):
                mime = "image/jpeg"
        b64 = base64.b64encode(raw).decode("ascii")
        return {"name": info.get("name", ""), "mime": mime,
                "data_uri": f"data:{mime};base64,{b64}",
                "bytes": len(raw)}

    def text_payload(self, info: Dict) -> Optional[str]:
        """文本 → 带来源标注的一段文字（超长留头尾，并如实说明截断了）。"""
        if info.get("kind") != "text":
            return None
        path = Path(str(info.get("path") or ""))
        if not path.is_file():
            return None
        raw = None
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                raw = path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                return None
        if raw is None:
            return None
        name = info.get("name", "附件")
        if len(raw) > MAX_TEXT_CHARS:
            head = raw[: int(MAX_TEXT_CHARS * 0.7)]
            tail = raw[-int(MAX_TEXT_CHARS * 0.3):]
            raw = (f"{head}\n\n…（中间省略 {len(raw) - MAX_TEXT_CHARS} 字符，"
                   f"原文共 {len(raw)} 字符）…\n\n{tail}")
        return f"### 附件「{name}」（用户上传，共 {info.get('size') or 0} 字节）\n{raw}"


def images_for(store: ChatAttachmentStore, infos: List[Dict]) -> List[Dict]:
    """把附件列表里所有图片转成多模态片段（顺序与用户上传一致）。"""
    out = []
    for info in infos or []:
        p = store.image_payload(info)
        if p:
            out.append(p)
    return out


def text_block(store: ChatAttachmentStore, infos: List[Dict]) -> str:
    """把附件列表里所有文本拼成一段（供插进提示词）。没有文本附件时返回空串。"""
    parts = [t for t in (store.text_payload(i) for i in infos or []) if t]
    return "\n\n".join(parts)


def attach_note(infos: List[Dict]) -> str:
    """给模型的「本轮附件清单」说明——它必须知道哪些是图、哪些是文本。"""
    if not infos:
        return ""
    lines = []
    for i in infos:
        if i.get("kind") == "image":
            lines.append(f"- 图片「{i.get('name')}」已作为图像内容一并发送给你")
        else:
            lines.append(f"- 文本「{i.get('name')}」的完整内容见下方「用户附件」一节")
    return ("## 本轮用户附件（共 %d 个）\n" % len(infos)) + "\n".join(lines) + "\n"
