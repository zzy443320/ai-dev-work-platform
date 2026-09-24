"""Provider-agnostic LLM transport, entirely config-driven.

Nothing here assumes a vendor: the wire format (Anthropic Messages vs OpenAI
Chat Completions), the base URL, the endpoint path, which JSON field carries the
answer, auth header shape, extra headers/body, proxy and TLS verification are all
configuration. That is what makes relays/gateways/self-hosted endpoints work
without touching code.

Only `requests` is used — no vendor SDK — so a relay that deviates slightly from
the official shape can still be accommodated through config.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests

from .usage import estimate_call as _estimate_call

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "custom": "",
}
DEFAULT_ENDPOINT = {
    "anthropic": "/v1/messages",
    "openai": "/chat/completions",
    "custom": "/chat/completions",
}
DEFAULT_MODELS_PATH = {
    "anthropic": "/v1/models",
    "openai": "/models",
    "custom": "/models",
}
DEFAULT_CONTENT_PATH = {
    "anthropic": "content.0.text",
    "openai": "choices.0.message.content",
    "custom": "choices.0.message.content",
}


class AIError(Exception):
    """Carries everything needed to debug a relay: url, status, body excerpt."""

    def __init__(self, message: str, *, url: str = "", status: int = 0,
                 body: str = "", seconds: float = 0.0, kind: str = "request"):
        super().__init__(message)
        self.message = message
        self.url = url
        self.status = status
        self.body = body[:1500]
        self.seconds = round(seconds, 2)
        self.kind = kind  # config | request | http | parse

    def to_dict(self) -> Dict:
        return {
            "error": self.message,
            "kind": self.kind,
            "url": self.url,
            "status": self.status,
            "body": self.body,
            "seconds": self.seconds,
        }


def _join(base: str, path: str) -> str:
    """base + path without doubling or dropping the slash."""
    base = (base or "").rstrip("/")
    path = (path or "").strip()
    if not path:
        return base
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base}/{path.lstrip('/')}"


def _usage_from(payload) -> Optional[Dict]:
    """把各家回包的 usage 归一成 {input, output, reasoning, estimated}。

    兼容 OpenAI（prompt_tokens/completion_tokens）、Anthropic
    （input_tokens/output_tokens）以及把用量塞在别处的网关；认不出就返回 None,
    由调用方走估算并打 estimated 标记（绝不假装是账单数据）。
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("usage") or payload.get("usageMetadata") or {}
    if not isinstance(raw, dict) or not raw:
        return None
    inp = raw.get("input_tokens")
    if inp is None:
        inp = raw.get("prompt_tokens")
    if inp is None:
        inp = raw.get("promptTokenCount")
    out = raw.get("output_tokens")
    if out is None:
        out = raw.get("completion_tokens")
    if out is None:
        out = raw.get("candidatesTokenCount")
    if inp is None and out is None:
        return None
    detail = raw.get("completion_tokens_details") or raw.get("output_tokens_details")
    reasoning = 0
    if isinstance(detail, dict):
        reasoning = detail.get("reasoning_tokens") or 0
    try:
        return {"input": int(inp or 0), "output": int(out or 0),
                "reasoning": int(reasoning or 0), "estimated": False}
    except (TypeError, ValueError):
        return None


def _stash_usage(cfg: "AIConfig", payload, prompt: str, system: str,
                 reply: str, reasoning: str = "") -> Dict:
    """把这一次调用的用量挂到 cfg 上（`_last_usage`），由 ai_model 负责入账。

    传输层只负责「如实报告这一次用了多少」，不关心账本——这样记账逻辑只有一处。
    """
    got = _usage_from(payload)
    if got is None:
        got = _estimate_call(prompt, system, reply, reasoning)
    cfg._last_usage = got
    return got


@dataclass
class AIConfig:
    provider: str = "anthropic"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    mock: bool = False

    temperature: float = 1.0
    max_tokens: int = 4000
    top_p: Optional[float] = None
    timeout: int = 120
    proxy: str = ""
    verify_ssl: bool = True

    endpoint: str = ""            # override the chat path, e.g. /v1/chat/completions
    models_path: str = ""         # override the model-list path
    content_path: str = ""        # dot-path to the answer text in the response JSON
    auth_style: str = ""          # bearer | x-api-key | header | none
    auth_header: str = ""         # used with auth_style=header, e.g. X-Auth-Token
    api_key_header: str = ""      # custom header name carrying the raw key
    json_mode: bool = True        # ask the endpoint for strict JSON output
    # 流式请求里附上 stream_options.include_usage，好让网关把真实 token 用量带回来
    # （不带这个字段时多数网关的 SSE 里没有任何 usage）。网关不认这个字段时自动降级重试。
    stream_usage: bool = True
    max_tokens_field: str = ""    # e.g. max_completion_tokens for newer endpoints
    system_role: str = ""         # e.g. developer; some gateways renamed "system"
    extra_headers: Dict = field(default_factory=dict)
    extra_body: Dict = field(default_factory=dict)
    name: str = ""                # label only, for the UI

    # ------------------------------------------------------------ factories
    @classmethod
    def from_dict(cls, d: Optional[Dict]) -> "AIConfig":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        for k in unknown:  # tolerate future keys instead of exploding
            d.pop(k)
        cfg = cls(**d)
        cfg.normalize()
        return cfg

    def normalize(self) -> "AIConfig":
        p = (self.provider or "anthropic").strip().lower()
        if p in ("openai-compatible", "openai_compat", "oai", "azure-openai"):
            p = "openai"
        if p in ("proxy", "relay", "gateway"):
            p = "custom"
        self.provider = p or "anthropic"
        self.base_url = (self.base_url or "").strip().rstrip("/")
        if not self.base_url and self.provider != "custom":
            self.base_url = DEFAULT_BASE.get(self.provider, "")
        self.endpoint = (self.endpoint or "").strip() or DEFAULT_ENDPOINT[self.provider]
        self.models_path = (self.models_path or "").strip() or DEFAULT_MODELS_PATH[self.provider]
        self.content_path = (self.content_path or "").strip() or DEFAULT_CONTENT_PATH[self.provider]
        self.auth_style = (self.auth_style or "").strip().lower() or self._default_auth()
        self.max_tokens_field = (self.max_tokens_field or "").strip() or "max_tokens"
        self.system_role = (self.system_role or "").strip() or "system"
        self.temperature = _as_float(self.temperature, 1.0)
        self.max_tokens = _as_int(self.max_tokens, 4000)
        self.timeout = _as_int(self.timeout, 120)
        self.top_p = _as_float(self.top_p, None)
        self.stream_usage = _as_bool(self.stream_usage, True)
        return self

    def _default_auth(self) -> str:
        if self.provider == "anthropic":
            return "x-api-key"
        if self.provider == "custom":
            return "header" if self.api_key_header else "bearer"
        return "bearer"

    # -------------------------------------------------------------- derived
    @property
    def has_key(self) -> bool:
        return bool((self.api_key or "").strip())

    @property
    def mode(self) -> str:
        return "mock" if (self.mock or not self.has_key) else self.provider

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"

    @property
    def chat_url(self) -> str:
        return _join(self.base_url, self.endpoint)

    @property
    def models_url(self) -> str:
        return _join(self.base_url, self.models_path)

    def redacted(self) -> Dict:
        """Safe-to-echo view of the config (never includes the key)."""
        d = self.__dict__.copy()
        d.pop("api_key", None)
        d["chat_url"] = self.chat_url
        d["models_url"] = self.models_url
        d["mode"] = self.mode
        return d

    def validate(self) -> List[str]:
        problems = []
        if self.mock:
            return problems
        if not self.has_key:
            problems.append("未填写 API Key（勾选 Mock 可跳过真实调用）")
        if not self.base_url:
            problems.append("custom 接入方必须填写 Base URL；或改用 anthropic/openai 以使用官方默认地址")
        if not self.model:
            problems.append("未填写模型名称")
        for label, url in (("Base URL", self.base_url), ("Chat 地址", self.chat_url)):
            if url and urlsplit(url).scheme not in ("http", "https"):
                problems.append(f"{label} 不是合法 URL：{url}")
        if self.verify_ssl is False:
            problems.append("已关闭 TLS 证书校验，仅建议在内网自签证书场景使用")
        return problems

    # ------------------------------------------------------------- requests
    def build(self, prompt: str, system: str) -> Tuple[str, Dict, Dict, Dict]:
        """Return (url, headers, params, body) for a chat call."""
        if self.provider == "anthropic":
            body: Dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                body["system"] = system
            if self.top_p is not None:
                body["top_p"] = self.top_p
        else:
            messages = []
            if system:
                messages.append({"role": self.system_role, "content": system})
            messages.append({"role": "user", "content": prompt})
            body = {"model": self.model, "messages": messages,
                    "temperature": self.temperature}
            body[self.max_tokens_field] = self.max_tokens
            if self.top_p is not None:
                body["top_p"] = self.top_p
            if self.json_mode:
                body["response_format"] = {"type": "json_object"}
        body.update(self.extra_body or {})

        headers = {"content-type": "application/json"}
        key = (self.api_key or "").strip()
        style = self.auth_style
        if style == "bearer":
            headers["authorization"] = f"Bearer {key}"
        elif style == "x-api-key":
            headers["x-api-key"] = key
            headers["anthropic-version"] = ANTHROPIC_VERSION
        elif style == "header":
            name = self.auth_header or self.api_key_header or "x-api-key"
            headers[name] = key
        elif style == "query":
            pass  # handled in params below
        # style == "none": relay 走 IP 白名单，不带 key

        params = {}
        if style == "query":
            params[self.auth_header or "key"] = key
        params.update({k: v for k, v in (self.extra_body or {}).items()
                       if isinstance(v, (str, int, float, bool)) and k in ("api-version",)})

        headers = {**headers, **(self.extra_headers or {})}
        return self.chat_url, headers, params, body

    def session(self) -> requests.Session:
        s = requests.Session()
        s.verify = bool(self.verify_ssl)
        if self.proxy:
            s.proxies = {"http": self.proxy, "https": self.proxy}
        return s

    def _missing_field_hint(self, seg: str, cur: dict) -> str:
        """Explain *why* the answer field is absent — not just that it is.

        Reasoning models (deepseek-*/o-series/glm-*) put hidden reasoning tokens in
        the same `max_tokens` budget as the visible answer. When that budget runs out
        the reply arrives as `{"role": "assistant"}` with `finish_reason: "length"`
        and no `content` at all. Blaming the "响应取值路径" for that sends the user
        to fix the wrong thing, so detect the truncation signature explicitly.
        """
        try:
            payload = self._last_payload if isinstance(self._last_payload, dict) else {}
        except AttributeError:
            payload = {}
        finish = ""
        reasoning = 0
        try:
            choice = payload.get("choices", [{}])[0]
            finish = str(choice.get("finish_reason") or "")
            usage = payload.get("usage") or {}
            reasoning = int((usage.get("completion_tokens_details") or {})
                            .get("reasoning_tokens") or 0)
        except (IndexError, TypeError, ValueError, AttributeError):
            pass

        if finish == "length" or reasoning:
            return (
                f"模型把 max_tokens 全部用在了推理上，正式回答还没开始就被截断，"
                f"所以响应里没有 {seg!r}（reasoning_tokens={reasoning}，"
                f"finish_reason={finish or 'length'}）。"
                "这不是取值路径的问题：请改用非推理模型（如 gpt-plus / kimi-pro），"
                "或把 max_tokens 调大（建议 ≥8000）后重试"
            )
        return (
            f"响应里缺少字段 {seg!r}（完整路径 {self.content_path}）；"
            f"该响应实际包含: {sorted(cur.keys()) or '（空对象）'}。"
            "若不是推理模型截断，请用「响应取值路径」指定该网关实际返回的结构"
        )

    def extract_text(self, payload) -> str:
        self._last_payload = payload
        cur = payload
        for seg in (self.content_path or "").split("."):
            if seg == "":
                continue
            if isinstance(cur, list):
                try:
                    cur = cur[int(seg)]
                except (ValueError, IndexError):
                    raise AIError(
                        f"响应里取不到 {self.content_path}（数组下标 {seg} 越界或非数组）",
                        kind="parse",
                    )
            elif isinstance(cur, dict):
                if seg not in cur:
                    raise AIError(self._missing_field_hint(seg, cur), kind="parse")
                cur = cur[seg]
            else:
                raise AIError(f"响应路径 {self.content_path} 在 {seg!r} 处不是对象/数组",
                              kind="parse")
        if isinstance(cur, str):
            return cur
        if isinstance(cur, list):  # anthropic thinking+text 块混排
            parts = []
            for block in cur:
                if isinstance(block, dict) and block.get("type") in (None, "text"):
                    parts.append(block.get("text", ""))
            return "\n".join(p for p in parts if p)
        return json.dumps(cur, ensure_ascii=False)


def complete(cfg: AIConfig, prompt: str, system: str = "") -> str:
    """One chat turn → raw text. Raises AIError with a debuggable payload."""
    problems = cfg.validate()
    if problems:
        raise AIError("；".join(problems), kind="config")

    url, headers, params, body = cfg.build(prompt, system)
    t0 = time.time()
    try:
        resp = cfg.session().post(url, headers=headers, params=params, json=body,
                                  timeout=cfg.timeout)
    except requests.exceptions.SSLError as e:
        raise AIError(f"TLS 握手失败：{e}", url=url, kind="request",
                      seconds=time.time() - t0,
                      body="若为内网自签证书，可勾选「跳过 TLS 证书校验」或配置 CA") from e
    except requests.exceptions.ProxyError as e:
        raise AIError(f"代理不可达：{e}", url=url, kind="request",
                      seconds=time.time() - t0) from e
    except requests.exceptions.RequestException as e:
        raise AIError(f"请求失败：{type(e).__name__}: {e}", url=url, kind="request",
                      seconds=time.time() - t0) from e

    elapsed = time.time() - t0
    raw = resp.text or ""
    if resp.status_code >= 400:
        raise AIError(f"网关返回 HTTP {resp.status_code}", url=url, status=resp.status_code,
                      body=raw, seconds=elapsed, kind="http")
    try:
        payload = resp.json()
    except ValueError:
        raise AIError("响应不是 JSON（可能被登录页/风控页拦截了）", url=url,
                      status=resp.status_code, body=raw, seconds=elapsed, kind="parse")
    try:
        text = cfg.extract_text(payload)
    except AIError as e:
        # 先挂用量再抛：失败调用同样消耗了 prompt token，账本要记下来
        _stash_usage(cfg, payload, prompt, system, "")
        # a shape mismatch is far easier to debug with the url + body attached
        e.url = e.url or url
        e.status = e.status or resp.status_code
        e.body = e.body or raw
        e.seconds = e.seconds or round(elapsed, 2)
        raise
    _stash_usage(cfg, payload, prompt, system, text)
    if not text.strip():
        raise AIError("响应里的目标字段为空", url=url, status=resp.status_code,
                      body=raw, seconds=elapsed, kind="parse")
    return text


def complete_stream(cfg: AIConfig, prompt: str, system: str = "",
                    on_delta=None) -> str:
    """Streaming variant of `complete` — same request + "stream": true.

    `on_delta(kind, text)` fires incrementally with kind in
    ("reasoning", "content"): reasoning models (deepseek-*/o-series) stream
    hidden thinking as `delta.reasoning_content`/`delta.reasoning`, the visible
    answer as `delta.content`. Returns the full visible text; raises AIError on
    config/HTTP/parse problems. If the gateway ignores stream:true and answers
    with a plain JSON body, we degrade gracefully (one single content delta).
    """
    problems = cfg.validate()
    if problems:
        raise AIError("；".join(problems), kind="config")

    url, headers, params, body = cfg.build(prompt, system)
    body["stream"] = True
    t0 = time.time()

    def _fail(e: AIError) -> AIError:
        e.url = e.url or url
        e.seconds = e.seconds or round(time.time() - t0, 2)
        return e

    def _post(with_usage: bool):
        b = dict(body)
        if with_usage:
            b["stream_options"] = {"include_usage": True}
        return cfg.session().post(url, headers=headers, params=params, json=b,
                                  timeout=cfg.timeout, stream=True)

    # 绝大多数网关只有在收到 stream_options.include_usage 时才会在 SSE 里带用量，
    # 不带就只能按字符估算。用户用 extra_body 自己写过 stream_options 时不插手。
    ask_usage = (bool(cfg.stream_usage) and cfg.provider != "anthropic"
                 and "stream_options" not in body)
    try:
        resp = _post(ask_usage)
    except requests.exceptions.RequestException as e:
        raise _fail(AIError(f"请求失败：{type(e).__name__}: {e}", url=url,
                            kind="request")) from e

    if resp.status_code >= 400 and ask_usage and resp.status_code in (400, 415, 422):
        # 网关不认 stream_options。为了「多要一个字段」把整个调用搞挂不值得：
        # 降级重试一次，并在本进程内记住不再尝试（后续走估算 + estimated 标记）。
        try:
            resp.close()
        except Exception:
            pass
        cfg.stream_usage = False
        try:
            resp = _post(False)
        except requests.exceptions.RequestException as e:
            raise _fail(AIError(f"请求失败：{type(e).__name__}: {e}", url=url,
                                kind="request")) from e

    if resp.status_code >= 400:
        raw = ""
        try:
            raw = resp.text or ""
        except Exception:
            pass
        raise _fail(AIError(f"网关返回 HTTP {resp.status_code}", url=url,
                            status=resp.status_code, body=raw, kind="http"))

    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" not in ctype:
        # 网关不支持流式：按普通 JSON 兜底，行为与 complete() 一致
        raw = resp.text or ""
        try:
            payload = resp.json()
        except ValueError:
            raise _fail(AIError("响应不是 JSON（网关既未流式返回也不是 JSON）",
                                url=url, status=resp.status_code, body=raw,
                                kind="parse"))
        text = cfg.extract_text(payload)
        _stash_usage(cfg, payload, prompt, system, text)
        if not text.strip():
            raise _fail(AIError("响应里的目标字段为空", url=url,
                                status=resp.status_code, body=raw, kind="parse"))
        if on_delta:
            try:
                on_delta("content", text)
            except Exception:
                pass
        return text

    parts: List[str] = []
    think_parts: List[str] = []
    last_usage = None
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue  # 注释/心跳行
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            if isinstance(obj.get("error"), dict):
                msg = str(obj["error"].get("message") or obj["error"])
                raise _fail(AIError(f"流式响应返回错误：{msg}", url=url, kind="http"))
            # 带 include_usage 时用量在最后一块（choices 为空的收尾块）里
            if isinstance(obj.get("usage"), dict) and obj["usage"]:
                last_usage = obj["usage"]
            choices = obj.get("choices") or []
            delta = {}
            if choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or {}
            think = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if isinstance(think, str) and think:
                think_parts.append(think)
                if on_delta:
                    try:
                        on_delta("reasoning", think)
                    except Exception:
                        pass
            piece = delta.get("content")
            if isinstance(piece, str) and piece:
                parts.append(piece)
                if on_delta:
                    try:
                        on_delta("content", piece)
                    except Exception:
                        pass
    except AIError:
        raise
    except requests.exceptions.RequestException as e:
        raise _fail(AIError(f"流式读取中断：{type(e).__name__}: {e}", url=url,
                            kind="request")) from e

    text = "".join(parts)
    # 用量会在估算时把 reasoning 一并计入——推理模型的思考量往往比正文大得多，
    # 漏掉它会让"用量"这个数字小得离谱。
    _stash_usage(cfg, {"usage": last_usage} if last_usage else None,
                 prompt, system, text, "".join(think_parts))
    if not text.strip():
        raise _fail(AIError("流式响应里没有 content 内容（可能推理耗尽了 max_tokens）",
                            url=url, kind="parse"))
    return text


def usage_of(cfg: AIConfig, prompt: str, system: str) -> Dict:
    """Best-effort token usage for the connectivity test panel."""
    try:
        url, headers, params, body = cfg.build(prompt, system)
        body["max_tokens"] = body.get(cfg.max_tokens_field) or 16
        resp = cfg.session().post(url, headers=headers, params=params, json=body,
                                  timeout=cfg.timeout)
        data = resp.json()
        u = data.get("usage") or {}
        return {
            "input": u.get("input_tokens") or u.get("prompt_tokens"),
            "output": u.get("output_tokens") or u.get("completion_tokens"),
        }
    except Exception:
        return {}


def list_models(cfg: AIConfig) -> List[str]:
    url = cfg.models_url
    if not url:
        raise AIError("custom 接入方需填写「模型列表路径」才能拉取模型", kind="config")
    if not cfg.has_key:
        raise AIError("未填写 API Key，无法拉取模型列表", kind="config")
    headers = {"content-type": "application/json"}
    key = cfg.api_key.strip()
    if cfg.auth_style == "bearer":
        headers["authorization"] = f"Bearer {key}"
    elif cfg.auth_style == "x-api-key":
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif cfg.auth_style == "header":
        headers[cfg.auth_header or cfg.api_key_header or "x-api-key"] = key
    headers.update(cfg.extra_headers or {})
    params = {}
    if cfg.auth_style == "query":
        params[cfg.auth_header or "key"] = key
    t0 = time.time()
    try:
        resp = cfg.session().get(url, headers=headers, params=params, timeout=cfg.timeout)
    except requests.exceptions.RequestException as e:
        raise AIError(f"拉取模型列表失败：{e}", url=url, kind="request",
                      seconds=time.time() - t0) from e
    if resp.status_code >= 400:
        raise AIError(f"拉取模型列表返回 HTTP {resp.status_code}", url=url,
                      status=resp.status_code, body=resp.text, seconds=time.time() - t0,
                      kind="http")
    try:
        data = resp.json()
    except ValueError:
        raise AIError("模型列表响应不是 JSON", url=url, body=resp.text, kind="parse")
    items = data
    if isinstance(data, dict):
        for k in ("data", "models", "body", "result", "items"):
            if isinstance(data.get(k), list):
                items = data[k]
                break
        else:
            items = []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            mid = it.get("id") or it.get("name") or it.get("model") or it.get("slug")
            if mid:
                out.append(str(mid))
    return sorted(set(out))


def describe(cfg: AIConfig) -> Dict:
    """What the UI shows after a successful test: exact request shape, minus secrets."""
    url, headers, params, body = cfg.build("…", "…")
    masked = {}
    for k, v in headers.items():
        low = k.lower()
        if low in ("authorization", "x-api-key", "api-key") or "token" in low or "key" in low:
            masked[k] = _mask_value(v)
        else:
            masked[k] = v
    return {
        "method": "POST",
        "url": url,
        "headers": masked,
        "params": params,
        "body_keys": sorted(body.keys()),
        "content_path": cfg.content_path,
    }


def _mask_value(v: str) -> str:
    v = str(v)
    if len(v) <= 10:
        return "*" * len(v)
    return f"{v[:6]}…{v[-4:]} ({len(v)} chars)"


def _as_float(v, default):
    try:
        if v in (None, ""):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default):
    try:
        if v in (None, ""):
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _as_bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v in (None, ""):
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")
