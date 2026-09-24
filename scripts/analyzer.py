"""Stage 2: locate suspect code and ask the model for SEARCH/REPLACE patches."""
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from . import usage
from .patch_engine import parse_blocks, render_block_prompt

# ONES 描述是富文本 HTML：标签属性会产生海量垃圾符号（class/data/mime/uuid…）
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
# 富文本的段落结构只存在于标签里（工单常常一个裸换行都没有），所以块级
# 结束标签要换成换行、<br> 换成换行，否则整段文字会被压成一行（知识库卡片
# 的「现象」区就会全部挤在一起）。
_BR_RE = re.compile(r"(?is)<br\s*/?>")
_BLOCK_END_RE = re.compile(
    r"(?is)</(p|div|li|tr|h[1-6]|blockquote|section|article|dd|dt|table|ul|ol|pre)\s*>")
_BLOCK_START_RE = re.compile(
    r"(?is)<(p|div|li|tr|h[1-6]|blockquote|section|article|dd|dt|table|ul|ol|pre)"
    r"(\s[^>]*)?>")
_HR_RE = re.compile(r"(?is)<hr\s*/?>")
_CELL_END_RE = re.compile(r"(?is)</(td|th)\s*>")
_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)
_ENTITIES = (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
             ("&quot;", '"'), ("&#39;", "'"), ("&hellip;", "…"))

# 命中这些路径特征的视为第三方/构建产物，不作为嫌疑文件
_THIRD_PARTY_RE = re.compile(
    r"(^|/)(node_modules|dist|build|public|coverage)(/|$)"
    r"|\.min\.(js|css)$|\.map$|^yarn\.lock$|^package-lock\.json$"
    r"|demo_index|iconfont|monaco-editor|kindeditor", re.I)

# 中文停用词：ONES 工单模板里的样板词，对代码定位没有价值
_ZH_STOPWORDS = {"测试环境", "测试分支", "测试账号", "测试链接", "迭代", "缺陷",
                 "问题描述", "复现步骤", "预期结果", "实际结果", "页面", "显示",
                 "异常", "问题", "功能", "应该", "需要", "可以", "没有", "进行"}

# 路径段里的通用词：命中它们说明不了任何定位信息（ai/agent/index 到处都是）
_GENERIC_SEGMENTS = {"index", "main", "app", "src", "lib", "test", "tests", "utils",
                     "common", "shared", "core", "config", "types", "const", "demo",
                     "ai", "agent", "package", "packages", "web", "node",
                     "task", "tasks"}

# ── 工单模板噪音：ONES 缺陷单有固定栏目（【测试环境】/【测试分支】/【测试账号】…），
# 里面的值对代码定位毫无价值，却会变成高分关键词把真正的修改点埋掉。
# 事故（2026-09-23）：「【测试账号】demo/Acme@123456」让 path_tokens 只拿到
# demo/Acme → segment `acme`（高频短词，几乎命中全仓库），
# 于是 tier-0 目录失准；「【测试分支】develop」又在全仓 91 个文件里命中，
# 把 AI 运行日志组件彻底挤出读取窗口。
_NOISE_TOKENS = {
    # 环境/流程样板词
    "develop", "development", "production", "release", "master", "origin",
    "local", "localhost", "staging", "preview", "pre", "dev", "test", "tests",
    "npm", "yarn", "pnpm", "taobao", "registry", "install", "start", "build",
    "lint", "node_env", "test_env",
    # 工单里出现的演示账号/域名片段
    "acme", "demo", "sample", "example", "admin", "root", "password",
    "token", "secret", "apikey", "localhost",
    # 常见版本/技术栈噪音
    "typescript", "javascript", "webpack", "vite", "rsbuild", "babel", "eslint",
    "prettier", "husky", "commit", "branch", "merge", "request", "pull",
}
# 单 token 前缀命中会误伤真词，所以「子串形态」的噪音只用于路径段过滤
# （segment 必须是整段相等才过滤）与超高频关键词过滤。

# 截图/附件文件名：图注（d1-time-list.png）会变成关键词语料，纯垃圾
_IMG_NAME_RE = re.compile(r"[\w-]+\.(?:png|jpe?g|gif|bmp|webp|svg)\b", re.I)

# 纯翻译/文案包：只存 UI 字符串，不承载任何逻辑。缺陷的「修改点」绝不在
# 这里（改 locale 只会改文案，改不了行为）。工单 100001 里 4 个读取名额被
# locale 文件占了 2 个，而真正的渲染组件一个没进。
# ⚠ 目录段判定要按 `[-._/]` 分词：「demo-locale/admin/…」的 locale
# 段带连字符前缀，`/locale/` 这种纯斜杠边界匹配不到。
_LOCALE_RE = re.compile(
    r"(^|[/\-_.])(locales?|i18n|lang|messages|translations)([/\-_.]|$)", re.I)

# 文档/说明：与运行代码无关
_DOC_RE = re.compile(r"\.(md|txt|rst|adoc)$", re.I)

# 堆栈/报错里最常见的「框架内部路径」，命中它们不是缺陷点
_FRAMEWORK_PATH_RE = re.compile(
    r"(^|/)(node_modules|webpack|rollup|vite|@vue|vue-router|vuex|pinia"
    r"|react-dom|regenerator-runtime|tslib|core-js|whatwg-fetch|axios)(/|$)", re.I)

# 代码标识符特征：驼峰（getFinalContent/hasChoiceCard）或下划线（handle_sse_event）。
# 这类 token 出现在工单描述里时几乎总是从堆栈/调用链抄来的真实符号，
# grep 命中即修改点，精度远高于泛词（warn/task/JSON）。
_CAMEL_RE = re.compile(r"[a-z][A-Z]")
_SNAKE_RE = re.compile(r"[a-z0-9]_[a-z0-9]")
# 引号/书名号里的字面量：工单常把报错文案、日志串原样抄进来（「Failed to parse JSON string」）
_QUOTED_RE = re.compile(r"「([^「」]{6,80})」|\"([^\"\n]{6,80})\"|'([^'\n]{6,80})'")


def _seg_hit(low_path: str, seg: str) -> bool:
    """按词边界判断路径段命中，避免 'ai' 命中 'main'、'detail' 这类假阳性。"""
    s = seg.lower()
    if not s.isascii():
        return s in low_path
    return re.search(rf"(?<![a-z0-9]){re.escape(s)}(?![a-z0-9])", low_path) is not None


def _hint_hit(basename: str) -> bool:
    """表单/校验宿主文件名特征，按 -._ 分段精确匹配：
    "form-detail" 命中 form，"ai-platform" 不会误中 form。"""
    parts = re.split(r"[-._]", (basename or "").lower())
    return any(w in parts for w in _FORM_HOST_HINTS)


def _is_noise_segment(seg: str) -> bool:
    """路径段是否是工单模板噪音（分支名/账号/演示域名）。

    ⚠ 必须按 -._ 分段后比对**整段相等**，绝不能用子串：`Acme` 含
    'a','h','i' 三连子串，一子串匹配就命中几乎整个仓库（本案 tier-0 目录
    被它带偏的直接原因）。分段后 `ai-platform` 会拆成 ['ai','platform']，
    platform 不是噪音词，安全保留。"""
    s = (seg or "").strip().lower()
    if not s:
        return True
    if s in _NOISE_TOKENS:
        return True
    return any(p in _NOISE_TOKENS for p in re.split(r"[-._]", s) if p)


# 承载表单/校验规则的文件名特征：缺陷若是"应必填/未校验"，改动点几乎都在这里
_FORM_HOST_HINTS = ("editor", "form", "edit", "create", "modal", "dialog",
                    "setting", "config", "validate", "panel")

# 纯路由壳子/入口，通常只做拼装，不写校验
_CONTAINER_FILES = {"index.vue", "index.ts", "index.tsx", "app.vue", "main.ts"}


def _dedupe(seq) -> List[str]:
    """保序去重。重复项曾把 hard[:22] 的名额吃光（本案 startTime 在 idents 里
    出现 4 次、JSON 残段反复进 quoted），导致 path_tokens/segments 永远进不了
    grep 关键词表——必须先去重再截断。"""
    out, seen = [], set()
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _clean_path_tokens(tokens: List[str]) -> List[str]:
    """路径提示 token 清洗：只留「像真实相对路径」的候选。

    描述里的账号（demo/Acme@123456）、URL 残段（cn/demo-workspace/schedule）、
    中文短语（观察日期/时间控件的可编辑性）都会被正则捞进来；脏 token 曾把
    segments[-1] 污染成 create，让真正的目标目录失去 tier-0 资格。
    规则：去首尾斜杠、去重保序、至少两个段、至少一个 ≥4 字符段。"""
    out: List[str] = []
    seen = set()
    for tok in tokens:
        tok = (tok or "").strip("/").strip()
        if len(tok) < 5 or "/" not in tok:
            continue
        segs = [s for s in tok.split("/") if s]
        if len(segs) < 2 or all(len(s) < 4 for s in segs):
            continue
        if tok.lower() in seen:
            continue
        seen.add(tok.lower())
        out.append(tok)
    return out


def _is_noise_path(p: str) -> bool:
    """路径候选是否被工单模板噪音污染（分支名/账号段）。"""
    p = (p or "").lstrip("./")
    segs = [s for s in re.split(r"[/\\]", p) if s and s not in (".", "..")]
    if not segs:
        return True
    return any(_is_noise_segment(s) for s in segs)


def _route_to_dir_tokens(text: str) -> List[str]:
    """把前端路由串反解成疑似目录名，供 tier-0 目录判定使用。

    工单里的「页面定位」常写成前端路由（`admin/#/demo-skill-log`）。
    真实源码目录却是 kebab 化的组件路径（`demo/widgets/skill-log/`），
    直接 grep 路由串必然 0 命中。这里把路由按 kebab 拆词后再回拼相邻词对，
    生成 `demo-skill` / `skill-log` 等候选，
    让候选路径的「目录段」有机会撞上真实目录名。

    ⚠ 只在 `#/`（前端 hash 路由）或裸 kebab 路由段的语境下触发，避免把
    普通英文词组误当路由。

    ⚠⚠ 输出**必须包含完整路由段本身**（如 demo-skill-log），不能只有
    相邻词对：真实目录常是路由段的后缀（skill-log ⊆ demo-skill-log），
    尾缀匹配靠它。注意：返回值不含斜杠——调用方绝不能再把它丢进要求含 '/'
    的清洗函数（曾因过 _clean_path_tokens 的斜杠检查导致 route_tokens 全
    军覆没，admin/#/demo-skill-log 工单因此定位到无关模块）。"""
    out: List[str] = []
    # hash 路由：admin/#/demo-skill-log  或  /#/demo-skill-log
    for m in re.finditer(r"#/([\w\-/]{3,80})", text):
        route = m.group(1).strip("/").split("?")[0]
        for seg in route.split("/"):
            words = [w for w in seg.split("-") if w]
            if len(words) < 2:
                continue
            # 完整段本身（demo-skill-log）——尾缀匹配的真目录钥匙
            full = "-".join(words)
            if len(full) >= 8 and full not in out:
                out.append(full)
            # 相邻词两两组合（demo-skill / skill-log）
            for i in range(len(words) - 1):
                cand = f"{words[i]}-{words[i + 1]}"
                if len(cand) >= 8 and cand not in out:
                    out.append(cand)
    return out[:5]


def extract_stack_paths(text: str, limit: int = 4) -> List[str]:
    """从堆栈/报错文本里抽「文件路径:行号」——最高精度的修改点证据。

    报错堆栈里的路径（`at packages/a/b/c.vue:405:12`）就是出错文件本身，
    比任何关键词打分都准。放在 `_extract_keywords` 之外单独做，是因为它
    需要「路径:行号」这种跨字段结构，会被普通的 token 正则切碎。

    会自动剔除 node_modules / 框架内部路径，并按出现顺序保序去重。"""
    out: List[str] = []
    for m in re.finditer(r"([\w./@-]+\.(?:vue|ts|tsx|js|jsx|less|scss|css))[:：](\d{1,6})",
                         text, re.A):
        fp = m.group(1).lstrip("./")
        if _FRAMEWORK_PATH_RE.search(fp) or _THIRD_PARTY_RE.search(fp):
            continue
        if 1 <= int(m.group(2)) <= 99999 and fp not in out:
            out.append(fp)
    return out[:limit]


def load_precedent_files(proposals_dir: str, limit: int = 40) -> List[str]:
    """历史已采纳提案改过的文件 = 该项目最可信的修改点先例。

    只认 status=applied（真正写进仓库且经人工确认）的提案；按 created 倒序取
    最近 limit 条，返回去重后的相对路径列表。同一模块反复出缺陷时
    （用户反馈「输入其他工单还是会出现这个问题」），先例文件几乎总是修改点。
    任何异常都静默返回空列表——先例只是加权信号，绝不能炸掉定位流程。"""
    try:
        root = Path(proposals_dir)
        if not root.is_dir():
            return []
        items: List[Tuple[str, List[str]]] = []
        for f in root.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("status") != "applied":
                continue
            files: List[str] = []
            for b in ((data.get("analysis") or {}).get("patch_blocks") or []):
                fp = str((b or {}).get("file_path") or "").replace("\\", "/")
                if fp and fp not in files:
                    files.append(fp)
            for b in ((data.get("patch") or {}).get("blocks") or []):
                fp = str((b or {}).get("file_path") or "").replace("\\", "/")
                if fp and fp not in files:
                    files.append(fp)
            if files:
                items.append((str(data.get("created") or ""), files))
        items.sort(key=lambda x: x[0], reverse=True)
        out: List[str] = []
        for _, files in items[:limit]:
            for fp in files:
                if fp not in out:
                    out.append(fp)
        return out
    except Exception:
        return []


def strip_html(text: str) -> str:
    """ONES 富文本描述 → 纯文本（保留 URL 路径部分，去掉标签与实体）。

    换行语义必须保住：富文本的段落/列表结构只体现在标签上，工单里经常一个
    裸换行都没有。一律 `sub(" ")` 会把整篇描述压成一行，知识库卡片的「现象」
    就会挤成一团，同时关键词抽取也会把相邻两句粘在一起造出假关键词。
    """
    text = text or ""
    text = _SCRIPT_RE.sub(" ", text)
    text = _URL_RE.sub(lambda m: m.group(0).split("/", 3)[-1] if m.group(0).count("/") > 2
                       else " ", text)  # 去掉协议+域名，保留 /path 部分
    # 块级边界 → 换行，行内标签 → 空格（先块级后行内，避免 <p> 被当空格吃掉）
    text = _BR_RE.sub("\n", text)
    text = _HR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _BLOCK_START_RE.sub("\n", text)
    text = _CELL_END_RE.sub("  ", text)   # 同行的表格单元格用空格分隔
    text = _TAG_RE.sub(" ", text)
    for ent, ch in _ENTITIES:
        text = text.replace(ent, ch)
    text = re.sub(r"[ \t]+\n", "\n", text)      # 去掉行尾空白（含 &nbsp; 变来的）
    text = re.sub(r" *\n *", "\n", text)        # 统一行首缩进
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# 判断「模型认为根因不在前端仓库」的信号词。
# 分两档计分：显式结论（模型直接说清非前端/后端）权重 2，间接线索（提到后端、
# 服务端、接口层）权重 1。总分 ≥2 才判定——单提一句「接口」不算，避免把
# 「窗口不足但改动点其实在前端」的失败误标成后端缺陷。
_NON_FRONTEND_STRONG = (
    "非前端缺陷", "不修改任何前端文件", "不需要修改前端", "前端无需修改",
    "根因在后端", "问题在后端", "后端缺陷", "需改后端", "后端修复",
    "不属于前端", "超出前端范围",
)
_NON_FRONTEND_WEAK = ("后端", "服务端", "接口层", "后端接口", "服务端接口",
                      "数据库", "SQL", "存储", "未命中")
_API_PATH_RE = re.compile(r"/(?:api|rest|v\d)/(?:[\w.-]+/)*[\w.-]+", re.I)


def detect_non_frontend(analysis: Dict, block_count: int = 0) -> bool:
    """从模型的结论文本判断根因是否在前端仓库之外（后端/接口/数据）。

    为什么需要服务端兜底：prompt 里让模型「非前端缺陷时以该前缀开头」只是
    软约束，真实回答常写成「问题落在 /api/api/ai/agent/logs/query 对
    logType=SKILL 的条件匹配与字段存储上，不修改任何前端文件」——措辞自由，
    category 还可能是「其他」。只看前缀/枚举会让这类正确答案仍显示成红色
    「未生成可应用补丁」（工单 100002 的实际情况）。

    有补丁时一律返回 False：能改前端就不算非前端缺陷。"""
    if block_count:
        return False
    text = " ".join(str((analysis or {}).get(k) or "")
                    for k in ("root_cause", "explanation", "prevention"))
    if not text.strip():
        return False
    score = sum(2 for s in _NON_FRONTEND_STRONG if s in text)
    score += sum(1 for s in _NON_FRONTEND_WEAK if s in text)
    # 「接口」出现且带具体 API 路径 = 明确的接口层归因
    if _API_PATH_RE.search(text) and ("接口" in text or "请求" in text):
        score += 1
    return score >= 2


class DefectAnalyzer:
    def __init__(
        self,
        repo_path: str,
        ai_model,
        max_files: int = 4,
        file_cap_bytes: int = 12000,
        context_lines: int = 80,
        precedent_dir: str = "",
    ):
        self.repo_path = Path(repo_path).resolve()
        self.ai = ai_model
        self.max_files = max_files
        self.file_cap_bytes = file_cap_bytes
        self.context_lines = context_lines
        # 历史已采纳提案目录：analyze() 每次现读，保证服务长驻时先例状态新鲜
        self.precedent_dir = precedent_dir or ""
        self._precedent_files: List[str] = []
        self._repo_total = 0

    # ------------------------------------------------------------------ public
    def analyze(self, defect: Dict, on_delta=None) -> Dict:
        title = defect.get("title", "")
        desc = strip_html(defect.get("description", "") or defect.get("desc", ""))
        text = f"{title}\n{desc}"

        # 堆栈/报错里点名的文件 = 出错文件本身，比任何打分都准。
        # 直接进"必读"名单，不参与排序竞争。
        self._stack_files = extract_stack_paths(text)

        # 历史先例：本项目近期人工采纳过的补丁文件，是同一模块再次出缺陷时
        # 最可信的修改点信号。每次 analyze 现读（服务长驻时提案状态会变化）。
        if self.precedent_dir:
            self._precedent_files = load_precedent_files(self.precedent_dir)

        keywords, path_tokens = self._extract_keywords(text)
        # 超高频关键词（分支名/账号/技术栈）几乎命中整个仓库，只会把噪音文件
        # 抬进候选池，真正的小众标识符（startTime）反被挤掉。按「命中文件数」
        # 做一次频次闸门：命中 >8% 仓库文件的关键词直接出局。
        keywords, dropped = self._drop_high_freq_keywords(keywords)
        self._freq_dropped = dropped
        # "应必填/未校验"类缺陷的改动点在表单组件，把这个语境传给排序阶段
        self._wants_validation = any(
            t in text.lower() for t in ("必填", "校验", "required", "validate", "validator"))
        suspect_files = self._scan_repo(keywords, path_tokens)
        # 堆栈点名的文件提到最前：这是唯一"不依赖打分、直接读"的信号，
        # 因为堆栈行号就是出错现场（`at packages/a/b/c.vue:405`）。
        stack = [f for f in getattr(self, "_stack_files", []) if f not in suspect_files]
        if stack:
            suspect_files = stack + suspect_files
        snippets, meta = self._read_contexts(suspect_files, keywords)

        prompt = self._build_prompt(defect, snippets, meta)
        # reasoning 模型（deepseek-flash 等）的隐藏推理 token 与正式回答共享
        # max_tokens 配额；给少了会出现 content 字段直接缺失（finish_reason=length）。
        # 8000 仍被观察到偶发吃光，故留到 16000 的余量。
        with usage.step("locate"):
            ai_result = self.ai.complete(prompt, max_tokens=16000, on_delta=on_delta)

        raw_patch = ai_result.get("patch_blocks", "") or ai_result.get("fix_suggestion", "")
        blocks, parse_errors = parse_blocks(raw_patch)

        # ── 二次定位重试（最多一次）──────────────────────────────────
        # 模型按「宁缺勿猜」护栏拒出补丁时，会在 root_cause/explanation 里点名
        # 需要看的目录/文件。分两路补窗口：① 点名的**具体文件**直接强制开窗
        # （不依赖得分排序——真改动点可能排在读取名额之外，本案
        # repeat-form.vue 排第 5）；② 目录提示走重扫投票。
        retry_note = ""
        if not blocks and not ai_result.get("error"):
            hints = self._model_hints(ai_result, path_tokens)
            exts = {"vue", "ts", "tsx", "js", "jsx"}
            file_hints = [h for h in hints
                          if h.rsplit(".", 1)[-1].lower() in exts]
            dir_hints = [h for h in hints if h not in file_hints]
            snippets2, meta2 = dict(snippets), list(meta)
            forced: List[str] = []
            for fp in file_hints[:2]:
                resolved = self._resolve_file_hint(fp)
                if not resolved or resolved in snippets2 \
                        or len(snippets2) >= self.max_files + 2:
                    continue
                t, note = self._read_one(resolved, keywords)
                if t is not None:
                    snippets2[resolved] = t
                    if note:
                        meta2.append(note)
                    forced.append(resolved)
            if dir_hints:
                merged = (dir_hints + list(path_tokens))[:8]
                kws2, _ = self._extract_keywords(text + "\n" + " ".join(dir_hints))
                suspect2 = self._scan_repo(kws2, merged)
                extra, extra_meta = self._read_contexts(suspect2, kws2)
                for k, v in extra.items():
                    if k not in snippets2 and len(snippets2) < self.max_files + 2:
                        snippets2[k] = v
                meta2 += [m for m in extra_meta if m not in meta2]
            fresh = [f for f in snippets2 if f not in snippets]
            if fresh:
                # 模型点名的新文件提到 prompt 最前：模型正是为了看它们才
                # 要求重试，排在四个旧窗口末尾会被长上下文稀释注意力
                snippets2 = {**{k: snippets2[k] for k in fresh},
                             **{k: v for k, v in snippets2.items()
                                if k not in fresh}}
                note = (f"第一轮窗口不足，已按模型提示补充定位"
                        f"（点名文件 {', '.join(forced) or '-'}"
                        f"{'；目录 ' + ', '.join(dir_hints) if dir_hints else ''}），"
                        f"新增窗口：{', '.join(fresh[:3])}")
                prompt2 = self._build_prompt(
                    defect, snippets2, meta2 + [f"（{note}）"])
                with usage.step("locate_retry"):
                    ai2 = self.ai.complete(prompt2, max_tokens=16000,
                                          on_delta=on_delta)
                raw2 = ai2.get("patch_blocks", "") or ai2.get("fix_suggestion", "")
                blocks2, errs2 = parse_blocks(raw2)
                if blocks2 and not ai2.get("error"):
                    ai_result, raw_patch = ai2, raw2
                    blocks, parse_errors = blocks2, errs2
                    suspect_files = suspect_files + [f for f in fresh
                                                     if f not in suspect_files]
                    snippets, meta = snippets2, meta2
                    retry_note = note + "；重试成功"
                else:
                    retry_note = note + "；重试仍未获得补丁"
                    parse_errors = parse_errors + [retry_note]
                    # 第二轮窗口更全（含模型点名强制开窗的文件），即使仍无补丁，
                    # 其根因结论也比第一轮新鲜。不采纳会让 UI 停留在第一轮的
                    # 过时判断（曾一直显示「common.ts 本次未提供」，而第二轮
                    # 实际已看过该文件并改判「根因在后端」），误导排查方向。
                    for k in ("root_cause", "explanation", "prevention",
                              "category"):
                        v2 = str(ai2.get(k) or "").strip()
                        if v2:
                            ai_result[k] = v2
            # fresh 为空：模型提示的窗口与第一轮重合，重试无意义，保持原结果

        # 定位为空是"护栏触发"，必须与"补丁生成失败"区分开，
        # 否则用户看到的现象都是"无法生成补丁"，无法判断该修什么。
        non_frontend = detect_non_frontend(ai_result, len(blocks))
        locate_empty = not snippets
        if locate_empty and not parse_errors:
            pass
        elif locate_empty:
            parse_errors = ["定位阶段没有找到嫌疑文件（护栏触发，未让模型盲猜）"] + parse_errors
        elif not raw_patch and not ai_result.get("error"):
            # 模型正常回复了分析（root_cause 有内容）但没给 SEARCH/REPLACE 块，
            # 与"AI 调用失败/超时"是两回事，分开提示。
            parse_errors = [
                ("模型判断根因不在前端仓库（证据见根因），未生成前端补丁——"
                 "建议转后端/接口排查，或在工单补充后端信息后重跑"
                 if non_frontend else
                 "模型返回了分析但未给出 SEARCH/REPLACE 补丁块"
                 "（可能是嫌疑窗口不足以确定改法，需补充上下文）")
            ] + parse_errors

        return {
            "defect_id": defect.get("id") or defect.get("issueUUID"),
            "title": title,
            "keywords": keywords,
            "suspect_files": suspect_files,
            "read_files": list(snippets),
            "locate_empty": not snippets,
            "locate_retry": retry_note,
            "truncation": meta,
            "root_cause": ai_result.get("root_cause", ""),
            "category": ai_result.get("category", "其他"),
            # 根因不在前端仓库（后端/接口/数据）→ 前端确实无补丁可出，
            # 与「没定位到、模型拒猜」是两种结论，UI 必须分开提示
            "non_frontend": non_frontend,
            "prevention": ai_result.get("prevention", ""),
            "explanation": ai_result.get("explanation", ""),
            "patch_text": raw_patch,
            "patch_blocks": [
                {
                    "file_path": b.file_path,
                    "search": b.search,
                    "replace": b.replace,
                    "match_mode": b.match_mode,
                    "match_start": b.match_start,
                }
                for b in blocks
            ],
            "patch_canonical": render_block_prompt(blocks),
            "parse_errors": parse_errors,
            "block_count": len(blocks),
            "ai_mode": getattr(self.ai, "mode", "unknown"),
            "ai_error": ai_result.get("error", ""),
        }

    # ------------------------------------------------------------- keywords
    def _model_hints(self, ai_result: Dict, known_tokens: List[str]) -> List[str]:
        """从模型「窗口不足」的回答里抽取路径提示。

        模型拒绝出补丁时常直接点名目标（如 /demo-workspace/schedule 创建弹窗、
        packages/.../task-schedule 之类的 mixin），这些就是二次定位的导航。"""
        text = " ".join(str(ai_result.get(k) or "")
                        for k in ("root_cause", "explanation", "prevention"))
        raw = re.findall(r"\b[\w-]+(?:/[\w-]+)+\b", text, re.A)
        raw += re.findall(r"[\w./-]+\.(?:ts|tsx|js|jsx|vue)", text)
        # 裸文件名（repeat-form.vue，无斜杠）不能过 _clean_path_tokens
        # 的「必须含 /」检查——单独放行，交给 _resolve_file_hint 解析
        raw_files = [t for t in raw if "/" not in t
                     and re.search(r"\.(?:ts|tsx|js|jsx|vue)$", t, re.I)]
        raw = [t for t in raw if "/" in t]
        # 模型常直接点名页面路由（admin/#/demo-skill-log），`#` 会把
        # 路径正则切断——必须走路由反解，否则二次定位拿不到目录提示
        routes = _route_to_dir_tokens(text)
        known = {t.lower() for t in known_tokens}
        out: List[str] = []
        for tok in _clean_path_tokens(raw) + _dedupe(raw_files) + routes:
            if tok.lower() in known:
                continue
            if tok not in out:
                out.append(tok)
        # 放宽到 8：模型回答里常混有 time/date、repeatFromPlan/xxx 这类
        # 顺带提及，截太紧会把真正点名的文件路径/路由挤掉
        return out[:8]

    def _extract_keywords(self, text: str) -> Tuple[List[str], List[str]]:
        """(keywords for git grep, path_tokens for ranking boost)."""
        # ── 堆栈路径：最高精度信号，单独抽（需要「路径:行号」结构）──
        stack_files = extract_stack_paths(text)
        # 截图/附件图注（d1-time-list.png）会污染语料，先摘掉
        text = _IMG_NAME_RE.sub(" ", text)
        # 工单模板里的「账号/密码」「邮箱」形态串（user/Acme@123456、
        # someone@example.com）：@ 连接的两段及拆词全部按噪音处理。
        # 这是结构性规则，不依赖任何具体账号名——任何团队的工单模板都适用。
        runtime_noise: set = set()
        for m in re.finditer(r"\b[\w.-]+(?:/[\w.-]+)*@[\w.-]+", text, re.A):
            for part in m.group(0).split("@"):
                runtime_noise.update(
                    p.lower() for p in re.split(r"[./_-]", part) if len(p) >= 2
                )
        paths = re.findall(r"[\w./-]+\.(?:ts|tsx|js|jsx|vue|less|scss|css)", text)
        # 已收录的堆栈文件从普通 paths 里去掉，避免重复占位
        paths = [p for p in paths if p.lstrip("./") not in stack_files]
        paths = [p for p in paths if not _is_noise_path(p)]
        # 描述里出现的路径提示（如 pages/schedule/edit）——最强的文件定位信号。
        # re.A（ASCII）必须加：Unicode 模式下 \w 会把「观察日期/时间控件的可编辑性」
        # 这类中文短语也当成路径 token，6 个名额被垃圾占满后目录判定全盘失准。
        raw_tokens = re.findall(r"\b[\w-]+(?:/[\w-]+)+\b", text, re.A)
        # 前端路由（admin/#/demo-skill-log）也是路径提示：把它反解成
        # 「疑似目录名」才能让 tier-0 命中 ai-platform/components/runtime-log/。
        route_tokens = _route_to_dir_tokens(text)
        # ⚠⚠ route_tokens 绝不能再过 _clean_path_tokens：它们不含斜杠，
        # 会被「必须含 /」检查全部丢弃（2026-09-23 事故：admin/#/demo-skill-log
        # 工单的目录提示全灭，定位漂移到 schedule）。路由候选单独直通。
        path_tokens = (_clean_path_tokens(
            [p for p in raw_tokens
             if not p.endswith((".png", ".jpg", ".gif"))
             and not _is_noise_path(p)])
            + route_tokens)[:8]
        members = re.findall(r"([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]+)", text)
        symbols = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]{3,}", text)
        # 中文词：UI 字符串/注释大多是中文，"重复周期"这类词 grep 命中率极高。
        # 连续长句要滑窗切词（"重复周期应该必填"整句 grep 会命中 0）。
        zh_raw = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        zh_words: List[str] = []
        for chunk in zh_raw:
            if chunk not in _ZH_STOPWORDS and 2 <= len(chunk) <= 8:
                zh_words.append(chunk)
            if len(chunk) > 3:
                for i in range(len(chunk) - 1):
                    gram = chunk[i:i + 2]
                    if gram not in _ZH_STOPWORDS:
                        zh_words.append(gram)
        seen_zh = set()
        zh_words = [w for w in zh_words if not (w in seen_zh or seen_zh.add(w))]
        line_refs = re.findall(r"line\s*:?\s*(\d{1,5})", text, re.I)
        errors = re.findall(
            r"(?:TypeError|ReferenceError|Cannot read|is not a function|undefined|null)[^\n。]{0,60}",
            text,
        )
        member_kws = [f"{a}.{b}" for a, b in members] + [b for _, b in members]

        # 代码标识符（驼峰/下划线）与引号字面量 = 高精度关键词类。
        # 它们多从堆栈/调用链/报错文案抄写而来，命中哪个文件哪个文件就是修改点。
        # 必须排在最前，否则泛词（Agent/JSON/warn/task）会挤占 grep 窗口——
        # 本案里 getFinalContent/hasChoiceCard 全被挤出，定位直接落空。
        idents: List[str] = []
        quoted: List[str] = []
        for tok in symbols:
            if len(tok) >= 6 and (_CAMEL_RE.search(tok) or _SNAKE_RE.search(tok)):
                idents.append(tok)
        for m in _QUOTED_RE.findall(text):
            lit = next(g for g in m if g)
            if any(c in lit for c in "{}"):
                continue  # JSON 残段（如 :{..., ）是纯噪音，还占 hard 名额
            if "→" in lit or lit.count(" ") >= 2:
                continue  # 导航面包屑/模板栏目（AI平台 → AI技能）grep 必然 0 命中
            if not lit.isascii() and len(lit) > 12:
                continue  # 中文长句字面量 grep 必然 0 命中（zh bigram 已覆盖）
            if lit not in quoted:
                quoted.append(lit)
        # 只有 ASCII 字面量才算高精度（报错文案/日志串）；中文引号内容多为
        # 运行时 UI 字符串（如「收到，到点该喝水啦」），grep 只会命中 locale
        # 文件，×8 加权反而把真正的修改点挤出读取名额——只作普通关键词。
        self._ident_kws = set(idents) | {q for q in quoted if q.isascii()}

        # path_tokens 拆段也能当 grep 词（demo-workspace、schedule）
        segments: List[str] = []
        for tok in path_tokens:
            segments.extend(s for s in tok.split("/") if len(s) >= 4 and "-" not in s[:1])

        kws: List[str] = []
        seen = set()
        # 高信号 token 顺序：文件路径 → 代码标识符 → 路径提示 → 引号字面量 →
        # 报错串 → 成员访问 → 路径段 → 其余符号 → 中文二字词。
        # 标识符/字面量必须排在泛词符号（Agent/JSON/Console…）之前；
        # 泛词符号放最后，只做兜底填充。
        # 每个分量必须先去重再截断：曾因 idents 里 timePlan×4、quoted 里 JSON
        # 残段重复，前 22 槽被吃满，path_tokens/segments 从未进入关键词表。
        # path_tokens 必须排在 quoted 之前：日期值/长句字面量 grep 几乎必然
        # 0 命中，不该压过「最强文件定位信号」。
        hard = (_dedupe(paths) + _dedupe(idents) + _dedupe(path_tokens)
                + _dedupe(quoted) + _dedupe(errors) + _dedupe(member_kws)
                + _dedupe(segments) + _dedupe(symbols))[:22]
        # ── 中文高精度串（引号里的 UI 文案）─────────────
        # `_scan_repo` 只 grep 前 20 个关键词，所以"进不了前 20"等于不存在。
        # 事故：工单 100001 里 `触发时间` 仅命中 13 个文件、其中 4 个正是
        # runtime-log 的渲染组件（精度极高），但中文二字词 bigram 全排在
        # 它前面（`比实`/`际时`/`间早` = 纯噪音，命中 0），把第 16-21 槽吃光，
        # 真正有用的 UI 串从未参与 grep —— 定位因此整体失准。
        # 修正：中文大串（4-8 字，非 bigram）排在 bigram 之前。
        zh_long = [w for w in zh_words if len(w) >= 4]
        zh_short = [w for w in zh_words if len(w) < 4]
        ordered = hard + zh_long[:6] + zh_short[:12]
        for kw in ordered:
            kw = (kw or "").strip()
            if len(kw) < 2 or len(kw) > 80 or kw in seen:
                continue
            if kw in {"true", "false", "null", "undefined", "http", "https", "line"}:
                continue
            # 符号通道漏进来的工单样板噪音（测试账号/分支名）：admin、develop、
            # demo… 进 grep 要么 0 命中要么污染候选池，白占关键词槽位；
            # runtime_noise 是按 @ 形态从本条工单动态收集的账号/域名拆词
            if kw.lower() in _NOISE_TOKENS or kw.lower() in runtime_noise:
                continue
            # URL/Trace 残留的 16+ 位十六进制串（e7bb17bc1747a7…）不可能命中代码
            if len(kw) >= 16 and re.fullmatch(r"[0-9a-f]+", kw.lower()):
                continue
            seen.add(kw)
            kws.append(kw)
        self._line_refs = [int(n) for n in line_refs]
        return kws[:30], path_tokens

    # ------------------------------------------------------------ scanning
    def _drop_high_freq_keywords(self, keywords: List[str],
                                 ratio: float = 0.04, floor: int = 25) -> Tuple[List[str], List[str]]:
        """按「命中文件数 / 仓库文件总数」剔除超高频关键词。

        事故（2026-09-23，工单 100001）：`develop`（工单【测试分支】栏的值）
        命中 91 个文件、`skill` 命中 323 个、`admin` 命中 882 个、`runtime`
        命中 710 个——这些词把大量与缺陷无关的文件抬进候选池，而真正区分度
        极高的 `startTime` 反而因为「命中文件太多」被排序压下去，最终读取
        名额全给了定时任务/locale 代码，AI 只能拒出补丁。

        闸门规则：命中文件数 > max(floor, ratio × 仓库文件数) → 出局。
        带 floor 是为了小仓库不被误伤（几十个文件的小项目里 4% 只是 2 个文件）。
        高精度标识符（_ident_kws）豁免——startTime 这类词即使命中上百个文件，
        仍然是缺陷点名要求定位的字段，不能因为「常见」就丢掉。
        """
        if not keywords:
            return keywords, []
        total = self._repo_file_count()
        if total <= 0:
            return keywords, []
        budget = max(floor, int(total * ratio))
        ident_kws = getattr(self, "_ident_kws", set()) or set()
        kept: List[str] = []
        dropped: List[str] = []
        for kw in keywords:
            if kw in ident_kws or "/" in kw:
                kept.append(kw)
                continue
            if self._grep_file_count(kw) > budget:
                dropped.append(kw)
                continue
            kept.append(kw)
        return kept, dropped

    def _grep_file_count(self, kw: str) -> int:
        """git grep -l 的文件数（带超时保护，失败返回 0 表示不拦截）。"""
        try:
            result = subprocess.run(
                ["git", "grep", "-l", "-i", "--untracked", "--", kw],
                cwd=str(self.repo_path), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return 0
        if result.returncode != 0 or not result.stdout:
            return 0
        return len([ln for ln in result.stdout.splitlines() if ln.strip()])

    def _repo_file_count(self) -> int:
        """仓库受版本管理的文件总数（git ls-files），带缓存。"""
        if getattr(self, "_repo_total", None):
            return self._repo_total
        try:
            result = subprocess.run(
                ["git", "ls-files"], cwd=str(self.repo_path),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30)
            self._repo_total = len([ln for ln in (result.stdout or "").splitlines()
                                    if ln.strip()])
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self._repo_total = 0
        return self._repo_total or 0

    def _ls_files(self) -> List[str]:
        """git ls-files 缓存（供裸文件名提示解析）。失败返回空列表。"""
        cached = getattr(self, "_ls_files_cache", None)
        if cached is not None:
            return cached
        try:
            result = subprocess.run(
                # --exclude-standard 必须加：否则 --others 会把 gitignore 掉的
                # node_modules/dist 旧拷贝也捞进来，裸文件名解析到过期副本
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=str(self.repo_path), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30)
            self._ls_files_cache = [ln.strip().replace("\\", "/")
                                    for ln in (result.stdout or "").splitlines()
                                    if ln.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self._ls_files_cache = []
        return self._ls_files_cache

    def _resolve_file_hint(self, hint: str) -> str:
        """把模型点名的文件提示解析成仓库内真实相对路径。

        模型常只给裸文件名（repeat-form.vue）或带 ./ 前缀的路径；
        先按原样认，再按「路径以 /{hint} 结尾」在 ls-files 里匹配——
        解析失败等于二次定位白做（强制开窗窗口根本打不开）。"""
        hint = (hint or "").lstrip("./").replace("\\", "/")
        if not hint:
            return ""
        if (self.repo_path / hint).is_file():
            return hint
        tail = "/" + hint.lower()
        for f in self._ls_files():
            if f.lower().endswith(tail):
                return f
        return ""

    def _scan_repo(self, keywords: List[str], path_tokens: List[str] = None) -> List[str]:
        path_tokens = path_tokens or []
        # kebab 复合段（路由反解产物，无斜杠）也要拆出子词：runtime-log 的
        # runtime 子词才能通过 _seg_hit 的词边界匹配命中 runtime-log/ 目录
        segments = _dedupe(
            [s for tok in path_tokens for s in tok.split("/") if len(s) >= 4]
            + [w for tok in path_tokens for s in tok.split("/")
               for w in re.split(r"[-_]", s) if len(w) >= 4])
        ident_kws = getattr(self, "_ident_kws", set()) or set()
        hits: Dict[str, float] = {}
        for kw in keywords[:20]:
            if len(kw) < 2 or kw.startswith("-"):
                continue
            try:
                result = subprocess.run(
                    ["git", "grep", "-nI", "-i", "--untracked", "--", kw],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
            if result.returncode != 0 or not result.stdout:
                continue
            # 计「该关键词命中了哪些文件」（去重），不按命中行数累加：
            # 否则一个含 200 行 task/warn 的大文件会碾压只命中 1 次
            # getFinalContent 的真正修改点。
            files_hit = {line.split(":", 1)[0] for line in result.stdout.splitlines()
                         if line.split(":", 1)[0]}
            # 标识符/字面量是高精度信号：命中一次就强于多个泛词命中
            weight = 8.0 if kw in ident_kws else 1.0
            for rel in files_hit:
                hits[rel] = hits.get(rel, 0.0) + weight
        # 排除第三方/构建产物——它们体积巨大且永远不是修复目标
        hits = {f: n for f, n in hits.items() if not _THIRD_PARTY_RE.search(f)}

        # 路径提示指名的目录：描述里写了 pages/schedule/edit，
        # 那么该目录下的文件必须优先于任何"碰巧命中更多关键词"的宽泛文件。
        # 英文单词按词边界匹配，避免 "ai" 命中 "main"/"detail" 这类假阳性。
        # ⚠ 必须排除 _GENERIC_SEGMENTS 与工单模板噪音：`acme`（来自测试账号
        # demo/Acme@123456）含高频短词，一旦进 strong_dirs
        # 就会让几乎全仓库的文件白拿目录加权，tier-0 彻底失效。
        strong_dirs = [s for s in segments
                       if s.lower() not in _GENERIC_SEGMENTS
                       and not _is_noise_segment(s)]
        # 段投票制：出现在多个 path_token 里的段 = 描述反复指名的目录。
        # 旧逻辑取 segments[-1]（最后一个 token 的末段），一个 URL 残段
        # （…/ai/schedule/create → create）就能让真正的目标目录
        # （schedule 被提及 3 次）失去 tier-0 资格——本案定位全错的元凶之一。
        seg_votes: Dict[str, int] = {}
        for tok in path_tokens:
            for s in tok.split("/"):
                # kebab 段拆子词计票：demo-skill-log 的 runtime/skill
                # 各得一票，投票才能聚到「描述反复指名」的模块目录上
                for part in re.split(r"[-_]", s):
                    sl = part.lower()
                    if (len(part) >= 4 and sl not in _GENERIC_SEGMENTS
                            and not _is_noise_segment(part)):
                        seg_votes[sl] = seg_votes.get(sl, 0) + 1
        target_dirs = self._target_dirs(path_tokens, seg_votes)

        def _in_target_dir(f: str) -> bool:
            low = f.lower()
            return any(d in low for d in target_dirs)

        precedent = set(getattr(self, "_precedent_files", []) or [])
        prec_dirs = {p.rsplit("/", 1)[0] for p in precedent if "/" in p}

        def _boost(f: str) -> int:
            low = f.lower()
            basename = low.rsplit("/", 1)[-1]
            score = sum(12 for tok in path_tokens if tok.lower() in low)
            # 段命中按票数加权：schedule 被描述提及 3 次时一次命中值 18 分，
            # 而 URL 里顺手带出的 runtime（1 票）只值 6 分
            score += sum(6 * seg_votes.get(seg.lower(), 1)
                         for seg in strong_dirs if _seg_hit(low, seg))
            # 历史先例：人工采纳过的文件 +30，同目录 +12。
            # 只对已有关键词命中的文件生效——不相关模块的先例不会凭空入选
            if f in precedent:
                score += 30
            elif f.rsplit("/", 1)[0] in prec_dirs:
                score += 12
            # "必填/校验"类缺陷真正的改动点在表单/编辑器组件。
            # ⚠ 只在 wants_validation 语境下生效，且按分隔符分段精确匹配：
            # 裸子串曾让 "plat-form"/vue."config" 白拿 +5，压过真正的修改点
            if wants_validation and _hint_hit(basename):
                score += 5
            # 纯容器/入口文件通常是路由壳子，几乎不承载校验逻辑
            if basename in _CONTAINER_FILES:
                score -= 2
            return score

        # 缺陷文本里出现"必填/校验/required"时，提升承载校验文件的优先级。
        # 判定词由 analyze() 从原始标题+描述提取后透传，避免这里重复解析。
        wants_validation = bool(getattr(self, "_wants_validation", False))

        def _val_rank(f: str) -> int:
            if not wants_validation:
                return 0
            return 0 if _hint_hit(f.lower().rsplit("/", 1)[-1]) else 1

        def _dead_end(f: str) -> int:
            """非逻辑文件（翻译文案包 / 文档）一律排在逻辑代码之后。

            ⚠ 不能直接删掉候选：万一工单就是改文案，文件仍须可被读取。
            但它绝不能占用有限的读取名额——工单 100001 里 4 个名额被
            `locale/admin/system.*.ts` 占了 2 个，真正的渲染组件一个没进，
            模型只能给出「嫌疑文件中没有运行日志代码」的结论并拒出补丁。"""
            return 1 if (_LOCALE_RE.search(f) or _DOC_RE.search(f)) else 0

        # 优先级依次为：逻辑代码（非文案/文档） → 路径提示目录/历史先例 →
        # 综合得分 → 校验语境（仅平局裁决） → 路径浅/名短。
        # ⚠ _val_rank 绝不能放在得分之前：曾作为第二排序位让全仓库所有
        # form-* 文件在"必填/校验"语境下碾压任何非 form 文件——本案
        # schedule/api.ts 拿到 4 个标识符 ×8 命中仍被几百个表单文件埋掉。
        ranked = sorted(
            hits,
            key=lambda f: (_dead_end(f),
                           0 if (_in_target_dir(f) or f in precedent) else 1,
                           -(hits[f] + _boost(f)), _val_rank(f),
                           f.count("/"), len(f)),
        )
        # 候选池取 12（max_files*3）：tier-0 生效时目标目录内命中文件很多，
        # 池太小会让高分文件挤不进来
        top = ranked[: max(self.max_files * 3, 12)]
        named_set = {f for f in top
                     if any(_seg_hit(f.lower().rsplit("/", 1)[-1], w)
                            for w in strong_dirs)}
        if named_set and len(named_set) < len(top):
            # 目录内语义命名的文件提到最前。⚠ 必须稳定重排（named 全部保留）：
            # 旧写法 named[:2] + 非 named 会把 named[2:] 整段丢掉——tier-0
            # 修好后 top 里几乎全是目标目录文件，named 膨胀，本案
            # schedule-form.vue/repeat-form.vue 就这样被挤出了窗口
            top = ([f for f in top if f in named_set]
                   + [f for f in top if f not in named_set])
        return top[: self.max_files * 3]

    @staticmethod
    def _target_dirs(path_tokens: List[str], seg_votes: Dict[str, int]) -> List[str]:
        """由路径提示 token 推导 tier-0 目录（pages/{seg}/ 与 /{seg}/）。

        投票多者胜（被提及 ≥2 次的段 = 描述反复指名的目录，平票取最具体的
        最多 2 个）；全部只被提及一次时退回「末段 + 次末段」——URL 末段常是
        动作名（create），模块目录常在次末位（schedule），两个都纳入宁可
        稍微稀释也不丢真目标。

        ⚠ `_route_to_dir_tokens` 生成的 kebab 复合段（runtime-log）**整体**
        参与 tier-0 匹配，不做再拆分——真实目录名就是 `runtime-log/`，
        拆成 runtime/log 反而会让 `/log/` 这种超宽前缀命中一堆无关目录。"""
        if not seg_votes:
            return []
        ordered = sorted(seg_votes.items(), key=lambda kv: (-kv[1], -len(kv[0])))
        top_vote = ordered[0][1]
        if top_vote >= 2:
            chosen = [s for s, v in ordered if v == top_vote][:2]
        else:
            # 「末段+次末段」只从**含斜杠**的 token 取：路由反解的 kebab token
            # （demo-workspace）没有位置语义，混进来会把真模块目录（memory）
            # 挤出兜底——2026-09-23 记忆工单回归的元凶
            cands = [s.lower() for tok in path_tokens if "/" in tok
                     for s in tok.split("/")
                     if len(s) >= 4 and s.lower() not in _GENERIC_SEGMENTS
                     and not _is_noise_segment(s)]
            chosen = list(dict.fromkeys(cands[-1:] + cands[-2:-1]))[:2]
        dirs: List[str] = []
        for s in chosen:
            # kebab 复合段（含 '-' 且 ≥8 字符）= 路由反解出来的真实目录名。
            # 必须同时给「尾缀形态」：真实目录 runtime-log/ 里，runtime-log
            # 前面是 '-' 而非 '/'，纯 /{s}/ 边界永远匹配不到它。
            if "-" in s and len(s) >= 8:
                dirs += [f"/{s}/", f"-{s}/"]
            else:
                dirs += [f"pages/{s}/", f"/{s}/"]
                if len(s) >= 6:
                    # task-schedule/ 这类「前缀-模块」复合目录同样常见
                    dirs.append(f"-{s}/")
        # kebab 复合 token 若由选中段组成（如 runtime-log ⊇ runtime），
        # 整体也升为 tier-0 目录：真实目录常是路由段的超集（runtime-log）
        for tok in path_tokens:
            if "/" in tok:
                continue
            tl = tok.lower()
            if len(tl) < 8 or "-" not in tl:
                continue
            subs = [w for w in tl.split("-") if len(w) >= 4]
            if any(w in chosen for w in subs):
                for d in (f"/{tl}/", f"-{tl}/"):
                    if d not in dirs:
                        dirs.append(d)
        return dirs

    def _read_contexts(
        self, files: List[str], keywords: List[str]
    ) -> Tuple[Dict[str, str], List[str]]:
        """Read suspect files until max_files snippets are collected.

        候选列表是排过序的；前几个若因超大/不可读被跳过，会继续尝试后面的候选，
        而不是直接让 prompt 里一个文件都没有。"""
        snippets: Dict[str, str] = {}
        meta: List[str] = []
        for rel in files:
            if len(snippets) >= self.max_files:
                break
            text, note = self._read_one(rel, keywords)
            if text is None:
                if note:
                    meta.append(note)
                continue
            snippets[rel] = text
            if note:
                meta.append(note)
        return snippets, meta

    def _read_one(self, rel: str, keywords: List[str]) -> Tuple:
        """读取单个嫌疑文件：小文件全文，大文件给关键词锚点附近的窗口。
        返回 (text, note)；text=None 表示不可读（note 说明原因）。"""
        text, note = self._read_full(rel)
        if text is None:
            return None, note
        lines = text.split("\n")
        if len(text) <= self.file_cap_bytes:
            return text, (f"{rel}: {note}" if note else "")
        # 中等体积文件（≤32KB）直接全文给出更稳：窗口化对 600 行的 .vue 组件
        # 会把「模板 handler 引用」与「方法实现」切开，模型看不到完整调用链。
        # .vue 组件是缺陷定位的主战场，值得为它放宽预算。
        if len(text) <= max(self.file_cap_bytes * 3, 32000):
            return text, (f"{rel}: {note}" if note else "")
        # 大文件：优先给「关键词命中点包围窗口」，而不是单一的 _anchor_line。
        # 单锚点曾让 repeat-form.vue 只给出 a-time-picker 的模板行，
        # 真正要改的 onTimeChange 实现（`time || '08:00'`）落在窗口之外——
        # 模型据此判定「窗口不足」而按护栏拒出补丁。命中点包围窗口保证
        # 定义处与调用处同时在窗口内。
        hits = self._hit_lines(lines, keywords)
        # 窗口行数的下限：命中点包围区若很宽（定义+调用相隔几百行），
        # 80 行的固定窗口会把两端切掉其一。这里放宽到命中区间的实际跨度。
        span = (max(hits) - min(hits) + 8) if hits else 0
        win_lines = max(self.context_lines, min(span, 600))
        if hits:
            start = max(0, min(hits) - 3)
            end = min(len(lines), max(hits) + 4)
            if end - start < win_lines:
                pad = win_lines - (end - start)
                start = max(0, start - pad // 2)
                end = min(len(lines), start + win_lines)
        else:
            anchor = self._anchor_line(lines, keywords)
            half = self.context_lines // 2
            start = max(0, anchor - half)
            end = min(len(lines), start + self.context_lines)
        window = "\n".join(lines[start:end])
        if len(window) > self.file_cap_bytes:  # very long lines
            window = window[: self.file_cap_bytes]
            end = start + len(window.split("\n"))
        return window, (
            f"{rel}: 文件 {len(lines)} 行，仅提供第 {start + 1}-{end} 行"
            f"（{len(hits)} 处关键词命中已纳入）；SEARCH 块只能引用这段窗口内的原文"
        )

    def _hit_lines(self, lines: List[str], keywords: List[str]) -> List[int]:
        """关键词命中行号 + Vue handler 调用链（模板引用 → 方法定义）。

        仅用高精度关键词定窗：泛词（任务/time 之类的中文二字词）命中遍布
        全文，用来定窗会把窗口拉成随机一段，反而错过真正的修改点。

        还必须顺着模板事件处理器展开：本案工单只写了现象（失焦回退 08:00），
        不可能写出 `onTimeChange` 这个函数名——但它就挂在
        `<a-time-picker @change="onTimeChange">` 上。只按关键词定窗时
        窗口停在模板行，真正要改的 `time || '08:00'`（方法体）被留在窗口外，
        模型据此判定「窗口不足」而按护栏拒出补丁。"""
        ident_kws = getattr(self, "_ident_kws", set()) or set()
        strong = [k for k in keywords if k in ident_kws or "/" in k
                  or any(c.isupper() for c in k)
                  # 中文 UI 串（运行日志/触发时间）是前端缺陷最常见的唯一定位
                  # 证据——工单不可能写出函数名，只能写出界面上看到的字
                  or (len(k) >= 4 and not k.isascii())]
        if not strong:
            strong = keywords[:8]
        out: List[int] = []
        handler_names: List[str] = []
        for i, ln in enumerate(lines):
            low = ln.lower()
            if not any(k.lower() in low for k in strong):
                continue
            out.append(i)
            # @change="onTimeChange" / @input="onDateChange" / @blur="fn"
            for m in re.finditer(r"@[\w.-]+\s*=\s*[\"']([A-Za-z_$][\w$]*)", ln):
                if m.group(1) not in handler_names:
                    handler_names.append(m.group(1))
        # handler 定义：全文件找 `name(` 且不是重新引用
        for name in handler_names[:6]:
            pat = re.compile(rf"\b{re.escape(name)}\s*[(:]")
            for i, ln in enumerate(lines):
                if pat.search(ln) and i not in out:
                    out.append(i)
        return sorted(out)

    def _read_full(self, rel: str):
        try:
            full = self.repo_path / rel
            if not full.is_file():
                return None, f"{rel}: 文件不存在（git grep 结果已过期）"
            raw = full.read_bytes()
            if len(raw) > 400_000:
                return None, f"{rel}: 文件过大（{len(raw)} 字节），未纳入上下文"
            text = raw.decode("utf-8")
            return text, ""
        except UnicodeDecodeError:
            return None, f"{rel}: 非 UTF-8 文本，已跳过"
        except Exception as e:
            return None, f"{rel}: 读取失败 {e}"

    def _anchor_line(self, lines: List[str], keywords: List[str]) -> int:
        refs = getattr(self, "_line_refs", [])
        if refs and 1 <= refs[0] <= len(lines):
            return refs[0] - 1
        ident_kws = getattr(self, "_ident_kws", set()) or set()
        scored: List[Tuple[int, int]] = []
        for i, ln in enumerate(lines):
            low = ln.lower()
            stripped = ln.lstrip()
            # import/require 区命中全是泛词误中，把窗口拉到文件头部毫无价值
            # （历史案例：4 个窗口全部停在 1-60 行，模型只能拒出补丁）
            if stripped.startswith(("import ", "import{", "import(")) or \
                    ("require(" in low and stripped.startswith(("const ", "var ", "let "))):
                continue
            kws_hit = {kw.lower() for kw in keywords[:16] if kw.lower() and kw.lower() in low}
            if not kws_hit:
                continue
            # 不同关键词的命中数优先于总次数：一行里同时出现 3 个工单关键词
            # 的位置远比一个词重复 5 次的位置更像修改点
            score = 10 * len(kws_hit) + sum(5 for kw in kws_hit if kw in ident_kws)
            scored.append((-score, i))
        if scored:
            best = max(-neg for neg, _ in scored)
            at_best = [i for neg, i in scored if -neg == best]
            # 取最后一次出现：定义/调用点通常在文件后部，
            # 而 SSE 派发/import 等同名引用在前（本案 getFinalContent 439 vs 147）
            return at_best[-1]
        return 0

    # ------------------------------------------------------------- prompt
    def _build_prompt(self, defect: Dict, files: Dict[str, str], meta: List[str]) -> str:
        files_section = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in files.items()
            if content
        ) or "(未定位到嫌疑文件)"
        meta_section = "\n".join(f"- {m}" for m in meta) or "- 所有嫌疑文件均完整提供"
        desc = (defect.get("description") or defect.get("desc") or "")[:3000]

        return (
            "你是资深前端工程师。基于缺陷描述与嫌疑文件，给出**最小改动补丁**。\n\n"
            "## 缺陷\n"
            f"- ID: {defect.get('id') or defect.get('issueUUID')}\n"
            f"- 标题: {defect.get('title', '')}\n"
            f"- 优先级: {defect.get('priority', '')}\n"
            f"- 描述: {desc}\n\n"
            "## 嫌疑文件\n"
            f"文件读取情况说明：\n{meta_section}\n\n"
            f"{files_section}\n\n"
            "## 输出要求\n"
            "严格输出一个 JSON 对象，不要任何解释性前后缀：\n"
            "{\n"
            '  "root_cause": "根因(1-3 句，具体到文件与行)",\n'
            '  "category": "数据/UI/逻辑/性能/兼容/接口/其他",\n'
            '  "explanation": "为什么这样改能修好，以及影响面",\n'
            '  "prevention": "如何避免再次发生(可执行动作)",\n'
            '  "patch_blocks": "SEARCH/REPLACE 补丁文本，见下方格式"\n'
            "}\n\n"
            "### patch_blocks 格式（必须严格遵守）\n"
            "每个改动一个块，用 \\n 换行：\n"
            "<<<<<<< SEARCH 相对/文件/路径.tsx\n"
            "此处逐字复制文件中的原始代码\n"
            "=======\n"
            "此处写修改后的代码\n"
            ">>>>>>> REPLACE\n\n"
            "硬性规则，违反即视为无效补丁：\n"
            "1. SEARCH 段必须**逐字复制**上面提供的内容，不得改写、省略、加省略号或重排；\n"
            "2. 只包含需要修改的行及其少量上下文（2-6 行为宜），**禁止整文件覆写**；\n"
            "3. 同一文件多处改动就写多个块；文件路径必须与上面 ### 后的路径完全一致；\n"
            "4. 若提供的窗口不足以确定正确改法，patch_blocks 填空字符串，"
            "并在 root_cause 里说明需要看哪个文件的哪一段——宁可不改，也不要猜；\n"
            "5. 只做与该缺陷直接相关的改动，不顺手重构、不改格式、不动无关代码；\n"
            "6. 若证据表明根因不在前端仓库（如请求参数已正确发出而后端返回异常、"
            "同接口其它类型正常仅此类型异常、需改后端接口或数据），**不要编造前端补丁**："
            "patch_blocks 留空，category 填「接口」，root_cause 以「非前端缺陷：」开头，"
            "列出证据链（对照请求/响应、正常类型与异常类型的差异）。"
        )
