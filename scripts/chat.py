# -*- coding: utf-8 -*-
"""问答 / 随手改代码：通用入口。

为什么单独做一块：缺陷修复要工单、需求开发要设计稿、接口联调要接口文档、代码测试要目标
文件——四个页签都是**有固定输入形态的任务**。但日常更多时候是「问一句」（这个配置在哪
改？这个报错什么原因？）或者「顺手改一下」（把字段加上、把命名统一）。这些套不进任何一
条流水线，硬塞只会更慢。

本模块提供三件事：

1. `ChatStore`  —— 会话持久化：一问一答追加落盘，可回看 / 删除
2. `RepoReader` —— 目标仓库**只读**检索（list_dir / read_file / grep），让模型自己去
   仓库里找证据，而不是凭印象编；所有路径都必须落在仓库根内
3. `run_chat`   —— 多轮 agent 循环：先查仓库再回答；用户明确要改代码时，在回答末尾附
   一段 `<PROPOSAL>` 改动提案

安全契约与其它模块完全一致：**本模块只读仓库**。任何改动都先变成「产出物」，只有人工在
产出物里点「采纳」才会写工作区（不 commit、不 push）。这里不导入 `ArtifactApplier`，也
不做任何写文件动作。
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .artifact import safe_rel_path
from .task_modules import _files, _str_list  # 复用产出的文件归一化（与其它任务同口径）

MAX_TITLE_CHARS = 40
MAX_SESSIONS = 300
# 历史消息送进模型的上限：按条数 + 字符双限，避免长会话把预算烧在复读上
MAX_HISTORY_MSGS = 24
MAX_HISTORY_CHARS = 24000
MAX_MSG_CHARS = 6000
# 推理模型（deepseek-flash 等）的 hidden reasoning 与正文共享 max_tokens，留足余量
BUDGET = 16000
MAX_TOOL_TEXT = 8000
MAX_ROUNDS = 5
DEFAULT_MAX_HITS = 60

PROPOSAL_OPEN = "<PROPOSAL>"
PROPOSAL_CLOSE = "</PROPOSAL>"

# 仓库检索时跳过的目录（git 不可用时的兜底遍历用）
IGNORE_DIRS = {
    ".git", "node_modules", "dist", "build", "out", ".next", ".nuxt", ".output",
    "coverage", ".turbo", ".cache", ".parcel-cache", "__pycache__", ".venv",
    "venv", ".idea", ".vscode", "public", "static",
}
MAX_FILE_BYTES = 1_500_000


class ChatError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", str(s or "")).strip("-")[:40] or "chat"


def _clip(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + f"\n…（已截断，原文 {len(s)} 字符）"


# ------------------------------------------------------------------ 会话存储
class ChatStore:
    """一问一答的会话账本：一个会话一个 JSON 文件，追加式更新。

    和提案/产出物一样是「人可读、可手工删」的普通文件，没有数据库。
    """

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, sid: str) -> Path:
        return self.base / f"{_slug(sid)}.json"

    def create(self, title: str = "", kind: str = "") -> Dict:
        now = _now()
        sid = f"chat-{datetime.now():%Y%m%d-%H%M%S}"
        sess = {
            "id": sid,
            "title": (title or "新会话")[:MAX_TITLE_CHARS],
            "kind": kind or "chat",
            "created": now,
            "updated": now,
            "messages": [],
        }
        self.save(sess)
        return sess

    def save(self, sess: Dict) -> Dict:
        sess["updated"] = _now()
        target = self._path(sess["id"])
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(sess, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(target)
        return sess

    def get(self, sid: str) -> Optional[Dict]:
        path = self._path(sid)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list(self) -> List[Dict]:
        """会话摘要，按更新时间倒序（最新的在最前）。"""
        out: List[Dict] = []
        for f in self.base.glob("*.json"):
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            msgs = s.get("messages") or []
            last = next((m for m in reversed(msgs) if m.get("role") == "assistant"), None) \
                or (msgs[-1] if msgs else None)
            out.append({
                "id": s.get("id") or f.stem,
                "title": s.get("title") or "新会话",
                "created": s.get("created", ""),
                "updated": s.get("updated", ""),
                "count": len(msgs),
                "preview": _clip((last or {}).get("content", ""), 120).replace("\n", " "),
            })
        out.sort(key=lambda s: str(s.get("updated") or s.get("created") or ""), reverse=True)
        return out[:MAX_SESSIONS]

    def append(self, sid: str, role: str, content: str, meta: Optional[Dict] = None) -> Dict:
        sess = self.get(sid)
        if sess is None:
            sess = self.create()
            sess["id"] = sid  # 允许前端指定 id（例如刷新后恢复）
        msg = {"role": role, "content": str(content or ""), "ts": _now()}
        if meta:
            msg["meta"] = meta
        sess.setdefault("messages", []).append(msg)
        # 首次有用户消息时用它的前若干字当标题，省得用户自己命名
        if role == "user" and (sess.get("title") in ("", "新会话")):
            first = re.sub(r"\s+", " ", str(content or "")).strip()
            if first:
                sess["title"] = first[:MAX_TITLE_CHARS]
        return self.save(sess)

    def delete(self, sid: str) -> bool:
        path = self._path(sid)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def clear(self, sid: str) -> Optional[Dict]:
        sess = self.get(sid)
        if sess is None:
            return None
        sess["messages"] = []
        sess["title"] = "新会话"
        return self.save(sess)


# ------------------------------------------------------------ 仓库只读检索
TOOL_SPECS: List[Dict] = [
    {
        "name": "repo_list",
        "description": "列出仓库中某个目录下的文件与子目录（路径相对仓库根）。用来确认某个模块到底有哪些文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对仓库根的目录路径；仓库根传空字符串"},
                "limit": {"type": "integer", "description": "最多返回多少条，默认 120"},
            },
            "required": [],
        },
    },
    {
        "name": "repo_read",
        "description": "读取仓库中某个文本文件的内容（带行号，便于引用具体行）。改了哪一行要能指出来。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对仓库根的文件路径"},
                "start": {"type": "integer", "description": "起始行（1 起）；省略或 0 = 从开头"},
                "end": {"type": "integer", "description": "结束行；省略或 0 = 到文件尾"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "repo_grep",
        "description": "在仓库中按正则搜索文本内容，返回「路径:行号: 内容」。用来找定义、引用、配置项。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "glob": {"type": "string", "description": "可选：限定文件，如 .vue / .ts / src/**"},
                "max_hits": {"type": "integer", "description": "最多返回多少条命中，默认 60"},
            },
            "required": ["pattern"],
        },
    },
]


class RepoReader:
    """目标仓库的只读视图。所有路径都要落在仓库根内，越界直接拒绝。

    `tools_enabled=False`（用户在界面上关掉了仓库检索，或仓库路径不存在）时
    `spec()` 返回空 —— 模型会被告知「没有工具可用」，只能依据用户提供的信息回答。
    """

    def __init__(self, root: str, tools_enabled: bool = True):
        self.root = Path(root).resolve()
        self.tools_enabled = bool(tools_enabled)
        self._file_cache: Optional[List[str]] = None

    # -- 路径安全（与产出物采纳共用同一套判定，避免两处规则打架）
    def resolve(self, rel: str) -> Optional[Path]:
        return safe_rel_path(self.root, rel)

    def files(self) -> List[str]:
        """仓库内的文本候选文件（相对路径）。git 可用时用它（自带 .gitignore 语义），
        否则退化成带黑名单的目录遍历。结果缓存一次，避免每个工具调用都扫全仓。"""
        if self._file_cache is None:
            self._file_cache = self._git_files() or self._walk_files()
        return self._file_cache

    def _git_files(self) -> List[str]:
        try:
            r = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "--exclude-standard", "-c", "-o"],
                capture_output=True, timeout=90,
            )
        except Exception:
            return []
        if r.returncode != 0:
            return []
        text = r.stdout.decode("utf-8", "replace")
        return [l.strip().replace("\\", "/") for l in text.splitlines() if l.strip()][:20000]

    def _walk_files(self) -> List[str]:
        out: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames
                           if d not in IGNORE_DIRS and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    out.append(str(p.relative_to(self.root)).replace("\\", "/"))
                except ValueError:
                    continue
                if len(out) >= 20000:
                    return out
        return out

    # -- 工具实现
    def list_dir(self, path: str = "", limit: int = 120) -> str:
        target = self.resolve(path) if str(path or "").strip() else self.root
        if target is None:
            return f"[错误] 路径越界或非法：{path!r}（只能访问仓库内的路径）"
        if not target.exists() or not target.is_dir():
            return f"[错误] 目录不存在：{path or '.'}"
        try:
            limit = max(1, min(int(limit or 120), 400))
        except Exception:
            limit = 120
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        rows = []
        for p in entries[:limit]:
            rel = str(p.relative_to(self.root)).replace("\\", "/")
            if p.is_dir():
                rows.append(f"{rel}/")
            else:
                try:
                    kb = p.stat().st_size / 1024
                    rows.append(f"{rel}  ({kb:.1f} KB)")
                except OSError:
                    rows.append(rel)
        if not rows:
            return f"[空] 目录 {path or '.'} 下没有文件"
        more = "" if len(entries) <= limit else f"\n…（还有 {len(entries) - limit} 项未显示）"
        return f"目录 {path or '.'}（{len(entries)} 项）：\n" + "\n".join(rows) + more

    def read_file(self, path: str, start: int = 0, end: int = 0) -> str:
        target = self.resolve(path)
        if target is None:
            return f"[错误] 路径越界或非法：{path!r}（只能访问仓库内的路径）"
        if not target.is_file():
            return f"[错误] 文件不存在：{path}"
        if target.stat().st_size > MAX_FILE_BYTES:
            return f"[错误] 文件过大（{target.stat().st_size / 1024:.0f} KB），请用 repo_grep 定位后分段读取"
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = target.read_text(encoding="gbk", errors="replace")
        lines = text.split("\n")
        try:
            a = max(0, int(start or 0) - 1) if int(start or 0) > 0 else 0
            b = int(end or 0) if int(end or 0) > 0 else len(lines)
        except Exception:
            a, b = 0, len(lines)
        chunk = lines[a:b]
        body = "\n".join(f"{a + i + 1}\t{ln}" for i, ln in enumerate(chunk))
        head = f"{path}（第 {a + 1}-{a + len(chunk)} 行 / 共 {len(lines)} 行）\n"
        return head + _clip(body, MAX_TOOL_TEXT)

    def grep(self, pattern: str, glob: str = "", max_hits: int = DEFAULT_MAX_HITS) -> str:
        if not str(pattern or "").strip():
            return "[错误] pattern 不能为空"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"[错误] 正则不合法：{e}"
        try:
            max_hits = max(1, min(int(max_hits or DEFAULT_MAX_HITS), 200))
        except Exception:
            max_hits = DEFAULT_MAX_HITS
        g = str(glob or "").strip().lower()
        hits: List[str] = []
        scanned = 0
        truncated_by_scan = False
        for rel in self.files():
            if g and not (rel.lower().endswith(g) or fnmatch.fnmatch(rel.lower(), g)):
                continue
            if scanned >= 6000:
                truncated_by_scan = True
                break
            p = self.resolve(rel)
            if p is None or not p.is_file():
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
                if b"\x00" in p.open("rb").read(512):
                    continue  # 二进制
            except OSError:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            scanned += 1
            for i, line in enumerate(text.split("\n"), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:220]}")
                    if len(hits) >= max_hits:
                        break
            if len(hits) >= max_hits:
                break
        if not hits:
            return f"[无命中] 在 {scanned} 个文件里没找到匹配 /{pattern}/ 的内容"
        tail = ""
        if len(hits) >= max_hits:
            tail = f"\n…（已达上限 {max_hits} 条）"
        elif truncated_by_scan:
            tail = "\n…（已扫描 6000 个文件，可能还有更多）"
        return f"命中 {len(hits)} 条：\n" + "\n".join(hits) + tail

    def spec(self) -> List[Dict]:
        return TOOL_SPECS if self.tools_enabled else []

    def call(self, name: str, args: Dict) -> str:
        """统一入口：任何异常都变成可读文本回给模型，绝不让它冒泡打断会话。"""
        args = args if isinstance(args, dict) else {}
        try:
            if name == "repo_list":
                return self.list_dir(str(args.get("path") or ""), args.get("limit") or 120)
            if name == "repo_read":
                return self.read_file(str(args.get("path") or ""),
                                      args.get("start") or 0, args.get("end") or 0)
            if name == "repo_grep":
                return self.grep(str(args.get("pattern") or ""),
                                 str(args.get("glob") or ""),
                                 args.get("max_hits") or DEFAULT_MAX_HITS)
        except Exception as e:
            return f"[工具执行异常] {name}: {e}"
        return f"[错误] 未知工具 {name!r}"


# --------------------------------------------------------------- 提示词
CHAT_SYSTEM = (
    "你是这个前端仓库的资深工程师，和用户**对话**。用户不一定在开发或修缺陷——"
    "可能只是问一句、排查一个现象、或者顺手让你改点代码。\n"
    "要求：\n"
    "1. 用中文，直接给结论，再给依据。不要客套话、不要复述用户的问题。\n"
    "2. 回答用 Markdown。**凡是引用代码，必须写明 `文件路径:行号`**，并且只能引用工具"
    "真实返回过的内容——拿不到就说拿不到，绝不凭印象编造文件路径、函数名或行号。\n"
    "3. 不确定的地方就说「不确定」，并说明还需要看什么。\n"
    "4. 回答长度按问题来：一句话能说清就别写三段。\n"
    "5. 你**没有写文件的权限**——只能读。需要改动时以提案形式给出（见下），"
    "由人工确认后才会落到工作区；不要说「我已经改好了」。"
)

PROPOSAL_RULES = (
    "## 什么时候输出改动提案\n"
    "先判断用户的意图：\n"
    "- 只是问「为什么 / 在哪 / 是不是 / 怎么调」→ **只回答**，绝对不要输出提案。\n"
    "- 明确要你改代码（「帮我改」「把 X 换成 Y」「加个字段」「顺手重构一下」）→ 先用工具"
    "读够相关文件和调用方，回答里说清改法与影响，然后在回答**最末尾**附一段提案：\n"
    "```\n" + PROPOSAL_OPEN + "\n"
    '{"summary": "一句话说明这次改什么", '
    '"files": [{"path": "相对仓库根的文件路径", "action": "overwrite 或 create", '
    '"description": "这个文件改什么", "content": "改完之后的完整文件内容"}], '
    '"checklist": ["需要人工确认的点", ...]}\n'
    + PROPOSAL_CLOSE + "\n```\n"
    "硬要求：\n"
    "- `content` 必须是该文件**改完后的完整内容**（不是 diff、不是片段、不要省略号），"
    "否则人工采纳时会把文件写坏。\n"
    "- 只包含你确实要改的文件；不要为了「顺便优化」加无关改动。\n"
    "- 提案块之外不要出现 " + PROPOSAL_OPEN + " 字样，也不要解释这个格式。\n"
    "- 提案只是**建议**：它不会自动写入仓库，用户会在「产出物」里 review 后决定采纳与否。"
)

TOOL_RULES = (
    "## 工具调用规则\n"
    "你可以调用下面的工具去**仓库里查真实内容**（最多 <<max_rounds>> 轮）。\n"
    "需要查的时候，你这一轮的回复**只输出这一个 JSON**（不要有任何其他文字）：\n"
    '{"action": "call", "tool": "工具名", "arguments": {参数}}\n'
    "工具结果会以文本回给你。查到够了就停止调用工具，直接给出最终回答（Markdown 正文）。\n"
    "不要为了显得完整而反复查同一个文件；也不要假装工具返回了你没看到的内容。"
)


def _tool_block(specs: List[Dict], max_rounds: int) -> str:
    if not specs:
        return ("## 可用工具\n（本轮没有开启任何工具：你**看不到仓库内容**，"
                "只能依据用户提供的信息回答。凡是需要看代码才能确定的事，"
                "直接说明「需要仓库信息/请把相关代码贴过来」，不要猜。")
    listing = json.dumps(
        [{"name": t["name"], "description": (t.get("description") or "")[:200],
          "parameters": t.get("inputSchema") or {"type": "object"}} for t in specs],
        ensure_ascii=False, indent=1)
    return ("## 可用工具\n" + listing + "\n\n"
            + TOOL_RULES.replace("<<max_rounds>>", str(max_rounds)))


def build_prompt(messages: List[Dict], user_text: str, budget_chars: int = MAX_HISTORY_CHARS) -> str:
    """把会话历史拼成一段文本。从最近往前取，总量封顶，不把整段历史无脑塞进去。"""
    picked: List[Dict] = []
    used = 0
    for m in reversed(messages[-MAX_HISTORY_MSGS:]):
        body = _clip(m.get("content", ""), MAX_MSG_CHARS)
        used += len(body)
        if used > budget_chars and picked:
            break
        picked.append({"role": m.get("role"), "content": body})
    picked.reverse()
    lines = ["## 对话历史（越靠后越新）"]
    if not picked:
        lines.append("（无，这是第一句）")
    for m in picked:
        who = "用户" if m.get("role") == "user" else "你"
        lines.append(f"### {who}\n{m['content']}")
    lines.append("\n## 现在\n请回应上面最后一条【用户】消息。")
    if user_text and (not picked or picked[-1]["content"] != user_text):
        lines.append(f"（本轮用户输入：{_clip(user_text, MAX_MSG_CHARS)}）")
    return "\n".join(lines)


# ------------------------------------------------------------- 解析工具
def _as_tool_call(text: str) -> Optional[Dict]:
    """识别「整段回复就是一个 action=call 的 JSON」这种工具调用形态。

    模型偶尔会套一层 ``` 围栏，一并剥掉。只认这种情况，避免把正文里的 JSON 例子
    （比如回答里贴的一段配置）误当成工具调用。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    if not t.startswith("{") or len(t) > 3000:
        return None
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if str(obj.get("action") or "").strip().lower() != "call":
        return None
    args = obj.get("arguments")
    return {"tool": str(obj.get("tool") or "").strip(),
            "arguments": args if isinstance(args, dict) else {}}


def split_proposal(text: str) -> Tuple[str, Optional[Dict], str]:
    """把回答末尾的 <PROPOSAL>…</PROPOSAL> 摘出来。

    返回 (给用户看的回答, 提案 dict 或 None, 错误说明)。
    提案块存在但解析失败时**保留原文**并把错误说清楚——静默吞掉会让用户以为 AI 没提改动。
    """
    raw = str(text or "")
    i = raw.find(PROPOSAL_OPEN)
    if i < 0:
        return raw.strip(), None, ""
    head = raw[:i]
    j = raw.find(PROPOSAL_CLOSE, i)
    inner = raw[i + len(PROPOSAL_OPEN): j if j >= 0 else len(raw)]
    inner = re.sub(r"```[a-zA-Z]*\s*", "", inner).replace("```", "").strip()
    try:
        obj = json.loads(inner)
        if not isinstance(obj, dict):
            raise ValueError("提案不是 JSON 对象")
    except Exception as e:
        return (raw.strip(), None,
                f"AI 给出了改动提案但格式不合法（{e}），已按原文保留，未生成产出物")
    tail = raw[j + len(PROPOSAL_CLOSE):] if j >= 0 else ""
    return (head + tail).strip(), obj, ""


def proposal_payload(patch: Dict, ai_mode: str = "") -> Dict:
    """把 <PROPOSAL> 里的 JSON 归一成产出物 payload（与其它任务的字段口径一致）。"""
    files = _files(patch.get("files"))
    err = ""
    if not files:
        err = "AI 给出的提案里没有可用的文件内容（files 为空或 content 缺失）"
    return {
        "ai_mode": ai_mode,
        "task": "chat",
        "label": "问答",
        "error": err,
        "summary": str(patch.get("summary") or "")[:2000],
        "plan": str(patch.get("plan") or ""),
        "checklist": _str_list(patch.get("checklist")),
        "endpoints": [],
        "mock_data": "",
        "cases": [],
        "raw": "",
        "files": files,
    }


# --------------------------------------------------------------- 主循环
def run_chat(ai, reader: RepoReader, messages: List[Dict], user_text: str, *,
             skills_text: str = "", allow_patch: bool = True,
             extra_specs: Optional[List[Dict]] = None,
             extra_executor: Optional[Callable[[str, Dict], str]] = None,
             max_rounds: int = MAX_ROUNDS,
             on_event: Optional[Callable[[Dict], None]] = None,
             kind: str = "chat", title: str = "") -> Dict:
    """多轮问答。`ai` 是 AIModel 实例，`reader` 提供仓库只读工具。

    返回 {reply, tools, patch, error, ai_mode, rounds}
    只读：本函数不写任何仓库文件。
    """
    def emit(evt: Dict) -> None:
        if on_event:
            try:
                on_event(dict(evt))
            except Exception:
                pass

    specs = list(reader.spec())
    if extra_specs:
        specs += list(extra_specs)
    if not specs:
        max_rounds = 1  # 没工具可调，多轮只会白烧 token

    system = CHAT_SYSTEM
    if allow_patch:
        system += "\n\n" + PROPOSAL_RULES
    if skills_text.strip():
        system += ("\n\n## 团队技能与规范（回答与改代码都必须遵守）\n"
                   + skills_text.strip()[:4000])

    base = build_prompt(messages, user_text)
    base += "\n\n## 仓库信息\n"
    if reader.tools_enabled:
        base += f"- 仓库根目录：{reader.root}\n"
        base += ("- 你只能通过工具访问仓库。路径用相对仓库根的形式，例如 "
                 "`packages/cloudpivot-ai/lui/components/xxx.vue`。\n")
    else:
        base += "- 本轮**没有开启仓库访问**，你看不到任何仓库内容。\n"
    base += "\n" + _tool_block(specs, max_rounds)

    tools_used: List[Dict] = []
    notes: List[str] = []
    rounds = 0
    res: Dict = {}
    text = ""

    for rnd in range(1, max_rounds + 1):
        rounds = rnd
        convo = base
        if notes:
            convo += "\n\n## 已经查到的仓库内容\n" + "\n\n".join(notes)
        if rnd == max_rounds:
            convo += ("\n\n## 最后要求\n不要再调用任何工具，直接给出最终回答"
                      "（Markdown 正文，引用代码请带 文件路径:行号）。")
        emit({"type": "stage", "stage": f"模型思考（第 {rnd}/{max_rounds} 轮）"})

        # 增量按 60 字符或换行合并后再发，避免一个 token 一个 SSE 事件把通道打满。
        # ⚠ 缓冲必须带 kind：模型先吐 reasoning 再吐 content，若统一按 content 收尾，
        # 最后一截思考会被当成正文渲染到回答里。
        buf: List[str] = []
        buf_kind = ["content"]
        buf_len = [0]

        def flush(_buf=buf, _kind=buf_kind, _n=buf_len):
            if _buf:
                emit({"type": "ai_delta", "kind": _kind[0], "text": "".join(_buf)})
                _buf.clear()
                _n[0] = 0

        def on_delta(k, piece, _buf=buf, _kind=buf_kind, _n=buf_len):
            piece = str(piece or "")
            if k and k != _kind[0]:
                flush()
                _kind[0] = k
            _buf.append(piece)
            _n[0] += len(piece)
            if _n[0] >= 60 or "\n" in piece:
                flush()

        res = ai.complete_text(convo, system, max_tokens=BUDGET, on_delta=on_delta)
        flush()
        if res.get("error"):
            return {"reply": "", "tools": tools_used, "patch": None,
                    "error": str(res["error"]), "ai_mode": getattr(ai, "mode", ""),
                    "rounds": rounds}
        text = res.get("text") or ""

        call = _as_tool_call(text)
        if call and rnd < max_rounds:
            name = call["tool"]
            args = call["arguments"]
            emit({"type": "stage", "stage": f"检索仓库：{_tool_label(name, args)}"})
            spec_names = {t["name"] for t in specs}
            if name not in spec_names:
                result = (f"[错误] 工具 {name!r} 不在可用列表里，"
                          f"只能用：{', '.join(sorted(spec_names))}")
            elif any(name == t["name"] for t in TOOL_SPECS):
                result = reader.call(name, args)
            else:
                try:
                    result = str(extra_executor(name, args)) if extra_executor else "[错误] 该工具不可用"
                except Exception as e:
                    result = f"[工具执行异常] {name}: {e}"
            result = _clip(result, MAX_TOOL_TEXT)
            tools_used.append({"name": name, "arguments": args, "chars": len(result)})
            emit({"type": "tool", "tool": name, "arguments": args,
                  "result": result[:1200], "chars": len(result)})
            notes.append(f"### 工具 {name} {json.dumps(args, ensure_ascii=False)}\n{result}")
            continue

        # 没有工具调用 = 本轮就是最终回答
        break

    # 轮次用尽时模型可能还在输出工具调用 JSON：那是内部协议，不能当成回答丢给用户
    if _as_tool_call(text):
        text = (text + "\n\n（模型在最后一轮仍在请求工具，没有给出正式回答，"
                       "可重试或换个模型。）")
    reply, patch, perr = split_proposal(text)
    if perr:
        emit({"type": "stage", "stage": perr})
    payload = proposal_payload(patch, getattr(ai, "mode", "")) if patch else None
    if payload and payload.get("error"):
        emit({"type": "stage", "stage": payload["error"]})
    return {"reply": reply, "tools": tools_used, "patch": payload,
            "error": "", "ai_mode": getattr(ai, "mode", ""),
            "rounds": rounds}


def _tool_label(name: str, args: Dict) -> str:
    if name == "repo_grep":
        return f"grep {args.get('pattern', '')[:40]}"
    if name == "repo_read":
        return f"读 {args.get('path', '')[:60]}"
    if name == "repo_list":
        return f"列目录 {args.get('path') or '.'}"
    return name


# ------------------------------------------------------------------ mock
_MOCK_CHANGE_WORDS = ("改", "修改", "加", "新增", "删除", "去掉", "重构", "替换", "统一", "调整")


def looks_like_change(text: str) -> bool:
    """粗略判断用户是不是在要求改代码——只用于 mock 演示时决定要不要附提案。"""
    return any(w in str(text or "") for w in _MOCK_CHANGE_WORDS)


def mock_chat_reply(user_text: str, allow_patch: bool = True) -> Dict:
    """离线演示输出。明确标注 mock，不伪造成真实回答（与 mock_task_result 同口径）。"""
    q = re.sub(r"\s+", " ", str(user_text or "")).strip()[:80]
    reply = (
        "**（Mock 演示回答）** 当前 AI 处于 mock 模式，没有真实调用大模型，"
        "所以这里不会给出真实结论。\n\n"
        f"你问的是：{q or '（空）'}\n\n"
        "配置真实模型（设置 → AI）后，这一步会：\n"
        "1. 自己调用只读工具（列目录 / 读文件 / 正则搜索）到仓库里找证据；\n"
        "2. 用 Markdown 回答，引用代码时带上 `文件路径:行号`；\n"
        "3. 如果你要的是改代码，会在回答末尾附一段改动提案，"
        "由你确认后落成「产出物」，采纳才写工作区。"
    )
    patch = None
    if allow_patch and looks_like_change(user_text):
        reply += ("\n\n（Mock 模式仍然会演示提案链路：下面附了一段**演示用**的改动提案，"
                  "内容不是真的改你的代码。）")
        patch = proposal_payload({
            "summary": "（Mock 演示）在示例组件里补一个可选属性",
            "files": [{
                "path": "src/components/Demo/Demo.vue",
                "action": "overwrite",
                "description": "（Mock 演示）演示用文件，采纳会新建这个文件",
                "content": ("<!-- MOCK 演示内容 —— 配置真实大模型后会按仓库真实代码生成 -->\n"
                            "<template>\n  <div class=\"demo\">{{ label }}</div>\n</template>\n\n"
                            "<script setup lang=\"ts\">\n"
                            "defineProps<{ label?: string }>();\n</script>\n"),
            }],
            "checklist": ["（Mock 演示）确认真实文件路径后再采纳"],
        }, "mock")
    return {"reply": reply, "tools": [], "patch": patch, "error": "",
            "ai_mode": "mock", "rounds": 1, "truncated": False}
