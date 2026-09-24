"""Figma REST client — pull a design file / node and flatten it into a compact
text description the AI can reason about.

Read-only: only ever calls GET endpoints on api.figma.com with the user's
Personal Access Token (X-Figma-Token). Nothing is written to Figma.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import requests

FIGMA_API = "https://api.figma.com/v1"


class FigmaError(Exception):
    pass


def parse_figma_url(url: str) -> Tuple[str, str]:
    """Extract (file_key, node_id) from a Figma URL like
    https://www.figma.com/design/ABC123/Name?node-id=12-34  (or /file/ legacy).
    node-id uses '-' in URLs but Figma API wants '12:34'."""
    url = (url or "").strip()
    if not url:
        raise FigmaError("请填写 Figma 设计稿链接")
    m = re.search(r"figma\.com/(?:file|design|proto)/([A-Za-z0-9]+)", url)
    if not m:
        # maybe user pasted the bare file key
        if re.fullmatch(r"[A-Za-z0-9]{10,}", url):
            return url, ""
        raise FigmaError("无法从链接里解析 Figma file key，请粘贴完整的设计稿分享链接")
    file_key = m.group(1)
    node_id = ""
    mn = re.search(r"[?&]node-id=([^&]+)", url)
    if mn:
        node_id = mn.group(1).replace("-", ":")
    return file_key, node_id


class FigmaClient:
    def __init__(self, token: str, timeout: int = 30):
        self.token = (token or "").strip()
        self.timeout = timeout
        if not self.token:
            raise FigmaError("缺少 Figma Personal Access Token（设置里保存过就不用再填）")

    def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        try:
            r = requests.get(
                f"{FIGMA_API}{path}",
                headers={"X-Figma-Token": self.token},
                params=params or {},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise FigmaError(f"网络请求失败: {e}")
        if r.status_code == 403:
            raise FigmaError("Figma 返回 403：token 无效或没有该文件的访问权限")
        if r.status_code == 404:
            raise FigmaError("Figma 返回 404：file key 或 node id 不存在")
        if r.status_code == 429:
            raise FigmaError("Figma 限流（429），请稍后重试")
        if r.status_code != 200:
            raise FigmaError(f"Figma 接口返回 HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            raise FigmaError("Figma 返回的不是 JSON")

    # ------------------------------------------------------------------ api
    def fetch(self, url_or_key: str, node_id: str = "") -> Dict:
        """Return {file_key, file_name, node_id, node_name, tree (compact text),
        node_count, last_modified}."""
        file_key, parsed_node = parse_figma_url(url_or_key)
        node_id = (node_id or "").strip().replace("-", ":") or parsed_node

        if node_id:
            data = self._get(f"/files/{file_key}/nodes", {"ids": node_id, "depth": "6"})
            nodes = data.get("nodes") or {}
            if node_id not in nodes:
                raise FigmaError(f"文件里找不到节点 {node_id}")
            entry = nodes[node_id]
            node = entry.get("document") or {}
            name = node.get("name", node_id)
        else:
            data = self._get(f"/files/{file_key}", {"depth": "4"})
            node = data.get("document") or {}
            name = data.get("name", file_key)

        lines: List[str] = []
        count = _flatten(node, lines, 0)
        return {
            "file_key": file_key,
            "file_name": data.get("name", ""),
            "node_id": node_id,
            "node_name": name,
            "tree": "\n".join(lines[:400]),
            "node_count": count,
            "last_modified": data.get("lastModified", ""),
            "truncated": len(lines) > 400,
        }


_INDENT = "  "

def _flatten(node: Dict, lines: List[str], depth: int) -> int:
    """Depth-limited structural walk producing an AI-readable text tree."""
    if not isinstance(node, dict) or len(lines) > 420:
        return 0
    ntype = node.get("type", "?")
    name = node.get("name", "")
    label = f"{ntype}"
    if name and name != ntype:
        label += f" “{name}”"
    box = node.get("absoluteBoundingBox") or {}
    if box.get("width"):
        label += f" {round(box.get('width', 0))}x{round(box.get('height', 0))}"
    fills = node.get("fills") or []
    if fills:
        cols = [_fmt_paint(p) for p in fills if p.get("visible") is not False]
        if cols:
            label += f" 填充[{', '.join(cols[:2])}]"
    if ntype == "TEXT":
        chars = node.get("characters", "")
        style = node.get("style") or {}
        fs = style.get("fontSize")
        fw = style.get("fontWeight")
        if fs:
            label += f" {fs}px"
        if fw:
            label += f"/{fw}"
        if chars:
            chars = chars.replace("\n", " ")[:80]
            label += f" 文本:“{chars}”"
    if depth <= 10:
        lines.append(f"{_INDENT * depth}{label}")
    count = 1
    if depth >= 10:
        return count
    for child in node.get("children") or []:
        count += _flatten(child, lines, depth + 1)
    return count


def _fmt_paint(paint: Dict) -> str:
    color = paint.get("color") or {}
    r, g, b = (round(color.get("r", 0) * 255), round(color.get("g", 0) * 255),
               round(color.get("b", 0) * 255))
    a = round(color.get("a", 1), 2)
    return f"#{r:02x}{g:02x}{b:02x}" + (f"@{a}" if a < 1 else "")
