# -*- coding: utf-8 -*-
"""接口契约护栏 + 后端归因的证据校验。

要解决的是首批真实工单里实测到的两个偏置：

  1. 「日志详情接口返回的 resource 结构与列表不一致」——根因写在结论里，`non_frontend`
     却判成 False，然后交了一个前端容错补丁把上游契约错误糊掉。这类补丁能工，代价是
     契约不一致永久留在代码里。
  2. 「把 modelType 传给接口」——typings 与假设冲突时，模型选择用交叉类型断言绕过去，
     而不是停下来问"后端真的接受这个字段名吗"。

所以规则要落在代码里，不能只写在提示词里（提示词只是第二道防线）：

  * **动到 API 契约形状**（请求参数名 / URL / 字段读写）的补丁，那个名字必须在仓库里
    **另有旁证** —— 有一个本次不改动的文件也用它。没有就拒绝这一版，让它要么拿证据，
    要么改判 `backend_issue`。这就是第 2 类偏置的硬拦。
  * **归因后端**的结论必须带可核查的证据：仓库内 `path:line` 引用（会真去读那一行，
    编造的引用会被戳穿），或者本次会话里成功过的 live 接口探测。两者都没有 → 降级成
    `backend_issue_unverified`，不冒充"我已经证明了"。
  * live 探测**只读、只允许白名单主机、只回结构不回数据**：给的是 `key: 类型` 的形状树，
    不含任何字段值。生产数据不该顺着提案 JSON 和知识卡片永久留在你本地。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

# 请求上下文关键字：只有出现在这些标识符附近的名字才算「契约面」改动
_REQUEST_CTX = r"(?:params|query|payload|body|formData|search|filter|data)"
# 对象字面量里的 key：`modelType: x` / `"modelType": x`
_KEY_RE = re.compile(r"\b(?:%s)\s*[:=]" % _REQUEST_CTX)
_KEY_NAME_RE = re.compile(r"[\"]?([A-Za-z_][A-Za-z0-9_]{2,})[\"]?\s*:")
# 只认**写**：`params.foo = x` / `params['foo'] = x` / `delete params.foo`。
# 早先的写法把 `data.list` 这种读也当成改契约，于是任何读响应字段的补丁都被要求
# 交旁证 —— 误拦的代价是模型学会绕检查，比漏拦更贵（`=(?!=|>)` 同时排掉 `==` 与 `=>`）。
_MEMBER_WRITE_RE = re.compile(r"\b(?:%s)\s*(?:\[\s*[\"']?([A-Za-z_][\w-]{2,})"
                              r"[\"']?\s*\]|\.\s*([A-Za-z_][\w-]{2,}))\s*"
                              r"(?:=(?!=|>)|\+=|-=)" % _REQUEST_CTX)
_URL_SEG_RE = re.compile(r"[\"'`]([^\"'`\s]{3,120})[\"'`]?")
_API_PATH_RE = re.compile(r"(/api/[\w/-]+|/v\d+/[\w/-]+)")
_QUERY_KEY_RE = re.compile(r"[?&]([A-Za-z_][\w-]{1,40})=")
# typings 不一致时用来"绕过去"的写法，需要模型解释（不直接拦：合法场景确实存在）
_ESCAPE_RE = re.compile(r"\bas\s+(?:any\b|unknown\b)|@\s*ts-(?:ignore|expect-error)|"
                        # 交叉类型断言（`as Record<string, unknown> & Extra`）——泛型里带
                        # 空格与逗号，字符类不收容就会漏检，而那正是首批第 1 条用来
                        # "绕过 typings 不一致"的确切写法
                        r"\bas\s+[\w<>[\]().,|&\s]{0,60}&")
_CITATION_RE = re.compile(r"([\w./\\-]+\.[A-Za-z0-9]{1,6}):(\d{1,5})(?:-(?:\d{1,5}))?")
_READ_ONLY_METHODS = ("GET", "HEAD")
MAX_SHAPE_LINES = 80
MAX_BODY_BYTES = 400_000


# ------------------------------------------------------------- 契约面改动识别
def contract_tokens(changes: Iterable[Dict]) -> Dict[str, List[str]]:
    """从补丁的 diff 里挑出「动了接口契约」的名字。

    刻意收窄：只有出现在 `params` / `query` / `body` 等请求上下文附近的新增 key、
    成员读写（`params.foo` / `params['foo']`）、URL 路径段与查询参数名才算。
    普通 UI 配置里的对象 key 不算 —— 误拦一次，模型就会学会绕过检查而不是去查证据。
    """
    keys: List[str] = []
    urls: List[str] = []
    for ch in changes or []:
        for line in (ch.get("diff") or "").splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            body = line[1:]
            if _KEY_RE.search(body):
                keys += _KEY_NAME_RE.findall(body)
            for m in _MEMBER_WRITE_RE.finditer(body):
                keys += [g for g in m.groups() if g]
            for path in _API_PATH_RE.findall(body):
                urls.append(path)
            keys += _QUERY_KEY_RE.findall(body)
    keep = []
    for k in keys:
        if k and k not in keep and not _is_js_noise(k):
            keep.append(k)
    return {"fields": keep, "urls": sorted(set(urls))}


def _is_js_noise(tok: str) -> bool:
    return tok.lower() in {
        "type", "data", "params", "query", "body", "code", "name", "value", "label",
        "list", "items", "total", "page", "size", "url", "method", "headers", "config",
    }


def corroborate(repo_root: Path, token: str, exclude: Iterable[str]) -> List[str]:
    """这个契约名字在**本次不改动的文件**里是否另有出处（旁证）。

    返回前 6 条 `路径:行号`。找不到就是找不到 —— 与其让模型猜字段名，不如让它去问后端。
    """
    skip = {str(e or "").replace("\\", "/") for e in exclude}
    hits: List[str] = []
    root = Path(repo_root)
    for f in _iter_source(root):
        rel = str(f.relative_to(root)).replace("\\", "/")
        if rel in skip:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if token not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"\b%s\b" % re.escape(token), line):
                hits.append(f"{rel}:{i}")
                break
        if len(hits) >= 6:
            break
    return hits


def _iter_source(root: Path):
    """粗扫源码文件（跳过依赖与产物目录），带总量上限，别把大仓扫穿。"""
    skip = {"node_modules", "dist", "build", "out", ".git", "coverage", ".turbo",
            ".cache", "public", "static", ".venv", "__pycache__"}
    exts = {".ts", ".tsx", ".js", ".jsx", ".vue", ".mjs", ".cjs", ".d.ts", ".json"}
    n = 0
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames:
            if not any(name.lower().endswith(e) for e in exts):
                continue
            n += 1
            if n > 6000:
                return
            yield Path(dirpath) / name


def escape_hatches(changes: Iterable[Dict]) -> List[str]:
    """新增行里用来绕过类型系统的位置（typings 不一致时最常被拿来糊过去）。"""
    out = []
    for ch in changes or []:
        for line in (ch.get("diff") or "").splitlines():
            if line.startswith("+") and not line.startswith("+++") and _ESCAPE_RE.search(line):
                out.append(f"{ch.get('file_path')}:{line[1:].strip()[:120]}")
    return out[:8]


# ------------------------------------------------------------- 证据引用核查
def parse_citations(text: str) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    for m in _CITATION_RE.finditer(str(text or "")):
        path, line = m.group(1).replace("\\", "/"), int(m.group(2))
        if (path, line) not in out:
            out.append((path, line))
    return out[:12]


def verify_citations(reader, citations: List[Tuple[str, int]]) -> Tuple[List[Dict], List[str]]:
    """逐条真去读那一行：编造的路径/行号会被戳穿。"""
    good: List[Dict] = []
    bad: List[str] = []
    for path, line in citations:
        text = ""
        try:
            text = reader.read_file(path, max(1, line - 1), line + 1) if reader else ""
        except Exception:
            text = ""
        body = " ".join((text or "").split())
        # RepoReader 把"文件不存在/越界/为空"也作为文本返回（这是给模型看的友好提示），
        # 但核查证据时它就是"引用无效"——不挡掉的话，编一个路径就能骗取"有据"结论。
        if body and not body.startswith(("[错误]", "[空]", "[警告]")):
            good.append({"ref": f"{path}:{line}", "snippet": body[:200]})
        else:
            bad.append(f"{path}:{line}")
    return good, bad


def normalize_verdict(obj: Dict) -> Dict:
    """清洗模型给的归因结论，字段固定，多余内容不接。"""
    kind = str(obj.get("kind") or "").strip().lower()
    if kind not in ("backend_issue", "adapted_pending_backend"):
        return {"ok": False, "error": "kind 只能是 backend_issue / adapted_pending_backend"}
    contract = obj.get("contract") if isinstance(obj.get("contract"), dict) else {}
    return {
        "ok": True,
        "kind": kind,
        "endpoint": str(obj.get("endpoint") or contract.get("endpoint") or "")[:200],
        "expected": str(obj.get("expected") or contract.get("expected") or "")[:600],
        "actual": str(obj.get("actual") or contract.get("actual") or "")[:600],
        "handoff": str(obj.get("handoff") or obj.get("reason") or "")[:1200],
        "cleanup": str(obj.get("cleanup") or "")[:800],
        "citations": [str(x)[:160] for x in (obj.get("citations") or [])[:8]],
    }


# ------------------------------------------------------------------ 形状摘要
def shape_of(payload, limit: int = MAX_SHAPE_LINES) -> List[str]:
    """`a.b: string` 式结构树，**不含任何字段值**。

    判契约不匹配需要的是 key 与类型（`type` 是字符串还是 `{code,label}`），不是数据本身；
    把值带进提案 JSON 与知识卡片，等于把生产数据留在本地仓库里。
    """
    out: List[str] = []

    def walk(node, path, depth):
        if len(out) >= limit or depth > 6:
            return
        if isinstance(node, dict):
            if not node:
                out.append(f"{path or '$'}: object{{}}")
                return
            for k, v in node.items():
                p = f"{path}.{k}" if path else str(k)
                if isinstance(v, (dict, list)):
                    walk(v, p, depth + 1)
                else:
                    out.append(f"{p}: {type(v).__name__}")
        elif isinstance(node, list):
            out.append(f"{path or '$'}: array(len={len(node)})")
            if node:
                walk(node[0], f"{path}[]" if path else "[]", depth + 1)
        else:
            out.append(f"{path or '$'}: {type(node).__name__}")

    walk(payload, "", 0)
    return out


# 响应上下文：新增的成员访问出现在这些名字后面，算「读接口返回的形状」
_RESPONSE_CTX = r"(?:response|resp|res|result|ret|payload|body|data)"
_CTX_WORDS = {"response", "resp", "res", "result", "ret", "payload", "body", "data"}
# 整条点链一次拿全：`response.data.list.rows` 里的 data/list/rows 都算读接口字段。
# 只匹配"紧邻的一个点"会漏掉链式访问（首批第 3 条就是 response.data.list），
# 于是「把 list/rows/items 全兼容一遍」这种最该拦的行为反而没被拦住。
_RESPONSE_CHAIN_RE = re.compile(r"\b(?:%s)\b\s*((?:\??\.\s*[A-Za-z_$][\w$]*|\??\[\s*[\"'][^\"']+[\"']\s*\])+)"
                                % _RESPONSE_CTX)
_CHAIN_SEG_RE = re.compile(r"[A-Za-z_$][\w$]*")
# 「全都兼容一遍」的特征：一次新增 ≥3 个响应字段读取。
# 这不是修缺陷，是拿容错代替查证 —— 真实首批里模型自己写了"没有接口样本，无法确认字段"，
# 然后把 list/rows/items/data 全列进候选。必须有证据才允许这种改动。
SHAPE_GUESS_THRESHOLD = 3


def shape_guessing(changes: Iterable[Dict]) -> List[Dict]:
    """挑出「一次新增 ≥3 个响应字段读取」的补丁块，附带每个字段是否有旁证。"""
    out: List[Dict] = []
    for ch in changes or []:
        added: List[str] = []
        for line in (ch.get("diff") or "").splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for m in _RESPONSE_CHAIN_RE.finditer(line[1:]):
                for tok in _CHAIN_SEG_RE.findall(m.group(1)):
                    if tok not in added and not _is_shape_noise(tok):
                        added.append(tok)
        if len(added) >= SHAPE_GUESS_THRESHOLD:
            out.append({"file_path": ch.get("file_path"), "fields": added})
    return out


def _is_shape_noise(tok: str) -> bool:
    """噪声 = 方法/内建 + **信封词本身**。

    `response.data.list` 里要盯的是 `list`；把信封词 `data` 也算成一个"新增字段"，
    会让"补两处 content/records"这种正常改动凑够 3 个而被误拦 —— 误拦的代价是模型
    学会怎么绕检查，比漏拦更贵。
    """
    if tok in _CTX_WORDS:
        return True
    return tok in {"length", "map", "filter", "forEach", "includes", "then", "catch",
                   "finally", "valueOf", "toString", "push", "pop", "shift",
                   "slice", "some", "every", "find", "findIndex", "flatMap", "reduce",
                   "sort", "reverse", "concat", "join", "split", "trim", "replace",
                   "replaceAll", "padStart", "at", "keys", "values", "entries",
                   "hasOwnProperty", "constructor", "prototype", "ok", "status",
                   "message", "msg", "code"}


def needs_evidence(fields: Iterable[str]) -> List[str]:
    """需要证据支撑的响应字段名（去重、保序）。"""
    out: List[str] = []
    for f in fields or []:
        if f and f not in out:
            out.append(f)
    return out


# ---------------------------------------------------------------- live 探测
def api_probe(url: str, *, allowed_hosts: List[str], cookies: Optional[Dict[str, str]] = None,
              timeout: int = 15) -> Dict:
    """只读探一次接口，只回结构与状态码，不回数据。

    三道硬约束：方法只许 GET/HEAD；主机必须在白名单里（默认只允许配置的被测应用域名）；
    响应体只用来算形状，不落盘、不进提案。探不到就如实说探不到 —— 「没证据」必须比
    「假装有证据」更容易被看见。
    """
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return {"ok": False, "error": "URL 无法解析"}
    if parts.scheme not in ("http", "https"):
        return {"ok": False, "error": "只允许 http(s)"}
    if not parts.netloc:
        return {"ok": False, "error": "缺少主机名"}
    hosts = {h.lower() for h in (allowed_hosts or []) if h}
    if parts.hostname and (parts.hostname.lower() not in hosts
                           and not any(parts.hostname.lower().endswith(":" + h) or
                                       parts.hostname.lower() == h for h in hosts)):
        return {"ok": False,
                "error": f"目标主机 {parts.hostname} 不在允许列表里（只读探测也不能乱打）"}
    safe_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))  # 丢查询参数值
    try:
        resp = requests.get(parts.geturl(), timeout=timeout, allow_redirects=False,
                            cookies=cookies or None, stream=True)
    except requests.RequestException as e:
        return {"ok": False, "error": f"请求失败：{type(e).__name__}: {e}", "url": safe_url}
    body = b""
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                break
    except requests.RequestException:
        pass
    finally:
        resp.close()
    ctype = str(resp.headers.get("content-type") or "")
    info: Dict = {"ok": 200 <= resp.status_code < 400, "method": "GET", "url": safe_url,
                  "query_param_names": sorted({m.group(1) for m in
                                               _QUERY_KEY_RE.finditer(
                                                   "?" + (parts.query or ""))}),
                  "status": resp.status_code, "content_type": ctype.split(";")[0],
                  "bytes": len(body)}
    if "json" not in ctype.lower():
        info["ok"] = bool(info["ok"])
        info["note"] = "响应不是 JSON（可能被网关/登录页拦了），只能当状态码证据"
        return info
    try:
        info["shape"] = shape_of(json.loads(body.decode("utf-8", "replace")))
    except ValueError:
        info["error"] = "响应声称是 JSON 但解析失败"
    return info


def render_handoff(defect: Dict, agent: Dict, proposal_id: str = "") -> str:
    """把归因结论排成一条能直接贴给后端的交接说明。

    刻意只带**结构与字段名**，不带响应里的真实数据（与 `api_probe` 同一口径）——
    交接说明会被写进 ONES 工单评论，那是会扩散给一屋子人的地方。
    """
    v = (agent or {}).get("verdict") or {}
    probes = [p for p in ((agent or {}).get("probes") or []) if p.get("ok")]
    if not v.get("kind"):
        return ""
    lines = ["【AI 缺陷诊断 · 后端交接】" if v["kind"] == "backend_issue"
             else "【AI 缺陷诊断 · 前端已兼容，等上游契约】"]
    did = (defect or {}).get("id") or ""
    title = (defect or {}).get("title") or ""
    if did or title:
        lines.append(f"缺陷：{did} {title}".strip())
    if v.get("endpoint"):
        lines.append(f"接口：{v['endpoint']}")
    if v.get("expected"):
        lines.append(f"期望：{v['expected']}")
    if v.get("actual"):
        lines.append(f"实际：{v['actual']}")
    if v.get("handoff"):
        lines.append(f"要后端改什么：{v['handoff']}")
    if v.get("cleanup"):
        lines.append(f"前端这段兼容，上游修好后应撤：{v['cleanup']}")
    cites = (v.get("citations_ok") or [])[:6]
    if cites:
        lines.append("证据（本次改动的文件之外另有出处）：" + "、".join(cites))
    for p in probes[:2]:
        lines.append(f"只读探测 {p.get('method', 'GET')} {p.get('url')} → HTTP {p.get('status')}")
        for row in (p.get("shape") or [])[:14]:
            lines.append(f"  · {row}")
    if not v.get("verified"):
        lines.append("⚠ 证据不足：以上归因未经可核查证据支撑，请人工先确认再改。")
    if proposal_id:
        lines.append(f"（由 ones-defect-auto-fixer 生成，提案 {proposal_id}）")
    return "\n".join(lines)[:2500]


def load_storage_cookies(path: Path, host: str) -> Dict[str, str]:
    """从 Playwright 的 storage_state 里取该主机的 cookie（复用登录态，不重新登录）。"""
    out: Dict[str, str] = {}
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for c in state.get("cookies") or []:
        domain = str(c.get("domain") or "").lstrip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            out[str(c.get("name"))] = str(c.get("value"))
    return out
