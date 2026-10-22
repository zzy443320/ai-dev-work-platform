# -*- coding: utf-8 -*-
"""自测：开发完一个需求后的自测环节 —— 三条链路共用一套「开页面 → 采证 → 逐条判定」。

为什么单独一个模块（而不是塞进「代码测试」）
--------------------------------------------
代码测试的输出是**测试文件**，判据是之后闸门跑出来的红绿；自测的输出是**逐条
判定表**（通过 / 不通过 / 无法判断），输入是别人给的用例、需求文档、设计稿 ——
三种输入、一个页面、一份报告。形态不同，混在一起会让两边都不好读。

它**只读**：不起分支、不改代码、不写目标仓库。不通过项交给「缺陷修复 / 需求
开发」去改，那两条链路才有闸门与备份。

三条链路
--------
``case``  测试用例：优先从 ONES 工单的「测试情况」关联用例拉，拉不到就用手贴
          文本（ONES 各部署的测试接口形状未证实，见 OnesClient.fetch_test_cases）。
``req``   需求文档 → 可判定验收点 → 逐条对照真实页面。
``figma`` Figma 画布 → 文本节点规格 → 与页面**实际计算样式**做数字比对。

判定纪律（沿用 repro.py 的三态口径）
------------------------------------
``pass`` / ``fail`` / ``unknown``。环境不可达、登录没成、页面空白、模型没跑起来，
一律 ``unknown`` 并写清原因 —— **绝不把「没看到问题」算成通过**。这条纪律是整个
工具唯一的信任基础：一份把白屏判成通过的报告比不做自测更糟。

Figma 比对为什么先做数字而不是先看图
------------------------------------
视觉模型说「看起来差不多」既不可复现也不可追责。这里先把 Figma TEXT 节点的
字号 / 字重 / 颜色 / 宽度与页面 ``getComputedStyle`` + ``getBoundingClientRect``
逐项比对（带容差），视觉判读只用来给不通过项写人话说明。位置（x/y）**不比对**：
页面有滚动与容器偏移，画布的绝对坐标对不上，硬比会得到一堆假阳性 —— 这一点在
报告的 notes 里如实写明。
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import usage
from .figma_fetcher import FigmaClient, FigmaError
from .ones_fetcher import OnesClient

LANE_CASE = "case"
LANE_REQ = "req"
LANE_FIGMA = "figma"
LANES = (LANE_CASE, LANE_REQ, LANE_FIGMA)
LANE_LABELS = {LANE_CASE: "测试用例", LANE_REQ: "需求一致性", LANE_FIGMA: "设计稿一致性"}
LANE_KEYS = {LANE_CASE: "cases", LANE_REQ: "req_points", LANE_FIGMA: "figma_nodes"}

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNKNOWN = "unknown"
STATUS_LABELS = {STATUS_PASS: "通过", STATUS_FAIL: "不通过", STATUS_UNKNOWN: "无法判断"}

MAX_ITEMS = 20          # 一条链路最多判多少项（再多模型看不过来，人也读不完）
MAX_ROUTES = 4
MAX_ACTIONS = 6         # 单条用例最多替它执行几个动作
MAX_PAGE_TEXT = 3000    # 交给模型的页面可见文本上限（字符）
# 重型 SPA（工作流设计器等）首屏挂载要 10~20s：每 2s 轮询一次可见文本，
# 连续 2 轮不再增长才算「稳定」，最多轮询 20 次（~40s）。
_SETTLE_POLL_MS = 2000
_SETTLE_MAX_POLLS = 20
_SETTLE_MIN_MS = 12000  # 真空白页至少也要等这么久才下结论（假空白高发期）
_JUDGE_BATCH = 8        # 判定分批：一次输出十几条 JSON 易被截断全批报废
DEADLINE_SECONDS = 240  # 一次自测的墙钟预算：浏览器 + 模型都在里面

_UI_STEP_RE = re.compile(r"(点击|点一下|点|输入|填写|填入|访问|打开|进入|跳转)")
# 路由：允许**裸 "/"**（首页是最常写的那个），但要求前面不是单词字符 ——
# 否则「2026/10/21」「a/b」里的斜杠都会被当成路由。
_ROUTE_RE = re.compile(r"(?<![\w])(/[\w\-./#?=&]{0,80})")
_FIGMA_TEXT_RE = re.compile(r"TEXT\s+“(?P<name>[^”]*)”(?P<rest>.*)$")
_FIGMA_SIZE_RE = re.compile(r"(?P<w>\d+)x(?P<h>\d+)")
_FIGMA_FILL_RE = re.compile(r"填充\[(?P<color>#[0-9a-fA-F]{6})")
_FIGMA_FONT_RE = re.compile(r"(?P<size>[\d.]+)px/(?P<weight>\d+)")
_FIGMA_CHARS_RE = re.compile(r"文本:“(?P<chars>[^”]*)”")
_RGB_RE = re.compile(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)")

_CASE_SYSTEM = (
    "你是前端自测执行员。只依据给到的证据（页面截图、页面可见文本、控制台报错）"
    "判断每条测试用例，只输出 JSON。"
    "**证据不足以判断的必须回答 unknown**，绝不允许因为「没看到报错」就判 pass，"
    "也不允许按用例文字想象页面内容。"
)
_REQ_SYSTEM = (
    "你是需求验收员。逐条核对验收点在真实页面上是否成立，只输出 JSON。"
    "页面文本与截图里都找不到依据的，回答 unknown；不要按需求文字替页面补内容。"
)
_SPLIT_SYSTEM = (
    "你是测试工程师，把需求文档拆成可在真实页面上核对的验收点，只输出 JSON。"
    "验收点必须是「打开页面就能观察或操作验证」的检查项——元素存在、文案、"
    "状态、交互后的结果；文档自身的章节标题、编号、附件名、背景/术语说明"
    "**不是**验收点，一律不要输出。"
)
_PARSE_SYSTEM = (
    "你是测试工程师，把一段人工写的测试用例整理成结构化条目，只输出 JSON。"
    "不要新增用例，不要改写预期结果的含义；原文没写的字段留空字符串。"
)

_PROBE_JS = """
(texts) => {
  const out = {};
  const all = document.querySelectorAll('body *');
  for (const t of texts) {
    let hit = null;
    for (const el of all) {
      if (el.children.length) continue;
      if ((el.textContent || '').trim() !== t) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const cs = getComputedStyle(el);
      hit = {x: Math.round(r.x), y: Math.round(r.y),
             w: Math.round(r.width), h: Math.round(r.height),
             fontSize: parseFloat(cs.fontSize) || 0,
             fontWeight: String(cs.fontWeight || ''),
             color: cs.color || '', lineHeight: cs.lineHeight || ''};
      break;
    }
    out[t] = hit;
  }
  return out;
}
"""

_BLANK_JS = """
() => {
  const body = document.body;
  const txt = (body && body.innerText ? body.innerText : '').trim();
  return {chars: txt.length, text: txt.slice(0, 400)};
}
"""


class SelfTestError(RuntimeError):
    """参数/环境层面的业务错误（路由层转 4xx）。"""


# ------------------------------------------------------------------ 小工具
def normalize_lanes(value) -> List[str]:
    """['case','req'] / 'case,req' / ['all'] → 有序的合法链路列表。"""
    if isinstance(value, str):
        raw = [x.strip() for x in re.split(r"[,，\s]+", value) if x.strip()]
    elif isinstance(value, (list, tuple)):
        raw = [str(x).strip() for x in value]
    else:
        raw = []
    if not raw or "all" in raw:
        return list(LANES)
    return [x for x in LANES if x in raw]


def parse_routes(text: str, default: str = "/") -> List[str]:
    """'/,/order/list' → ['/', '/order/list']；不合法/为空退回 default。"""
    found = [m.group(1) for m in _ROUTE_RE.finditer(str(text or ""))]
    out: List[str] = []
    for r in found:
        r = r.rstrip(".,，。;；")
        if r not in out and not r.startswith("/api/") and not r.startswith("/static/"):
            out.append(r)
    if not out and str(text or "").strip():
        # 用户可能只写了「订单列表」这种自然语言，宁可退回默认页也不假装命中
        out = [default]
    return (out or [default])[:MAX_ROUTES]


def _json_from_text(text: str):
    """从模型输出里挖 JSON（容忍 ```json 围栏、前后废话、尾部截断）。"""
    s = str(text or "").strip()
    if not s:
        return None
    m = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = s.find(opener), s.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 尾部截断修复：reasoning 与 content 共享 max_tokens 的模型会把 JSON 掐断，
    # 直接解析失败就丢掉全部结果太亏——截到最后一个完整条目，补齐括号捞回来。
    return _json_repair_truncated(s)


def _json_repair_truncated(s: str):
    """截断的 JSON 容错：引号感知扫描，截到最后一个完整闭合的元素再补齐外层。"""
    i = s.find("{")
    if i == -1:
        return None
    body = s[i:]

    def stack_of(t: str) -> List[str]:
        """引号感知地数未闭合的括号栈（'{'/'['）。"""
        out, in_str, esc = [], False, False
        for ch in t:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch in "{[":
                    out.append(ch)
                elif ch in "}]" and out and (
                        (out[-1] == "{" and ch == "}") or (out[-1] == "[" and ch == "]")):
                    out.pop()
        return out

    last_complete = -1
    in_str = esc = False
    depth = 0
    for k, ch in enumerate(body):
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str and ch in "{[":
            depth += 1
        elif not in_str and ch in "}]":
            depth -= 1
            if depth >= 1:          # 顶层数组/对象里刚闭合了一个完整元素
                last_complete = k
    if last_complete == -1:
        return None

    def close(t: str) -> str:
        tail = stack_of(t)
        return t + "".join("]" if c == "[" else "}" for c in reversed(tail))

    candidate = close(body[:last_complete + 1])
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # 可能带着半截的尾逗号/不完整键，去掉最后一个逗号之后的残段再试
        m = candidate.rfind(",")
        if m != -1:
            try:
                return json.loads(close(candidate[:m + 1].rstrip(",") + "]"
                                        if candidate[m:].find("]") == -1
                                        else candidate[:m + 1]))
            except json.JSONDecodeError:
                pass
        return None


def _is_mock(ai) -> bool:
    cfg = getattr(ai, "cfg", None)
    return bool(cfg is None or getattr(cfg, "is_mock", False))


def _strs(value, limit: int = MAX_ITEMS) -> List[str]:
    if isinstance(value, str):
        items = [ln.strip(" -·\t") for ln in value.splitlines()]
    elif isinstance(value, (list, tuple)):
        items = [str(x).strip() for x in value]
    else:
        items = []
    return [x for x in items if x][:limit]


# ------------------------------------------------------------- 用例来源
def fetch_ones_cases(issue: str, ones_cfg: Dict) -> Dict:
    """从 ONES 拉工单关联的测试用例（best-effort）。

    各部署的测试管理接口形状未证实，所以这里和 ``fetch_attachments`` 同一套纪律：
    候选通道逐个试，失败只记 note、不抛异常。返回 ``{issue, items, note}``。
    真正判读依赖的字段（步骤/预期）如果拉不到，界面会提示改用「手动粘贴用例」。
    """
    issue = str(issue or "").strip()
    if not issue:
        return {"issue": "", "items": [], "note": "没有填工单"}
    if not (ones_cfg or {}).get("base_url"):
        return {"issue": issue, "items": [], "note": "ONES 未配置 base_url"}
    try:
        client = OnesClient(
            base_url=str(ones_cfg.get("base_url") or ""),
            token=str(ones_cfg.get("token") or ""),
            project_uuid=str(ones_cfg.get("project_uuid") or ""),
            team_uuid=str(ones_cfg.get("team_uuid") or ""),
            email=str(ones_cfg.get("email") or ""),
            password=str(ones_cfg.get("password") or ""),
            testcase_apis=ones_cfg.get("testcase_apis") or None,
        )
    except Exception as e:                                   # noqa: BLE001
        return {"issue": issue, "items": [], "note": f"ONES 客户端初始化失败：{e}"}
    try:
        items = client.fetch_test_cases(issue)
    except Exception as e:                                   # noqa: BLE001
        return {"issue": issue, "items": [],
                "note": f"测试用例拉取失败：{type(e).__name__}: {e}"}
    return {"issue": issue, "items": items,
            "note": client.last_testcase_note or ("已取到用例" if items else "没有取到用例")}


def _normalize_cases(raw_items: List[Dict]) -> List[Dict]:
    """把来源各异的用例洗成 {id,title,steps,expected,route}。"""
    out: List[Dict] = []
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("name") or it.get("case_name")
                    or it.get("summary") or "").strip()
        if not title:
            continue
        steps = it.get("steps") or it.get("step") or it.get("procedure") or ""
        if isinstance(steps, (list, tuple)):
            steps = "\n".join(
                str(s.get("action") or s.get("content") or s) if isinstance(s, dict) else str(s)
                for s in steps)
        expected = str(it.get("expected") or it.get("expect") or it.get("result")
                       or it.get("expectation") or "").strip()
        route = ""
        m = _ROUTE_RE.search(f"{title}\n{steps}\n{expected}")
        if m:
            route = m.group(1).rstrip(".,，。")
        out.append({"id": f"C{len(out) + 1}", "title": title[:200],
                    "steps": str(steps)[:1200], "expected": expected[:600],
                    "route": route})
    return out[:MAX_ITEMS]


def _parse_cases_text(ai, text: str) -> List[Dict]:
    """手贴的用例文本 → 结构化条目。模型不可用时退回「一行一条」。"""
    text = str(text or "").strip()
    if not text:
        return []
    if not _is_mock(ai):
        prompt = (
            "把下面的测试用例文本整理成条目。字段：id（C1 起）、title（用例名）、"
            "steps（操作步骤，逐行，原文照抄）、expected（预期结果）、"
            "route（用例里出现的页面路径，没有就留空）。\n"
            '只输出 JSON：{"items":[{"id":"C1","title":"","steps":"","expected":"","route":""}]}\n'
            f"用例原文：\n{text[:12000]}"
        )
        try:
            with usage.step("selftest_parse"):
                res = ai.complete_text(prompt, system=_PARSE_SYSTEM, max_tokens=2500)
            data = _json_from_text(res.get("text") or "")
            items = (data or {}).get("items") if isinstance(data, dict) else data
            parsed = _normalize_cases(items if isinstance(items, list) else [])
            if parsed:
                return parsed
        except Exception:                                    # noqa: BLE001
            pass
    lines = [ln.strip(" -*\t") for ln in text.splitlines() if ln.strip()]
    # 人工写的用例几乎都带序号：「1.」「2、」「(3)」—— 不剥掉的话用例名会变成
    # 「1. 空列表显示占位图」，后面在报告里搜都对不上原文。
    lines = [re.sub(r"^(?:\(?\d{1,2}[.、)）]\s*)", "", ln).strip() for ln in lines]
    return _normalize_cases([{"title": ln} for ln in lines if ln])


def _load_cases(ai, ctx: Dict, settings: Dict) -> Dict:
    """用例来源优先级：手贴文本 > ONES 工单。返回 {items, note, source}。"""
    text = str(ctx.get("cases_text") or "").strip()
    if text:
        return {"items": _parse_cases_text(ai, text), "note": "用例来自手贴文本",
                "source": "text"}
    issue = str(ctx.get("issue") or "").strip()
    if issue:
        got = fetch_ones_cases(issue, settings.get("ones") or {})
        return {"items": _normalize_cases(got.get("items") or []),
                "note": got.get("note", ""), "source": "ones"}
    return {"items": [], "note": "既没有 ONES 工单也没有手贴用例", "source": ""}


# ------------------------------------------------------------- 需求验收点
_TOOL_NOTE_RE = re.compile(
    r"^\s*(#{1,6}\s*)?(附件\s*\||（用户上传的|（中间部分已省略|…（(需求)?正文超过|"
    r"需求文档：|本节来源|以下为附件)")

def _strip_tool_notes(doc: str) -> str:
    """去掉正文里**工具自己拼的元信息行**（附件来源标注、截断说明）。

    这些行是 merge_doc_text / 截断逻辑加的，不是需求内容——不滤掉的话，
    拆分模型（或行切分兜底）会把「### 附件 |PRD-xxx.pdf|」当成一条验收点，
    然后判定模型认真地去页面里找这个文件名，产出荒唐结论。
    """
    return "\n".join(ln for ln in str(doc or "").splitlines()
                     if not _TOOL_NOTE_RE.match(ln))


def _is_doc_junk(title: str) -> bool:
    """一条「验收点」是否其实是文档结构残留（标题行、元信息）。"""
    t = str(title or "").strip()
    if not t:
        return True
    if t.startswith("#") or _TOOL_NOTE_RE.match(t):
        return True
    # 「XXX：」/「XXX:」结尾且没有更多内容的多为章节标题（如「业务背景：」）
    if re.match(r"^[^，。；]{2,20}[：:]$", t):
        return True
    return False


def _split_req_points(ai, doc: str) -> List[Dict]:
    """需求文档 → 可判定的验收点。失败时退回按行/项目符号切分。"""
    doc = str(doc or "").strip()
    if not doc:
        return []
    if not _is_mock(ai):
        # 长文档**留头也留尾**：需求文档的「背景/目标」在开头，「验收标准」通常在
        # 末尾（PDF 抽取本身也是保头尾），只截前 14000 字符会把最可判定的那段丢掉。
        # 上传整份 PRD（附件合并后可达 6 万字符）时这一点尤其明显。
        body = _strip_tool_notes(doc)
        body = body if len(body) <= 14000 else (
            body[:8000] + "\n\n…（中间部分已省略，末尾的验收标准见下）…\n\n" + body[-6000:])
        prompt = (
            "把下面这份需求文档拆成**可以在页面上逐条核对**的验收点，"
            f"最多 {MAX_ITEMS} 条。\n"
            "硬性规则：\n"
            "1. 每条必须是「打开页面就能观察或操作验证」的检查项：界面元素存在、"
            "文案、状态、交互后的结果。功能名要翻译成它的界面落点——"
            "例如「支持消息通知节点」应拆成「左侧节点面板存在『消息通知』节点」，"
            "而不是拿功能名整句去页面里找。\n"
            "2. **禁止**输出文档自身的内容当验收点：章节标题、文档编号（如 PRD-D）、"
            "附件名、背景/术语/审批流程说明、「### 附件」这类来源标注。\n"
            "3. 页面本身验证不了、需要人工操作或后端行为的条目，也要输出，"
            "但加 \"verify\":\"manual\"，预期里写清需要做什么（如『拖入消息通知节点"
            "并配置接收人后保存』）。\n"
            "4. 每条要具体，不要写成「功能正常」这种无法判定的句子。\n"
            '只输出 JSON：{"points":[{"id":"R1","title":"验收点一句话",'
            '"expected":"页面上应看到的具体内容/行为","verify":"page|manual"}]}\n'
            f"需求文档：\n{body}"
        )
        try:
            with usage.step("selftest_req_split"):
                res = ai.complete_text(prompt, system=_SPLIT_SYSTEM, max_tokens=3000)
            data = _json_from_text(res.get("text") or "")
            points = (data or {}).get("points") if isinstance(data, dict) else data
            out = []
            for i, p in enumerate(points if isinstance(points, list) else []):
                if isinstance(p, dict) and str(p.get("title") or "").strip():
                    out.append({"id": str(p.get("id") or f"R{i + 1}"),
                                "title": str(p.get("title"))[:300],
                                "expected": str(p.get("expected") or "")[:600],
                                "verify": "manual" if str(p.get("verify") or "") == "manual"
                                          else "page"})
            out = [p for p in out if not _is_doc_junk(p["title"])]
            if out:
                return out[:MAX_ITEMS]
        except Exception:                                    # noqa: BLE001
            pass
    lines = _strs(_strip_tool_notes(doc), MAX_ITEMS)
    return [{"id": f"R{i + 1}", "title": ln[:300], "expected": "",
             "verify": "page"} for i, ln in enumerate(lines)
            if not _is_doc_junk(ln)]


# ------------------------------------------------------------- Figma 规格
def _figma_specs(settings: Dict, figma_url: str) -> Dict:
    """拉设计稿并解析出「文本节点规格」列表。

    只取带 ``characters`` 的 TEXT 节点：这是唯一能可靠映射到页面元素的锚点
    （按文本内容找 DOM）。容器/图片节点留待后续用选择器或视觉区块补。
    """
    url = str(figma_url or "").strip()
    if not url:
        return {"ok": False, "note": "没有填 Figma 链接", "nodes": [], "tree": ""}
    token = str((settings.get("figma") or {}).get("token") or "").strip()
    if not token:
        return {"ok": False, "note": "Figma Token 未配置（配置页 → Figma）",
                "nodes": [], "tree": ""}
    try:
        data = FigmaClient(token).fetch(url, "")
    except FigmaError as e:
        return {"ok": False, "note": f"Figma 取稿失败：{e}", "nodes": [], "tree": ""}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "note": f"Figma 取稿异常：{type(e).__name__}", "nodes": [],
                "tree": ""}
    tree = str(data.get("tree") or "")
    nodes: List[Dict] = []
    seen = set()
    for line in tree.splitlines():
        m = _FIGMA_TEXT_RE.search(line)
        if not m:
            continue
        rest = m.group("rest")
        cm = _FIGMA_CHARS_RE.search(rest)
        chars = (cm.group("chars") if cm else m.group("name")).strip()
        # 只拿短文本当锚点：长段落按文本匹配几乎必然失败，白占预算
        if not chars or len(chars) > 24 or chars in seen:
            continue
        seen.add(chars)
        sm = _FIGMA_SIZE_RE.search(rest)
        fm = _FIGMA_FILL_RE.search(rest)
        gm = _FIGMA_FONT_RE.search(rest)
        nodes.append({
            "text": chars,
            "width": int(sm.group("w")) if sm else 0,
            "height": int(sm.group("h")) if sm else 0,
            "color": (fm.group("color").lower() if fm else ""),
            "fontSize": float(gm.group("size")) if gm else 0,
            "fontWeight": int(gm.group("weight")) if gm else 0,
        })
        if len(nodes) >= MAX_ITEMS:
            break
    note = f"取到 {data.get('node_count', 0)} 个节点，其中可对齐的文本锚点 {len(nodes)} 个"
    if not nodes:
        note += "（设计稿里没有短文本节点，或取稿被截断）"
    return {"ok": True, "note": note, "nodes": nodes, "tree": tree,
            "file_name": data.get("file_name", ""), "node_name": data.get("node_name", "")}


# ------------------------------------------------------------- 浏览器采证
def _probe(settings: Dict, base_url: str, routes: List[str], evidence_dir: Path,
           progress=None) -> Dict:
    """逐路由开页面采证：截图 + 可见文本 + console/pageerror + 空白探测。

    单次 launch_persistent_context，profile 独立于「内嵌浏览器」页签（那个是
    单会话 + 文件锁，抢同一个 profile 会直接起不来）。
    """
    base = str(base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "没有填本地环境地址", "pages": {}}
    cfg = settings.get("playwright") or {}
    headless = bool(cfg.get("headless", True))
    pl = settings.get("pagelogin") or {}
    creds = (str(pl.get("email") or "").strip(), str(pl.get("password") or "")) \
        if pl.get("enabled") else ("", "")
    profile = evidence_dir / "_selftest_profile"
    profile.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    pages: Dict[str, Dict] = {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return {"ok": False, "error": f"playwright 未安装：{e}", "pages": {}}

    deadline = time.time() + DEADLINE_SECONDS
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=headless,
                viewport={"width": 1440, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
        except Exception as e:                               # noqa: BLE001
            return {"ok": False, "error": f"浏览器启动失败：{e}", "pages": {}}
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(15000)
        console_errors: List[str] = []
        page_errors: List[str] = []
        page.on("console", lambda m: console_errors.append(m.text[:300])
                if getattr(m, "type", "") in ("error", "warning") else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)[:300]))

        for i, route in enumerate(routes):
            url = base + (route if route.startswith("/") else "/" + route)
            if time.time() > deadline:
                pages[route] = {"route": route, "url": url, "ok": False,
                                "error": "超过自测时间预算，未采证"}
                continue
            if progress:
                progress("采证", f"{route} · {i + 1}/{len(routes)}")
            console_errors.clear()
            page_errors.clear()
            info: Dict = {"route": route, "url": url}
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:                            # noqa: BLE001
                    pass
                info["title"] = page.title()
                info["final_url"] = page.url
                login_note = ""
                if _looks_like_login(page.url):
                    login_note = _try_login(page, creds)
                    if login_note == "已代填登录":
                        try:
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:                    # noqa: BLE001
                            pass
                        info["title"] = page.title()
                        info["final_url"] = page.url
                info["login"] = login_note or "沿用持久 profile 的登录态"
                # SPA 的首屏渲染是异步的，而且重型页面（工作流设计器 / 大表格）
                # networkidle 之后还要 10~20s 才挂载完——固定等几次根本不够，
                # 等「文本连续两轮不再增长」才算稳定（上限 ~40s，防止死等）。
                blank = page.evaluate(_BLANK_JS)
                stable = 0
                settled_ms = 0
                for _ in range(_SETTLE_MAX_POLLS):
                    if time.time() > deadline:
                        break
                    page.wait_for_timeout(_SETTLE_POLL_MS)
                    settled_ms += _SETTLE_POLL_MS
                    cur = page.evaluate(_BLANK_JS)
                    cur_chars = int((cur or {}).get("chars") or 0)
                    if cur_chars > int((blank or {}).get("chars") or 0):
                        blank = cur
                    stable = stable + 1 if cur_chars == int((blank or {}).get("chars") or 0) else 0
                    # 内容页：稳定 2 轮就走；空白页：至少等满 _SETTLE_MIN_MS
                    # （晚期挂载高发在 10~20s），之后稳定了才允许提前收场。
                    if stable >= 2 and (cur_chars >= 20 or settled_ms >= _SETTLE_MIN_MS):
                        break
                shot = evidence_dir / f"selftest_{ts}_p{i}.png"
                page.screenshot(path=str(shot), full_page=False)
                chars = int((blank or {}).get("chars") or 0)
                info.update({
                    "ok": True,
                    "screenshot": str(shot),
                    "text": str((blank or {}).get("text") or "")[:MAX_PAGE_TEXT],
                    "chars": chars,
                    "blank": chars < 20,
                    # 报告里能看出这次等了多久才采证：以后再遇到「页面空白」，
                    # 先看这个数——如果只有几秒，多半又是重型页面没挂载完。
                    "settled_ms": settled_ms,
                })
                if info["blank"]:
                    # 空白原因要写进报告：用户看到「页面空白」第一反应是「我本地明明有
                    # 内容」——不给出工具到底看到了什么，这条结论就没法核对。
                    why = f"可见文本只有 {chars} 字符"
                    if page.url != url:
                        why += f"，页面重定向到了 {page.url}"
                    if login_note:
                        why += f"（{login_note}）"
                    if console_errors or page_errors:
                        why += f"；页面报了 {len(console_errors) + len(page_errors)} 条错误"
                    why += ("。常见原因：①「检查路由」不是真实页面路径（SPA 常在 "
                            "/admin/#/… 这类子路径，把路由填成完整路径再跑）；"
                            "② 需要登录而自测的浏览器没有登录态（在配置页「页面验证」"
                            "填账号密码，工具会在登录页代填；登录方式特殊时这条链路"
                            "会一直拿不到页面，属已知限制）。")
                    info["blank_reason"] = why
                info["console_errors"] = list(console_errors[:15])
                info["page_errors"] = list(page_errors[:15])
            except Exception as e:                           # noqa: BLE001
                info.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
            pages[route] = info

        pages["_page"] = page                               # 复用同一页做交互/取样式
        return {"ok": any(v.get("ok") for k, v in pages.items() if k != "_page"),
                "pages": pages, "error": "", "deadline": deadline,
                "console_all": console_errors, "profile": str(profile)}


def _looks_like_login(url: str) -> bool:
    return bool(re.search(r"/(login|signin|sign-in|auth|sso|passport)(\.[\w]+)?([/?#]|$)",
                          str(url or ""), re.I))


def _try_login(page, creds: Tuple[str, str]) -> str:
    """只在「页面明确是登录表单」时代填（沿用 verifier 的纪律：不猜登录页）。"""
    email, password = creds
    if not (email and password):
        return "停在登录页且没有配置页面验证账号"
    try:
        for sel in ("input[type=email]", "input[name*=user i]", "input[name*=account i]",
                    "input[type=text]"):
            el = page.query_selector(sel)
            if el:
                el.fill(email)
                break
        pwd = page.query_selector("input[type=password]")
        if not pwd:
            return "登录页找不到密码框"
        pwd.fill(password)
        pwd.press("Enter")
        page.wait_for_timeout(1200)
        return "已代填登录"
    except Exception as e:                                   # noqa: BLE001
        return f"代填登录失败：{type(e).__name__}"


def _run_steps(page, steps: str) -> Dict:
    """把人工用例的步骤尽力翻译成动作。翻译不了就跳过并如实记数。

    刻意只做三类：访问路径 / 点击可见文字 / 在可见输入框填字。选择器一律用
    **可见文本**，因为人工用例写的就是「点『提交』」；猜 CSS 选择器必然错。
    """
    ran: List[str] = []
    skipped: List[str] = []
    lines = [ln.strip(" 0123456789.、)（(）-·\t") for ln in str(steps or "").splitlines()]
    lines = [ln for ln in lines if ln][:MAX_ACTIONS]
    for ln in lines:
        if not _UI_STEP_RE.search(ln):
            skipped.append(ln[:80])
            continue
        try:
            m = _ROUTE_RE.search(ln)
            if re.search(r"(访问|打开|进入|跳转)", ln) and m:
                page.goto(page.url.split("#")[0].rstrip("/") + m.group(1),
                          wait_until="domcontentloaded", timeout=20000)
                ran.append(f"打开 {m.group(1)}")
                continue
            target = _quoted(ln)
            if not target:
                skipped.append(ln[:80])
                continue
            if re.search(r"(点击|点一下|点)", ln):
                loc = page.get_by_text(target, exact=False).first
                loc.click(timeout=5000)
                ran.append(f"点击「{target}」")
            elif re.search(r"(输入|填写|填入)", ln):
                box = (page.get_by_placeholder(target, exact=False).first
                       if page.query_selector(f"[placeholder*='{target}']") else None)
                if box is None:
                    box = page.locator("input:visible, textarea:visible").first
                box.fill(target, timeout=5000)
                ran.append(f"填写「{target}」")
            else:
                skipped.append(ln[:80])
        except Exception as e:                               # noqa: BLE001
            skipped.append(f"{ln[:60]}（{type(e).__name__}）")
    return {"ran": ran, "skipped": skipped}


def _quoted(text: str) -> str:
    m = re.search(r"[「『\"'“]([^」』\"'”]{1,30})[」』\"'”]", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:点击|点一下|点|输入|填写|填入)\s*([^\s，。,;；]{1,30})", text)
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------- 判定（模型）
def _vision_json(ai, page: Dict, prompt: str, system: str,
                 max_tokens: int = 2000) -> Optional[Dict]:
    """带截图的 JSON 判读。模型不可用/失败 → None（调用方记 unknown）。"""
    if _is_mock(ai):
        return None
    images = []
    path = str(page.get("screenshot") or "")
    if path and Path(path).is_file():
        try:
            data = Path(path).read_bytes()
        except OSError:
            data = b""
        if data and len(data) <= 4_000_000:
            images.append({"mime": "image/png", "tag": "页面截图",
                           "data_uri": "data:image/png;base64,"
                                       + base64.b64encode(data).decode()})
    try:
        with usage.step("selftest_judge"):
            res = ai.complete_text(prompt, system=system, max_tokens=max_tokens,
                                   images=images)
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    if res.get("error"):
        return {"error": res["error"]}
    data = _json_from_text(res.get("text") or "")
    return data if isinstance(data, dict) else None


def _page_context(page: Dict) -> str:
    errors = (page.get("console_errors") or []) + (page.get("page_errors") or [])
    return (
        f"路由：{page.get('route') or '-'}  页面标题：{page.get('title') or '-'}\n"
        f"页面可见文本（截断）：\n{(page.get('text') or '（空）')[:MAX_PAGE_TEXT]}\n"
        + ("控制台/页面报错：\n" + "\n".join(f"· {str(e)[:200]}" for e in errors[:10])
           if errors else "控制台与页面均未捕获到报错。")
    )


def _with_skills(system: str, skills_text: str) -> str:
    """团队技能/规范追加到判定提示词尾部（与其它模块同一口径）。"""
    extra = str(skills_text or "").strip()
    return f"{system}\n\n## 团队规范（判定时遵守）\n{extra[:6000]}" if extra else system


def _judge_cases(ai, page: Dict, cases: List[Dict],
                 extra_system: str = "") -> Tuple[Dict[str, Dict], str]:
    """一次调用判完一个页面上的所有用例（避免逐条烧图）。"""
    if not cases:
        return {}, ""
    if page.get("blank"):
        # 事实部分进每条结论；「常见原因」那段建议只在报告 notes 里说一次，
        # 12 条验收点各复读一遍「常见原因…」只会把判定表变噪音。
        why = (page.get("blank_reason") or "没有可判读的内容").split("。常见原因")[0]
        return ({c["id"]: {"status": STATUS_UNKNOWN, "reason": f"页面空白：{why}"}
                 for c in cases}, "页面空白，跳过模型判读")
    listing = "\n".join(
        f"- {c['id']} | {c['title']} | 步骤：{c['steps'][:400] or '（未写）'} | "
        f"预期：{c['expected'] or '（未写）'}" for c in cases)
    prompt = (
        "下面是在真实页面上采到的证据，以及若干条测试用例。逐条判断每条用例"
        "在当前页面上是否成立。\n"
        f"{_page_context(page)}\n\n用例：\n{listing}\n\n"
        "判定口径：\n"
        "· pass —— 证据里能明确看到符合预期（引用页面文本或截图里的具体内容）\n"
        "· fail —— 证据里能明确看到不符合预期\n"
        "· unknown —— 需要交互/需要页面里没有的数据/证据不足\n"
        "注意：这次采证可能没有执行用例里的操作步骤（见页面上下文），"
        "凡结论依赖点击或输入之后的状态，一律 unknown。\n"
        '只输出 JSON：{"results":[{"id":"C1","status":"pass|fail|unknown",'
        '"reason":"一句话，必须引用证据"}]}'
    )
    sys_prompt = _with_skills(_CASE_SYSTEM, extra_system)
    data = _vision_json(ai, page, prompt, sys_prompt, max_tokens=3200)
    if not data or data.get("error"):
        data = _vision_json(ai, page, prompt, sys_prompt, max_tokens=3200)
    if not data or data.get("error"):
        why = ((data or {}).get("error") if isinstance(data, dict) else None) \
            or "模型没有返回可解析的结论"
        return ({c["id"]: {"status": STATUS_UNKNOWN, "reason": f"未做模型判读：{why}"}
                 for c in cases}, why)
    out: Dict[str, Dict] = {}
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("id") or "")
        status = str(r.get("status") or "").lower()
        if cid and status in (STATUS_PASS, STATUS_FAIL, STATUS_UNKNOWN):
            out[cid] = {"status": status, "reason": str(r.get("reason") or "")[:400]}
    for c in cases:
        out.setdefault(c["id"], {"status": STATUS_UNKNOWN,
                                 "reason": "模型没有给出这条用例的结论"})
    return out, ""


def _judge_req(ai, page: Dict, points: List[Dict],
               extra_system: str = "") -> Tuple[Dict[str, Dict], str]:
    if not points:
        return {}, ""
    if page.get("blank"):
        why = (page.get("blank_reason") or "无法核对").split("。常见原因")[0]
        return ({p["id"]: {"status": STATUS_UNKNOWN, "reason": f"页面空白：{why}"}
                 for p in points}, "页面空白，跳过模型判读")
    # 分批判定：一次要它输出十几条带引用理由的 JSON，reasoning 一长 content
    # 就被截断，全批报废（用户实测整表「未做模型判读」就是这么来的）。
    out: Dict[str, Dict] = {}
    why = ""
    for i in range(0, len(points), _JUDGE_BATCH):
        chunk = points[i:i + _JUDGE_BATCH]
        judged, why = _judge_req_batch(ai, page, chunk, extra_system)
        out.update(judged)
        if why:                     # 这批彻底失败：后面的批照跑，失败批由调用方兜底
            continue
    return out, why


def _judge_req_batch(ai, page: Dict, points: List[Dict],
                     extra_system: str = "") -> Tuple[Dict[str, Dict], str]:
    listing = "\n".join(
        f"- {p['id']} | {p['title']} | 应看到：{p.get('expected') or '（未写明）'}"
        for p in points)
    prompt = (
        "下面是需求验收点与真实页面的证据。逐条核对是否成立。\n"
        f"{_page_context(page)}\n\n验收点：\n{listing}\n\n"
        "判定口径：\n"
        "- 验收点是**行为/界面检查的描述**，不是要整句搜索的字符串。先理解它要求\n"
        "  什么，再到页面文本里找**对应的界面落点**：功能名对应节点面板/菜单项/\n"
        "  按钮/文案，不要求逐字出现；例：验收点「左侧面板存在『消息通知』节点」\n"
        "  → 页面文本里有「消息通知」即算依据。\n"
        "- pass=页面上明确看到依据；fail=明确看到未实现或实现相反；\n"
        "- unknown=需要交互/其它页面/后端行为才能验证，或证据不足。\n"
        "- unknown 的 reason 要写**为什么静态页面验证不了**（如「需要把节点拖入"
        "画布并配置参数，本次自测未执行交互」），**不许**写成「页面文本未出现"
        "『某某功能名』」这种字面搜索式结论。\n"
        "**不允许**因为「没看到报错」或「看起来正常」就判 pass。\n"
        '只输出 JSON：{"results":[{"id":"R1","status":"pass|fail|unknown",'
        '"reason":"一句话，引用页面文本或截图里看到的内容"}]}'
    )
    sys_prompt = _with_skills(_REQ_SYSTEM, extra_system)
    data = _vision_json(ai, page, prompt, sys_prompt, max_tokens=3200)
    if not data or data.get("error"):
        # 重试一次：网关抖动 / 偶发截断（_json_repair_truncated 已能捞回大多数
        # 截断，这里兜住剩下的小概率失败），重试再失败才记「未做模型判读」。
        data = _vision_json(ai, page, prompt, sys_prompt, max_tokens=3200)
    if not data or data.get("error"):
        why = (data or {}).get("error") if isinstance(data, dict) else None
        why = why or "模型没有返回可解析的结论"
        return ({p["id"]: {"status": STATUS_UNKNOWN, "reason": f"未做模型判读：{why}"}
                 for p in points}, why)
    out: Dict[str, Dict] = {}
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("id") or "")
        status = str(r.get("status") or "").lower()
        if pid and status in (STATUS_PASS, STATUS_FAIL, STATUS_UNKNOWN):
            out[pid] = {"status": status, "reason": str(r.get("reason") or "")[:400]}
    for p in points:
        out.setdefault(p["id"], {"status": STATUS_UNKNOWN,
                                 "reason": "模型没有给出这条验收点的结论"})
    return out, ""


# ------------------------------------------------------------- 判定（数字）
def _hex_rgb(value: str) -> Optional[Tuple[int, int, int]]:
    s = str(value or "").lstrip("#")
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def _css_rgb(value: str) -> Optional[Tuple[int, int, int]]:
    m = _RGB_RE.search(str(value or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _compare_figma(page, page_info: Dict, nodes: List[Dict]) -> Dict:
    """设计稿文本节点 vs 页面实际计算样式。位置不比（见文件头）。"""
    if not nodes:
        return {"checked": 0, "verdicts": [], "note": "没有可对齐的设计稿文本节点"}
    try:
        measured = page.evaluate(_PROBE_JS, [n["text"] for n in nodes])
    except Exception as e:                                   # noqa: BLE001
        return {"checked": 0, "verdicts": [],
                "note": f"页面样式读取失败：{type(e).__name__}"}
    measured = measured or {}
    verdicts: List[Dict] = []
    for n in nodes:
        got = measured.get(n["text"])
        base = {"lane": LANE_FIGMA, "id": n["text"], "title": f"「{n['text']}」",
                "expected": (f"{n['fontSize'] or '?'}px/{n['fontWeight'] or '?'} "
                             f"{n['color'] or ''} 宽 {n['width'] or '?'}px").strip(),
                "route": page_info.get("route", ""),
                "evidence": [page_info.get("screenshot") or ""]}
        if not got:
            verdicts.append({**base, "status": STATUS_UNKNOWN,
                             "reason": "页面上找不到与设计稿文本完全一致的元素"
                                       "（可能是图标/图片文字，或文本被拆成多段）"})
            continue
        diffs: List[str] = []
        if n["fontSize"] and abs(float(got.get("fontSize") or 0) - n["fontSize"]) > 0.5:
            diffs.append(f"字号 {got.get('fontSize')} ≠ {n['fontSize']}")
        if n["fontWeight"] and str(got.get("fontWeight") or "") != str(n["fontWeight"]):
            diffs.append(f"字重 {got.get('fontWeight')} ≠ {n['fontWeight']}")
        want = _hex_rgb(n["color"])
        have = _css_rgb(got.get("color") or "")
        if want and have and max(abs(a - b) for a, b in zip(want, have)) > 10:
            diffs.append(f"颜色 #{want[0]:02x}{want[1]:02x}{want[2]:02x} ≠ {got.get('color')}")
        if n["width"] and abs(int(got.get("w") or 0) - n["width"]) > 8:
            diffs.append(f"宽度 {got.get('w')}px ≠ {n['width']}px")
        verdicts.append({**base,
                         "status": STATUS_FAIL if diffs else STATUS_PASS,
                         "reason": "；".join(diffs) if diffs else "字号/字重/颜色/宽度一致",
                         "actual": f"{got.get('fontSize')}px/{got.get('fontWeight')} "
                                   f"{got.get('color')} 宽 {got.get('w')}px"})
    checked = len([v for v in verdicts if v["status"] != STATUS_UNKNOWN])
    return {"checked": checked, "verdicts": verdicts,
            "note": f"数字比对 {checked}/{len(nodes)} 个文本节点；"
                    "位置(x/y)未比对（页面滚动与容器偏移无法与画布绝对坐标对齐）"}


# ------------------------------------------------------------------ 入口
def run(ai, ctx: Dict, settings: Dict, progress=None, skills_text: str = "") -> Dict:
    """执行一次自测。返回可直接落成产出物的 payload。"""
    lanes = normalize_lanes(ctx.get("lanes"))
    url = str(ctx.get("url") or (settings.get("playwright") or {}).get("base_url") or "").strip()
    routes = parse_routes(ctx.get("routes") or "/")
    limit = max(1, min(int(ctx.get("limit") or 12), MAX_ITEMS))
    evidence_dir = Path((settings.get("playwright") or {}).get("screenshot_dir")
                        or (Path.cwd() / "screenshots"))
    evidence_dir.mkdir(parents=True, exist_ok=True)

    result: Dict = {
        "task": "selftest", "label": "自测", "ai_mode": getattr(ai, "mode", ""),
        "summary": "", "plan": "", "checklist": [], "error": "",
        "lanes": [], "verdicts": [], "cases": [], "environment": {},
        # 这次判的是哪几份需求文档（名称 / 抽取字符数）：artifact.py 把
        # payload["attachments"] 当一等字段落盘，回看时才说得清判据来自哪一版 PRD。
        "attachments": [d for d in (ctx.get("attachments") or []) if isinstance(d, dict)],
        "notes": [], "lanes_enabled": lanes,
    }

    cases_info = {"items": [], "note": "", "source": ""}
    if LANE_CASE in lanes:
        if progress:
            progress("采证", "拉取测试用例…")
        cases_info = _load_cases(ai, ctx, settings)
        cases_info["items"] = cases_info["items"][:limit]
        result["notes"].append(f"测试用例：{cases_info['note']}"
                               f"（{len(cases_info['items'])} 条）")
        if progress and cases_info["items"]:
            progress("采证", f"已整理 {len(cases_info['items'])} 条测试用例")

    points: List[Dict] = []
    if LANE_REQ in lanes:
        if progress:
            progress("拆解", "拆解需求验收点…")
        points = _split_req_points(ai, ctx.get("req_doc") or "")[:limit]
        n_manual = sum(1 for p in points if p.get("verify") == "manual")
        if progress:
            progress("拆解", f"已拆出 {len(points)} 条验收点"
                     + (f"（{n_manual} 条需人工操作）" if n_manual else ""))
        docs = result["attachments"]
        if docs:
            # 报告里要能回看「判的是哪几份文档」：上传的 PDF 改天换一版，
            # 同一句结论的含义就完全不同了。chars 只有 PDF 有（文本是直读的，
            # 不落抽取结果），所以两种说法分开写，别让人以为 txt 抽出了 0 字符。
            def _doc_label(d: Dict) -> str:
                name = str(d.get("name") or "未命名")
                return (f"{name}（抽出 {d.get('chars')} 字符）" if d.get("chars")
                        else f"{name}（文本直读）")
            result["notes"].append("需求文档：" + "、".join(_doc_label(d) for d in docs))
        result["notes"].append(
            f"需求验收点：{len(points)} 条" if points else "需求验收点：没有需求文档，跳过")

    figma = {"ok": False, "nodes": [], "note": "未启用"}
    if LANE_FIGMA in lanes:
        if progress:
            progress("采证", "拉取 Figma 设计稿…")
        figma = _figma_specs(settings, ctx.get("figma_url") or "")
        result["notes"].append(f"Figma：{figma.get('note')}")
        if progress and figma.get("nodes"):
            progress("采证", f"设计稿取到 {len(figma['nodes'])} 个文本锚点")

    if not (cases_info["items"] or points or figma.get("nodes")):
        raise SelfTestError(
            "三条链路都没有可判定的输入：请填 ONES 工单（或手贴用例）、需求文档、Figma 链接之一")

    probe = _probe(settings, url, routes, evidence_dir, progress=progress)
    if progress:
        n_pages = sum(1 for r, pg in (probe.get("pages") or {}).items()
                      if r != "_page" and (pg or {}).get("ok"))
        progress("判定", f"采证完成（{n_pages} 个页面），开始逐条判定")
    result["environment"] = {
        "url": url, "routes": routes, "ok": bool(probe.get("ok")),
        "error": probe.get("error", ""),
        "pages": [{k: v for k, v in (p or {}).items()
                   if k in ("route", "url", "final_url", "title", "ok", "error",
                            "blank", "chars", "login", "screenshot")}
                  for r, p in (probe.get("pages") or {}).items() if r != "_page"],
    }
    if not probe.get("ok"):
        reason = probe.get("error") or "本地环境不可达，没有采到任何页面证据"
        result["error"] = reason
        for lane, key in ((LANE_CASE, "cases"), (LANE_REQ, "req_points"),
                          (LANE_FIGMA, "figma_nodes")):
            if lane not in lanes:
                continue
            items = (cases_info["items"] if key == "cases"
                     else points if key == "req_points" else figma.get("nodes") or [])
            for it in items:
                title = (it.get("title") or it.get("text") or it.get("id") or "")[:120]
                result["verdicts"].append({
                    "lane": lane, "id": str(it.get("id") or title), "title": title,
                    "expected": str(it.get("expected") or "")[:300],
                    "status": STATUS_UNKNOWN, "reason": reason, "route": "", "evidence": [],
                })
        result["summary"] = f"环境不可达：{reason}"
        result["plan"] = _plan_text(result)
        result["checklist"] = [f"先让本地环境起来并确认地址可用：{url}"]
        return _finalize(result)

    pages = probe.get("pages") or {}
    page = pages.get("_page")
    usable = [p for r, p in pages.items() if r != "_page" and p.get("ok")]
    first = usable[0] if usable else {}

    # 空白页诊断：用户看到「页面空白」第一反应是「我本地明明是好的」。必须把
    # 工具到底看到了什么（重定向到哪、标题、可见文本量、有没有报错、截图在哪）
    # 摆进报告，否则这条结论既没法核对、也没法自己修。
    for r, p in pages.items():
        if r == "_page" or not p.get("ok") or not p.get("blank"):
            continue
        shot_name = Path(str(p.get("screenshot") or "")).name
        result["notes"].append(
            f"空白诊断 · 路由 {r}：停在 {p.get('final_url') or p.get('url')}"
            f"，标题「{p.get('title') or '-'}」，{p.get('blank_reason') or ''}"
            + (f"证据截图：/screenshots/{shot_name}" if shot_name else ""))

    # ---- 链路一：测试用例
    if LANE_CASE in lanes and cases_info["items"]:
        if progress:
            progress("判定", "测试用例…")
        by_route: Dict[str, List[Dict]] = {}
        for c in cases_info["items"]:
            route = c.get("route") or (first.get("route") if first else "/")
            by_route.setdefault(route, []).append(c)
        for route, group in by_route.items():
            info = pages.get(route) or first
            steps_note = ""
            if page is not None and info.get("ok") and any(
                    _UI_STEP_RE.search(c.get("steps") or "") for c in group):
                page.goto(info.get("url") or url, wait_until="domcontentloaded",
                          timeout=30000)
                for c in group:
                    if _UI_STEP_RE.search(c.get("steps") or ""):
                        r = _run_steps(page, c.get("steps") or "")
                        c["steps_ran"] = r["ran"]
                        c["steps_skipped"] = r["skipped"]
                        if r["ran"]:
                            steps_note = "已执行部分操作：" + "；".join(r["ran"])[:300]
                            try:
                                page.wait_for_timeout(800)
                                shot = evidence_dir / f"selftest_case_{c['id']}.png"
                                page.screenshot(path=str(shot), full_page=False)
                                info = {**info, "screenshot": str(shot)}
                            except Exception:                # noqa: BLE001
                                pass
            judged, why = _judge_cases(ai, info, group, extra_system=skills_text)
            for c in group:
                j = judged.get(c["id"]) or {"status": STATUS_UNKNOWN, "reason": why}
                reason = j.get("reason") or ""
                if c.get("steps_skipped") and j["status"] != STATUS_PASS:
                    reason = (reason + "；未执行步骤：" +
                              "、".join(c["steps_skipped"][:3])).strip("；")
                result["verdicts"].append({
                    "lane": LANE_CASE, "id": c["id"], "title": c["title"],
                    "expected": c.get("expected", ""), "status": j["status"],
                    "reason": reason, "route": route,
                    "evidence": [info.get("screenshot") or ""],
                    "steps_ran": c.get("steps_ran") or [],
                })
                result["cases"].append({
                    "name": c["title"][:120],
                    "purpose": (c.get("expected") or "")[:200],
                })
        result["notes"].append(f"用例执行：{steps_note or '未执行交互步骤（只做静态核对）'}")

    # ---- 链路二：需求验收点
    if LANE_REQ in lanes and points:
        # verify=manual 的点（需要拖拽/配置/保存等交互或后端行为）静态页面
        # 验证不了：直接标 unknown 并写明要做什么，**不送模型**——送了它只会
        # 拿功能名去页面文本里字面搜索，产出「未出现『XXX功能』」这种荒唐结论。
        manual = [p for p in points if p.get("verify") == "manual"]
        page_points = [p for p in points if p.get("verify") != "manual"]
        if progress:
            progress("判定", "需求一致性…")
        if page_points:
            judged, why = _judge_req(ai, first, page_points, extra_system=skills_text)
        else:
            judged, why = {}, ""
        for p in points:
            if p.get("verify") == "manual":
                what = p.get("expected") or p["title"]
                j = {"status": STATUS_UNKNOWN,
                     "reason": f"需人工操作验证（本次自测只做静态核对，未执行交互）：{what}"}
            else:
                j = judged.get(p["id"]) or {"status": STATUS_UNKNOWN, "reason": why}
            result["verdicts"].append({
                "lane": LANE_REQ, "id": p["id"], "title": p["title"],
                "expected": p.get("expected", ""), "status": j["status"],
                "reason": j.get("reason", ""), "route": first.get("route", ""),
                "evidence": [first.get("screenshot") or ""],
            })
        if manual:
            result["notes"].append(
                f"需人工操作验证的验收点：{len(manual)} 条（未执行交互，见判定表 reason）")

    # ---- 链路三：Figma 数字比对
    if LANE_FIGMA in lanes and figma.get("nodes") and page is not None:
        if progress:
            progress("判定", "设计稿一致性…")
        best = {"verdicts": []}
        for info in usable:
            try:
                page.goto(info.get("url") or url, wait_until="domcontentloaded",
                          timeout=30000)
                cmp = _compare_figma(page, info, figma["nodes"])
            except Exception as e:                           # noqa: BLE001
                cmp = {"checked": 0, "verdicts": [],
                       "note": f"页面比对失败：{type(e).__name__}"}
            if len(cmp.get("verdicts") or []) > len(best.get("verdicts") or []):
                best = cmp
        result["verdicts"].extend(best.get("verdicts") or [])
        result["notes"].append(f"Figma：{best.get('note') or figma.get('note')}")
    elif LANE_FIGMA in lanes:
        result["notes"].append("Figma：没有可对齐的节点，跳过数字比对")

    return _finalize(result)


def _finalize(result: Dict) -> Dict:
    """统计三态、生成小结与 checklist。统计口径对三条链路一视同仁。"""
    verdicts = result.get("verdicts") or []
    counts = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_UNKNOWN: 0}
    per_lane: Dict[str, Dict] = {}
    for v in verdicts:
        status = v.get("status") if v.get("status") in counts else STATUS_UNKNOWN
        counts[status] += 1
        row = per_lane.setdefault(v["lane"], {"lane": v["lane"],
                                              "label": LANE_LABELS.get(v["lane"], v["lane"]),
                                              STATUS_PASS: 0, STATUS_FAIL: 0,
                                              STATUS_UNKNOWN: 0, "total": 0})
        row[status] += 1
        row["total"] += 1
    result["counts"] = counts
    result["lanes"] = list(per_lane.values())
    total = len(verdicts)
    if total:
        result["summary"] = (f"共检查 {total} 项：通过 {counts[STATUS_PASS]}，"
                             f"不通过 {counts[STATUS_FAIL]}，"
                             f"无法判断 {counts[STATUS_UNKNOWN]}")
    else:
        result["summary"] = result.get("summary") or "没有可判定的检查项"
    fails = [v for v in verdicts if v.get("status") == STATUS_FAIL]
    unknowns = [v for v in verdicts if v.get("status") == STATUS_UNKNOWN]
    checklist = [f"【不通过】{v['title']}：{v.get('reason') or ''}"[:200] for v in fails]
    checklist += [f"【待人工确认】{v['title']}：{v.get('reason') or ''}"[:200]
                  for v in unknowns[:8]]
    result["checklist"] = checklist[:20]
    result["plan"] = _plan_text(result)
    return result


def _plan_text(result: Dict) -> str:
    """给产出物弹窗/界面的纯文本报告（不重复 verdicts 明细，只给结论与口径）。"""
    lines = ["## 自测结论", result.get("summary") or "-", ""]
    for row in result.get("lanes") or []:
        lines.append(f"· {row['label']}：{row['total']} 项 "
                     f"（通过 {row['pass']} / 不通过 {row['fail']} / "
                     f"无法判断 {row['unknown']}）")
    env = result.get("environment") or {}
    lines += ["", "## 环境",
              f"· 地址：{env.get('url') or '-'}",
              f"· 路由：{', '.join(env.get('routes') or []) or '-'}",
              f"· 页面：{len(env.get('pages') or [])} 个"]
    for note in result.get("notes") or []:
        lines.append(f"· {note}")
    lines += ["", "## 判定纪律",
              "· 环境不可达 / 页面空白 / 证据不足一律记「无法判断」，不计为通过。",
              "· 设计稿比对只比字号/字重/颜色/宽度；位置(x/y)不参与比对。"]
    return "\n".join(lines)
