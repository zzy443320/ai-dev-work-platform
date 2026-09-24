"""AI facade: config-driven transport + JSON contract + an honest mock.

The real call lives in `ai_providers` so this module only deals with the prompt→
structured-result contract. Without a key (or with `mock: true`) we fall back to a
clearly-labelled mock that still emits *applicable* SEARCH/REPLACE blocks, so the
approval UI and the gate can be exercised offline — but `mode` says "mock" everywhere
so nobody mistakes it for real analysis.
"""
import json
import re
import time
from typing import Dict, List, Optional, Tuple

from . import usage
from .ai_providers import AIConfig, AIError, complete as _http_complete, describe, list_models
from .ai_providers import complete_stream as _http_complete_stream
from .ai_providers import usage_of
from .usage import estimate_call

# 推理模型吃光 max_tokens 的特征（见 ai_providers._missing_field_hint）
_TRUNCATION_MARKERS = ("max_tokens 全部用在了推理上", "推理耗尽了 max_tokens")


def _is_reasoning_truncation(error: str) -> bool:
    """判断失败是否是「推理模型烧光配额」——这是唯一值得自动加重试的失败。"""
    return any(m in (error or "") for m in _TRUNCATION_MARKERS)


class AIModel:
    def __init__(self, config: Optional[Dict] = None, *, model: str = "",
                 api_key: Optional[str] = None, provider: str = "", base_url: str = ""):
        # accept either a dict (preferred) or the legacy keyword form
        cfg = dict(config or {})
        if model:
            cfg.setdefault("model", model)
        if api_key:
            cfg.setdefault("api_key", api_key)
        if provider:
            cfg.setdefault("provider", provider)
        if base_url:
            cfg.setdefault("base_url", base_url)
        if not cfg.get("api_key"):
            import os

            env = os.environ.get("ANTHROPIC_API_KEY", "")
            if env:
                cfg["api_key"] = env
        self.cfg = AIConfig.from_dict(cfg)
        self.model = self.cfg.model
        self.api_key = self.cfg.api_key
        self.provider = self.cfg.provider
        self.base_url = self.cfg.base_url
        self.mode = self.cfg.mode

    # ------------------------------------------------------------------ api
    def complete(self, prompt: str, system: str = "",
                 max_tokens: Optional[int] = None, on_delta=None) -> dict:
        """prompt → structured dict.

        `on_delta(kind, text)`（可选）启用流式：kind 为 "reasoning"（思考过程）
        或 "content"（正式输出），随生成增量回调；最终仍走同一套 JSON 提取。
        """
        if self.cfg.is_mock:
            if on_delta:
                try:
                    on_delta("content", "(Mock 模式：无真实流式输出)")
                except Exception:
                    pass
            return self._mock_complete(prompt)
        text, err = self._call_raw(prompt, system, on_delta, max_tokens)
        if err:
            return {
                "error": err,
                "error_detail": getattr(self, "_last_error_detail", {}),
                "root_cause": f"(AI 调用失败: {err})",
                "category": "其他",
                "prevention": "",
                "patch_blocks": "",
            }
        return self._extract_json(text)

    def complete_text(self, prompt: str, system: str = "",
                      max_tokens: Optional[int] = None, on_delta=None) -> dict:
        """文本模式：直接返回模型原文，**不做 JSON 契约提取**。

        对话/问答用。理由：回答是自由 Markdown，
        （a）套 JSON 会被 `_extract_json` 的 raw 截断到 3000 字符，长回答直接丢尾；
        （b）正文里的代码块/示例 JSON 很容易被误判成候选而解析失败。
        账本、余额与「推理截断自动重试」与 complete() 共用同一条路径，口径一致。
        返回 {"text": ..., "error": ...}。
        """
        if self.cfg.is_mock:
            if on_delta:
                try:
                    on_delta("content", "(Mock 模式：无真实流式输出)")
                except Exception:
                    pass
            return {"text": "(Mock 模式：无真实输出)", "error": ""}
        text, err = self._call_raw(prompt, system, on_delta, max_tokens)
        if err:
            return {"text": "", "error": err,
                    "error_detail": getattr(self, "_last_error_detail", {})}
        return {"text": text, "error": ""}

    def _call_raw(self, prompt: str, system: str, on_delta,
                  max_tokens: Optional[int]) -> Tuple[str, str]:
        """一次真实请求（必要时自动重试一次）→ (text, err)。

        所有真实请求的唯一出口：账本记录与推理截断重试都只在这里发生，避免
        `complete` / `complete_text` 两条路径的用量口径走偏。
        """
        base_budget = int(max_tokens) if max_tokens else int(self.cfg.max_tokens)
        self.cfg.max_tokens = base_budget
        text, err = self._attempt(prompt, system, on_delta, attempt=1)
        if err and _is_reasoning_truncation(err):
            # 推理模型（deepseek-flash 等）的 hidden reasoning 与回答共享 max_tokens：
            # 大上下文（定位窗口动辄几十 KB）会把配额整个烧在思考上，响应里连
            # content 都没有（finish_reason=length）。这是**有界可自愈**的失败：
            # 自动把额度翻倍重试一次，避免用户看到"无法生成补丁"却其实是模型
            # 额度不够（与定位质量无关，容易误判成定位 bug）。
            try:
                self.cfg.max_tokens = min(base_budget * 2, 64000)
                text, err = self._attempt(prompt, system, on_delta, attempt=2)
            finally:
                self.cfg.max_tokens = base_budget  # 还原，避免污染后续调用预算
        return text, err

    def _attempt(self, prompt: str, system: str, on_delta,
                 *, step: str = "", attempt: int = 1) -> Tuple[str, str]:
        """单次请求：返回 (text, error)。error 非空时 text 无意义。

        每次请求都入用量账本（含失败——失败调用同样烧掉了 prompt token）。
        """
        t0 = time.time()
        self.cfg._last_usage = None
        try:
            if on_delta is not None:
                text = _http_complete_stream(self.cfg, prompt,
                                             system or _DEFAULT_SYSTEM, on_delta)
            else:
                text = _http_complete(self.cfg, prompt, system or _DEFAULT_SYSTEM)
        except AIError as e:
            self._last_error_detail = e.to_dict()
            self._record(prompt, system, "", time.time() - t0, False, e.message,
                         step=step, attempt=attempt)
            return "", (f"{e.message}（{e.kind}"
                        + (f" HTTP {e.status}" if e.status else "")
                        + (f"，{e.seconds}s" if e.seconds else "") + "）")
        self._record(prompt, system, text, time.time() - t0, True, "",
                     step=step, attempt=attempt)
        return text, ""

    def _record(self, prompt: str, system: str, reply: str, seconds: float,
                ok: bool, error: str, *, step: str = "", attempt: int = 1) -> None:
        """写一条用量账本。

        - mock 不入账（不消耗 token，记进去只会污染调用次数与均值）；
        - 网关给了 usage 就用真值，没给就按字符估算并打 `estimated` 标记；
        - 记账失败绝不影响主流程（usage.record 自己保证不抛）。
        """
        if self.cfg.is_mock:
            return
        got = getattr(self.cfg, "_last_usage", None) or estimate_call(prompt, system, reply)
        usage.record(
            model=self.cfg.model, mode=self.cfg.mode,
            input_tokens=got.get("input") or 0,
            output_tokens=got.get("output") or 0,
            reasoning_tokens=got.get("reasoning") or 0,
            estimated=bool(got.get("estimated")),
            seconds=seconds, ok=ok, error=error,
            step=step or None, attempt=attempt,
        )

    def describe_request(self) -> Dict:
        """Exact outbound request shape, secrets masked — for the test panel."""
        try:
            return describe(self.cfg)
        except Exception as e:  # pragma: no cover - describe is pure
            return {"error": str(e)}

    def probe(self) -> Dict:
        """Tiny real round-trip so relay misconfiguration is visible immediately."""
        problems = self.cfg.validate()
        if problems:
            return {"ok": False, "errors": problems, "request": self.describe_request()}

        prompt, system = "只回复两个字：收到", "你是连通性探针。"
        t0 = time.time()
        u2: Dict = {}
        with usage.task("ai_test", title="模型连通性测试", step="probe"):
            try:
                text = _http_complete(self.cfg, prompt, system)
            except AIError as e:
                self._record(prompt, system, "", time.time() - t0, False, e.message)
                out = e.to_dict()
                out.update({"ok": False, "request": self.describe_request(),
                            "errors": [e.message]})
                return out
            self._record(prompt, system, text, time.time() - t0, True, "")
            # 探针里那次极短的 usage 探测也是一次真实调用，一并入账
            u2 = usage_of(self.cfg, "ping", "")
            if u2:
                usage.record(model=self.cfg.model, mode=self.cfg.mode,
                             input_tokens=u2.get("input") or 0,
                             output_tokens=u2.get("output") or 0,
                             ok=True, step="probe_usage")
        return {
            "ok": True,
            "seconds": round(time.time() - t0, 2),
            "reply": text.strip()[:120],
            "usage": u2,
            "request": self.describe_request(),
            "errors": [],
        }

    def available_models(self) -> List[str]:
        return list_models(self.cfg)

    # ------------------------------------------------------------------ mock
    def _mock_complete(self, prompt: str) -> dict:
        text = prompt.lower()
        files = _prompt_files(prompt)
        null_kws = ["typeerror", "undefined", "cannot read", "is not a function", "null"]
        ui_kws = ["样式", "css", "错位", "样式异常", "显示异常", "移动端", "按钮"]
        logic_kws = ["刷新", "更新", "未生效", "数据不一致", "提交后"]

        if any(k in text for k in null_kws):
            return {
                "root_cause": "对可能为 null/undefined 的对象未做空值校验，直接访问属性导致 TypeError。",
                "category": "数据",
                "explanation": "在属性访问点加 optional chaining 并给渲染兜底值，异常即消失。",
                "prevention": "1) TS strict + noUncheckedIndexedAccess；2) 接口响应加 zod schema 校验；3) 关键数据访问点加防御性默认值。",
                "patch_blocks": _mock_patch_for_null(files),
            }
        if any(k in text for k in ui_kws):
            return {
                "root_cause": "样式在特定分辨率或浏览器下未做适配，布局约束缺失。",
                "category": "UI",
                "explanation": "补充响应式断点与 overflow 兜底，重叠问题在小屏下消除。",
                "prevention": "1) stylelint + visual regression 测试；2) 关键页面加入 Playwright 多分辨率截图用例。",
                "patch_blocks": _mock_patch_for_css(files),
            }
        if any(k in text for k in logic_kws):
            return {
                "root_cause": "状态更新或缓存失效逻辑不完整，提交后未触发列表/详情重新拉取。",
                "category": "逻辑",
                "explanation": "在提交成功回调里显式失效对应 query key，列表即可自动刷新。",
                "prevention": "1) 封装统一的 useSubmitMutation hook 强制刷新策略；2) E2E 覆盖提交后断言列表变化。",
                "patch_blocks": "",
            }
        return {
            "root_cause": "(mock 默认输出)请人工 review 嫌疑文件后补充根因。",
            "category": "其他",
            "explanation": "",
            "prevention": "补充对应单元测试覆盖该场景。",
            "patch_blocks": "",
        }

    # ------------------------------------------------------------------ json
    def _extract_json(self, text: str) -> dict:
        def _coerce(obj) -> dict:
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            obj.setdefault("patch_blocks", obj.get("fix_suggestion", ""))
            obj.setdefault("root_cause", "")
            obj.setdefault("category", "其他")
            return obj

        for candidate in _json_candidates(text):
            try:
                return _coerce(json.loads(candidate))
            except Exception:
                continue

        # JSON 被截断（reasoning 模型吃掉了大量 max_tokens）时，逐个字段抢救
        # 已经完整输出的字符串值——部分结果也远好于把整段原始 JSON 塞进 root_cause。
        salvaged = _salvage_fields(text)
        if salvaged.get("root_cause"):
            salvaged.setdefault("category", "其他")
            salvaged.setdefault("patch_blocks", salvaged.get("fix_suggestion", ""))
            salvaged["error"] = (
                "模型输出的 JSON 不完整（疑似被 max_tokens 截断），"
                "已抢救出可读字段；补丁可能缺失，建议提高 max_tokens 后重试"
            )
            return salvaged

        return {
            "error": "模型输出不是合法 JSON，已按原文保留，未生成任何补丁",
            "raw": text[:3000],
            "root_cause": text[:500],
            "category": "其他",
            "prevention": "",
            "patch_blocks": "",
        }


_DEFAULT_SYSTEM = "你是资深前端工程师，擅长分析缺陷并给出最小改动补丁。只输出 JSON。"


def _salvage_fields(text: str) -> Dict:
    """Recover complete `"key": "value"` pairs from a truncated JSON object.

    A reasoning model can burn most of max_tokens on hidden reasoning, leaving the
    JSON string cut mid-way. The analysis fields usually finish before the long
    `patch_blocks`, so extracting the closed string values still gives the user a
    usable root cause instead of a wall of raw JSON.
    """
    out: Dict[str, str] = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL):
        key, raw = m.group(1), m.group(2)
        try:
            out[key] = json.loads(f'"{raw}"')
        except Exception:
            out[key] = raw
    return out


def _json_candidates(text: str) -> List[str]:
    """Yield plausible JSON objects: fenced blocks first, then raw_decode scan.

    raw_decode respects string literals, so code content containing braces
    inside JSON string values no longer breaks extraction."""
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        yield m.group(1)
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _end = dec.raw_decode(text[i:])
            yield json.dumps(obj, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        i = text.find("{", i + 1)


# ------------------------------------------------------- mock patch helpers
def _prompt_files(prompt: str) -> Dict[str, str]:
    """Recover {path: content} from the `### path` sections we put in the prompt."""
    out: Dict[str, str] = {}
    for m in re.finditer(r"^### (.+?)\n```\n(.*?)\n```", prompt, re.DOTALL | re.MULTILINE):
        out[m.group(1).strip()] = m.group(2)
    return out


def _mock_patch_for_null(files: Dict[str, str]) -> str:
    """Turn the first `a.b.c` property access lacking ?. into a real SEARCH/REPLACE."""
    blocks: List[str] = []
    for path, content in files.items():
        if path.endswith((".less", ".scss", ".css")):
            continue
        for line in content.split("\n"):
            if "?." in line or "//" in line:
                continue
            m = re.search(r"([\w$]+(?:\.[\w$]+)+)", line)
            if not m:
                continue
            expr = m.group(1)
            fixed = _add_optional_chaining(expr)
            if fixed == expr:
                continue
            blocks.append(
                f"<<<<<<< SEARCH {path}\n{line}\n=======\n{line.replace(expr, fixed)}\n>>>>>>> REPLACE"
            )
            break
        if blocks:
            break
    return "\n\n".join(blocks)


def _add_optional_chaining(expr: str) -> str:
    parts = expr.split(".")
    if len(parts) < 2:
        return expr
    # 每个属性访问点都加 ?. 保护（props.a.b.c → props.a?.b?.c），
    # 只护最后一段对 props.a.b undefined 的中间层无能为力。
    # 根对象与第一个属性之间保持普通 "."。
    rest = parts[1:]
    return parts[0] + "".join(("." if i == 0 else "?.") + p for i, p in enumerate(rest))


def _mock_patch_for_css(files: Dict[str, str]) -> str:
    """Append a responsive guard to the first stylesheet we were shown."""
    for path, content in files.items():
        if not path.endswith((".less", ".scss", ".css")):
            continue
        block = content.rstrip("\n").split("\n")
        if len(block) < 3:
            continue
        tail = "\n".join(block[-2:])
        return (
            f"<<<<<<< SEARCH {path}\n{tail}\n=======\n{tail}\n\n"
            "@media (max-width: 768px) {\n  :global(.footer-actions) { flex-wrap: wrap; gap: 8px; }\n}\n"
            ">>>>>>> REPLACE"
        )
    return ""
