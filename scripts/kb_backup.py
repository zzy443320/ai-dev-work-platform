# -*- coding: utf-8 -*-
"""知识库保底：原始目录打包备份 + 可入库的脱敏副本。

为什么需要：``knowledge_base/`` 里是这条流水线唯一会随时间增值的资产（同类缺陷的
现象/根因/修复/预防 + 归纳出的模式卡），但它被 ``.gitignore`` 排除（里面确实是真实
工单数据）。于是「沉淀」这一步的成果既不在版本控制里、也没有任何备份——误删或换机
就归零。本模块补两条路：

    backup_zip()     原始目录整份打成一个 zip（放本地备份目录，绝不入库）
    export_masked()  按规则洗掉内网地址/凭据/内部标识后，输出一份可以提交进仓库的副本

脱敏是**规则驱动**的，不是「删掉整个字段」：现象/根因/修复这类正文要尽量留下（那才是
知识），要挡住的是能定位到具体客户/内网/账号的信息。词表放在知识库目录下的
``mask_terms.txt``（一行一个，``#`` 起头是注释；那个文件本身也在 .gitignore 里，
所以内部产品名不会因为这个工具而进仓库）。
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

TERM_FILE = "mask_terms.txt"
KEEP_TERM_FILE = "keep_terms.txt"
README_NAME = "EXPORT-NOTE.md"

# 公开文档站白名单：这些域名后的链接保留（修复思路常常就挂在官方文档上），
# 其余一律视为内网/客户环境地址。默认从严：不在名单上就脱敏。
PUBLIC_HOSTS = {
    "react.dev", "typescriptlang.org", "nodejs.org", "npmjs.com",
    "developer.mozilla.org", "stackoverflow.com", "github.com", "gitlab.com",
    "vuejs.org", "element-plus.org", "vite.dev", "jestjs.org", "playwright.dev",
    "mdn.io", "caniuse.com", "semver.org", "json.org", "yaml.org",
}

# 每条规则：(名字, 编译好的正则, 替换)。规则要能单独解释，才不会退化成一次性脚本。
_PLACEHOLDER_URL = "<已脱敏链接>"
_PLACEHOLDER_ID = "<已脱敏ID>"
_PLACEHOLDER_PATH = "<已脱敏路径>"
_PLACEHOLDER_CRED = "<已脱敏账号凭据>"
_PLACEHOLDER_MAIL = "<已脱敏邮箱>"


# 绝对路径的形状。**脱敏与自检必须共用这一个表达式**：先前两处各写一份，脱敏那侧
# 加了 (?<![A-Za-z0-9]) 保护而自检那侧没加，结果 Playwright 错误文本里的 `…log:\ncall`
# 被自检当成"路径原子"报成泄露（而脱敏侧正确地没有动它）。这类不一致只会带来假警报。
ABS_PATH = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|~[\\/]|/home/|/Users/)"
                      r"[^\s\"'`,]+")


def _mask_opaque(m: re.Match) -> str:
    return _PLACEHOLDER_ID


# 卡片里常有「repo-mirror.internal.cn 这台机器」这种**不带协议头**的主机名，URL 规则
# 抓不到。域名形状本身足够特定（至少两级 + 已知 TLD），所以可以按规则洗；公开文档域名
# 同样走白名单，不会被误洗。
_TLDS = "cn|com|net|org|io|internal|local|co|dev|test"
BARE_DOMAIN = re.compile(r"(?<![\w.-])(?:[\w-]+\.){1,4}(?:" + _TLDS + r")(?![\w-])")


def _mask_bare_domain(m: re.Match) -> str:
    if _is_public_host(m.group(0)):
        return m.group(0)
    return _PLACEHOLDER_URL


# 「≥12 位且大小写+数字混合」这个形状，**没法**同时区分开「ONES 工单号」和「驼峰代码
# 标识符」：真实知识库里 11 个工单号的最长连续小写串是 1~5，而 getUserItems2 是 4、
# ProcessListPanel2 是 3——取任何阈值都会两头漏（先按小写串≥5 放过代码，就漏掉了一个
# 真工单号；实测如此）。所以这里按**失败方向保守**处理：形状命中就当成 ID 洗掉，
# 误伤的代码线索用 keep_terms.txt 点名捞回。漏脱敏会外泄，多脱敏只是丢一点线索且能
# 在 diff 里看见，两者不对等。导出后还有 audit_masked() 兜底。
_OPAQUE_CANDIDATE = re.compile(
    r"\b(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{12,}\b")


def _is_public_host(host: str) -> bool:
    """是否在公开文档站白名单上。脱敏与自检必须用**同一个**判定，否则会出现
    「规则故意保留 react.dev，自检却把它当内网主机名报泄露」这种自相矛盾（实测踩过）。
    """
    h = (host or "").lower().split(":")[0].rstrip(".")
    if not h:
        return False
    if h in PUBLIC_HOSTS or h.endswith((".github.io", ".readthedocs.io")):
        return True
    return any(h == pub or h.endswith("." + pub) for pub in PUBLIC_HOSTS)


def _mask_url(m: re.Match) -> str:
    url = m.group(0)
    host_m = re.match(r"https?://([^/\s:?#]+)", url)
    if _is_public_host(host_m.group(1) if host_m else ""):
        return url
    return _PLACEHOLDER_URL


RULES: List[Tuple[str, re.Pattern, object]] = [
    # 链接：先过白名单，非公开域名整条替换
    ("url", re.compile(r"https?://[^\s<>\"'）)】]+"), _mask_url),
    # 裸域名（不带协议头）：必须排在 url 之后，否则会把 URL 的主机段洗两遍
    ("bare-domain", BARE_DOMAIN, _mask_bare_domain),
    # ONES 记录号这类不透明标识（形状判断见 _is_opaque_id，避免连代码标识符一起洗掉）
    ("opaque-id", _OPAQUE_CANDIDATE, _mask_opaque),
    # 本地绝对路径：盘符 / ~ / /home / /Users。
    # 前导的 (?<![A-Za-z0-9]) 不是多余的：不写它，`[A-Za-z]:[\\/]` 会命中
    # `https://…` 里那个 `s:/`，于是**连白名单放行的公开文档链接都会被当成路径洗掉**
    # （实测 https://react.dev/... 变成 http<已脱敏路径>）。形状定义见 ABS_PATH。
    ("abs-path", ABS_PATH, _PLACEHOLDER_PATH),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     _PLACEHOLDER_MAIL),
    # 迭代/版本号这类排期标记：留形状、去数值
    ("sprint", re.compile(r"【\s*(迭代|需求|版本)\s*\d+\s*】"), r"【\1N】"),
    # 刻意**没有**「snake_case 短标识」这类规则：形如 vd_customer 的实体号确实想挡，
    # 但它和 kb_version / defect_id / failed_checks 这些字段名在结构上无法区分，
    # 加上就会把卡片自己的 frontmatter 与模式卡的段落名一起洗掉（实测一次导出洗掉
    # 312 处，绝大多数是字段名），等于为了挡少数几个词毁掉整份知识。实体前缀请写进
    # mask_terms.txt 的词表——那里能区分「这是客户环境里的东西」和「这是代码字段名」。
]

# 凭据的两种真实形状：① 关键词和值在同一行（账号：xxx / token=abc）；
# ② 关键词独占一行、值在后面 1~2 个非空行里——知识库卡片就是
# 「【测试账号】」→ 空行 → 「user/pass」。只做同形替换会整类漏掉 ②，实测就是这样。
CRED_KW = re.compile(r"(测试账号|测试帐号|账号|帐号|口令|密码|password|passwd"
                     r"|token|secret|credential|凭据)", re.I)
KV_SECRET = re.compile(r"(?P<k>(账号|帐号|口令|密码|password|passwd|token|secret"
                       r"|credential|凭据)\s*[:：=]\s*)(?P<v>\S{3,})", re.I)
# user/pass 形状。用户名限 ASCII（\w 不含汉字），所以「A/B 两种登录」这种正常中文
# 句子不会命中——整行必须严格是 user / pass 两段。
USER_PASS = re.compile(r"^\s*(?P<user>[\w.@+-]{2,40})\s*/\s*(?P<pass>[^\s]{3,})\s*$")


def _mask_credentials(lines: List[str]) -> List[str]:
    out = list(lines)
    for i, line in enumerate(lines):
        if not CRED_KW.search(line):
            continue
        if KV_SECRET.search(line):
            out[i] = KV_SECRET.sub(lambda m: m.group("k") + _PLACEHOLDER_CRED, out[i])
        seen = 0
        for j in range(i + 1, min(i + 6, len(lines))):
            if not lines[j].strip():
                continue          # 关键词与值之间的空行不算「换话题」
            seen += 1
            if USER_PASS.match(lines[j]):
                out[j] = _PLACEHOLDER_CRED
                break
            if seen >= 2:
                break             # 再远就不像是这个账号的值了
    return out


def _apply_rules(line: str) -> str:
    out = line
    for _name, pattern, repl in RULES:
        out = pattern.sub(repl, out)  # type: ignore[arg-type]
    return out


def mask_text(text: str, terms: Iterable[str] = (),
              keep: Iterable[str] = (),
              tokens: Optional[Dict[str, str]] = None) -> str:
    """整份脱敏。

    terms   —— 内部标识词表，大小写不敏感地整体替换成 ``<内部标识>``；
    keep    —— 白名单：这些串在任何规则之前先被保护起来，全部规则跑完再原样放回。
               用于捞回被「不透明 ID」形状规则误伤的代码线索。
    tokens  —— 真实 ID → 合成 token 的映射（见 id_tokens()）。走同一套「先保护后放回」
               机制，所以既不会被别的规则二次改写，也不会残留原值。文件名与内容必须用
               **同一份**映射，否则 INDEX.md 里的链接会指向不存在的路径。
    """
    keeps = sorted({k for k in (t.strip() for t in keep) if k}, key=len, reverse=True)
    protected = text
    sentinels: Dict[str, str] = {}
    for i, tok in enumerate(keeps):
        sentinel = f"\x00KEEP{i}\x00"
        if tok in protected:
            protected = protected.replace(tok, sentinel)
            sentinels[sentinel] = tok
    for i, (real, fake) in enumerate(sorted((tokens or {}).items(), key=lambda kv: -len(kv[0]))):
        sentinel = f"\x00TOK{i}\x00"
        if real in protected:
            protected = protected.replace(real, sentinel)
            sentinels[sentinel] = fake

    lines = _mask_credentials(protected.split("\n"))
    out = "\n".join(_apply_rules(l) for l in lines)
    for term in terms:
        t = term.strip()
        if not t:
            continue
        out = re.sub(re.escape(t), "<内部标识>", out, flags=re.IGNORECASE)
    for sentinel, tok in sentinels.items():
        out = out.replace(sentinel, tok)
    return out


def collect_ids(kb_dir: Path) -> Dict[str, str]:
    """扫描**原始**知识库，给每个不透明 ID（工单记录号）分配一个确定性合成 token。

    token 形如 rec-3fa1c02d：纯小写十六进制，不会被 opaque 形状规则二次命中，
    而且是文件名安全的。用 sha1 而不是计数器，保证同一个 ID 跨多次导出得到同一个
    token（可复现，diff 不会因导出顺序而抖）。

    文件名也要扫 —— 卡片文件名本身就是工单号，只洗内容会把真实记录号原样留在路径里
    （这是本模块一开始的盲点，导出提交前的自检才发现）。
    """
    found: set = set()
    for md in sorted(Path(kb_dir).rglob("*.md")):
        found |= set(_OPAQUE_CANDIDATE.findall(
            md.read_text(encoding="utf-8", errors="replace")))
        found.add(md.stem)
    return {t: f"rec-{hashlib.sha1(t.encode('utf-8')).hexdigest()[:8]}"
            for t in sorted(found) if len(t) >= 8}


def load_terms(kb_dir: Path, name: str = TERM_FILE) -> List[str]:
    """读一个词表文件（``mask_terms.txt`` 要挡的 / ``keep_terms.txt`` 要留的）。

    缺文件时返回空表并给出可写提示（不猜词表）。
    """
    path = Path(kb_dir) / name
    if not path.is_file():
        return []
    terms = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    return [t for t in terms if t and not t.startswith("#")]


def write_term_template(kb_dir: Path) -> Path:
    """生成一份词表模板，让用户知道该往里填什么。"""
    path = Path(kb_dir) / TERM_FILE
    if path.exists():
        return path
    path.write_text(
        "# 一行一个内部标识，# 起头是注释；大小写不敏感地整体替换成 <内部标识>。\n"
        "# 这个文件本身在 .gitignore 里（知识库目录整份都不入库），所以填进去的词\n"
        "# 不会因为导出而进仓库。\n"
        "#\n"
        "# 该填什么：产品名 / 组织名 / 客户环境名 / 内网域名关键词，以及**客户环境里\n"
        "# 那种实体短号**（如 vd_xxxx）。这类词无法用正则规则区分——它和代码字段名\n"
        "# kb_version / defect_id 长得一模一样，写成规则就会把卡片的 frontmatter 一起\n"
        "# 洗掉，所以只能靠词表点名。\n"
        "#\n"
        "# 例：\n"
        "# some-internal-product\n"
        "# vd_\n",
        encoding="utf-8")
    return path


def write_keep_template(kb_dir: Path) -> Path:
    """生成 keep_terms.txt 模板：被形状规则误伤、需要原样保留的代码线索。"""
    path = Path(kb_dir) / KEEP_TERM_FILE
    if path.exists():
        return path
    path.write_text(
        "# 一行一个串，导出时**不**做任何替换（先保护、后脱敏、再放回）。\n"
        "# 用途：卡片里出现了「12 位以上且大小写+数字混合」的标识符（形如\n"
        "# getXxxItems2），它和 ONES 工单号在形状上无法区分，会被当成 ID 洗掉；\n"
        "# 确认那是代码线索而不是工单号时，把它加到这里。\n"
        "#\n"
        "# 例：\n"
        "# renderRowCells1\n",
        encoding="utf-8")
    return path


def _front_fields(text: str) -> Dict[str, str]:
    """粗取 frontmatter 的 key: value（只为统计和文件名服务，不改内容）。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: Dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def export_masked(kb_dir: str | Path, out_dir: str | Path,
                  terms: Optional[Iterable[str]] = None,
                  keep: Optional[Iterable[str]] = None) -> Dict:
    """把 ``kb_dir`` 下所有 .md 脱敏后写进 ``out_dir``，保持子目录结构。

    只导 .md（卡片 + 模式 + INDEX），``_stats.json`` 里带真实路径所以不导，改为
    在导出的 README 里给出「卡片数/类目数」这类不含敏感信息的统计。
    词表缺省从知识库目录下的 ``mask_terms.txt`` / ``keep_terms.txt`` 读。
    """
    src = Path(kb_dir).resolve()
    dst = Path(out_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"知识库目录不存在: {src}")
    if src == dst:
        raise ValueError("导出目录不能就是知识库本身")

    if terms is None:
        terms = load_terms(src, TERM_FILE)
    if keep is None:
        keep = load_terms(src, KEEP_TERM_FILE)
    keep = list(keep)

    files = sorted(p for p in src.rglob("*.md") if p.is_file())
    # 卡片文件名本身就是工单号 → 导出必须连文件名一起换成合成 token（collect_ids 与
    # 导出用的是同一份映射，所以 INDEX 里的链接还能指得到）。
    tokens = collect_ids(src)
    written: List[Path] = []
    for md in files:
        rel = md.relative_to(src)
        raw = md.read_text(encoding="utf-8", errors="replace")
        clean = mask_text(raw, terms, keep, tokens)
        clean_rel = Path(mask_text(str(rel).replace("\\", "/"), tokens=tokens))
        target = dst / clean_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(clean, encoding="utf-8")
        written.append(target)

    # 清掉上一次导出留下的 .md：文件名换了 token 之后，旧名字的文件不会被覆盖，而是
    # 和新的并存 —— 旧文件里带着**上一次的真实记录号命名**（实测就是这样把 13 个
    # 工单号留在导出目录里的）。所以重导必须扫尾，否则「脱敏副本」反而是最脏的一份。
    keep_set = {p.resolve() for p in written} | {dst / README_NAME}
    stale = [p for p in dst.rglob("*.md") if p.resolve() not in keep_set]
    for p in stale:
        try:
            p.unlink()
        except OSError:
            pass

    per_category: Dict[str, int] = {}
    masked_files = len(written)
    for target in written:
        rel = target.relative_to(dst)
        cat = rel.parts[0] if len(rel.parts) > 1 else "_root"
        per_category[cat] = per_category.get(cat, 0) + 1

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (dst / README_NAME).write_text(
        f"# 知识库脱敏副本\n\n"
        f"> 由 `scripts/kb_backup.py` 从 `knowledge_base/` 导出，生成时间：{stamp}\n"
        f"> 共 {masked_files} 份 markdown，{len(per_category)} 个类目。"
        f"词表：挡 {len(list(terms))} 个词，留 {len(keep)} 个词。\n\n"
        "## 这份副本的定位\n\n"
        "- **不是**知识库本身。运行流水线读的是未脱敏的 `knowledge_base/`（它不入库）。\n"
        "- 作用是让「沉淀」这件事进版本控制：卡片误删、换机器、开源这份工具时，"
        "同类缺陷的现象/根因/修复/预防不会归零。\n"
        "- **文件名也做了脱敏**：卡片文件名本身就是工单记录号，所以导出时按 sha1 换成 "
        "`rec-xxxxxxxx` 形式的合成名（同一个工单号跨多次导出得到同一个 token，diff 稳定）。"
        "INDEX.md 里的链接与正文中的引用同步换成同一个 token，链接仍然可点。\n"
        "- 脱敏规则见 `scripts/kb_backup.py`；词表在知识库目录下的 `mask_terms.txt`"
        "（要额外挡的内部标识）与 `keep_terms.txt`（被形状规则误伤、要捞回的代码线索）。"
        "回归用例见 `tests/check_kb_export.py`。\n\n"
        "## 提交前必须跑一次自检\n\n"
        "规则是启发式的，不可能自己保证不漏。所以导出之后要跑"
        "`python run.py --audit-kb`：它从**原始**知识库里抽出内网主机名、账号口令、"
        "工单记录号这些敏感原子，逐个确认在副本里已经消失。任何残留都会连文件带行号列出来，"
        "把那个词加进 `mask_terms.txt` 再重导一次。\n\n"
        "## 仍然不要提交的东西\n\n"
        "`_stats.json` 与提案原件里都可能有真实路径与工单号，导出时刻意跳过了 json。"
        "若要分享，先看一眼这份副本的 diff。\n",
        encoding="utf-8")

    return {"files": masked_files, "categories": per_category,
            "terms_used": len(list(terms)), "keep_used": len(keep),
            "out_dir": str(dst)}


def _sensitive_atoms(kb_dir: Path) -> Dict[str, List[str]]:
    """从**原始**知识库里抽出敏感原子：内网主机名、账号口令、工单记录号。

    只抽形状可确定的那几类；返回 {原子: [出处文件, ...]}。调用方负责不把它整个打印出来。
    """
    atoms: Dict[str, List[str]] = {}

    def add(tok: str, where: str) -> None:
        tok = (tok or "").strip()
        if len(tok) >= 4:
            atoms.setdefault(tok, [])
            if where not in atoms[tok]:
                atoms[tok].append(where)

    for md in sorted(Path(kb_dir).rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            for m in re.finditer(r"https?://([^/\s:?#]+)", line):
                if not _is_public_host(m.group(1)):
                    add(m.group(1), f"{md.name}:{i + 1}")
            for m in BARE_DOMAIN.finditer(line):
                if not _is_public_host(m.group(0)):
                    add(m.group(0), f"{md.name}:{i + 1}")
            for m in _OPAQUE_CANDIDATE.finditer(line):
                add(m.group(0), f"{md.name}:{i + 1}")
            if CRED_KW.search(line):
                for m in USER_PASS.finditer(line):
                    add(m.group("user"), f"{md.name}:{i + 1}")
                    add(m.group("pass"), f"{md.name}:{i + 1}")
                # 关键词与值隔行（卡片里的常见排版）
                for nxt in lines[i + 1:i + 4]:
                    if not nxt.strip():
                        continue
                    m2 = USER_PASS.match(nxt)
                    if m2:
                        add(m2.group("user"), f"{md.name}:{i + 1}")
                        add(m2.group("pass"), f"{md.name}:{i + 1}")
                    break
        for m in ABS_PATH.finditer(text):
            add(m.group(0), md.name)
    return atoms


def seed_terms_from_credentials(kb_dir: str | Path) -> List[str]:
    """把卡片里出现过的**测试账号名**自动补进 ``mask_terms.txt``。

    为什么单列一步：账号名只在「用户名/口令」那一行被规则盖住，可它常常还会以裸出现的
    形式散在正文（复现人、@某人、提交记录），那部分规则挡不住——真实知识库自检就是这么
    查出 3 个账号名残留的。账号名的来源确定（凭据行），所以可以放心自动入表；产品名/
    客户环境名没有这种可靠来源，只能人工点名。

    返回本次新增的词（已存在的跳过）。
    """
    src = Path(kb_dir)
    users: set = set()
    for md in sorted(src.rglob("*.md")):
        lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if not CRED_KW.search(line):
                continue
            for m in USER_PASS.finditer(line):
                users.add(m.group("user").strip())
            for nxt in lines[i + 1:i + 4]:        # 关键词与值隔行的排版
                if not nxt.strip():
                    continue
                m2 = USER_PASS.match(nxt)
                if m2:
                    users.add(m2.group("user").strip())
                break
    users = {u for u in users if len(u) >= 2}

    path = src / TERM_FILE
    existing = load_terms(src, TERM_FILE)
    add = sorted(u for u in users if u.lower() not in {e.lower() for e in existing})
    if not add:
        return []
    body = path.read_text(encoding="utf-8").rstrip("\n") if path.exists() else (
        "# 自动创建：见 scripts/kb_backup.py 的 seed_terms_from_credentials")
    body += "\n\n# 由凭据行自动补入：卡片里会裸出现的测试账号名\n" + "\n".join(add) + "\n"
    path.write_text(body, encoding="utf-8")
    return add


def audit_masked(kb_dir: str | Path, out_dir: str | Path) -> Dict:
    """导出后的泄密自检：原始库里的敏感原子，是否真的从副本里消失了。

    规则是启发式的、且只能挡住「形状已知」的东西；漏没漏必须靠比对原件才知道。
    keep_terms.txt 里点名的串**不算**泄露（那是有意保留的代码线索）。
    """
    src, dst = Path(kb_dir).resolve(), Path(out_dir).resolve()
    if not dst.is_dir():
        raise FileNotFoundError(f"导出目录不存在，先跑导出：{dst}")
    keep = set(load_terms(src, KEEP_TERM_FILE))
    exported = sorted(dst.rglob("*.md"))
    copy_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in exported)
    # 路径也要比 —— 卡片文件名就是工单号，只比内容会漏整批真实 ID（本模块最初的盲点）。
    copy_paths = "\n".join(str(p.relative_to(dst)).replace("\\", "/") for p in exported)
    atoms = _sensitive_atoms(src)
    leaked = []
    for tok, where in sorted(atoms.items()):
        if tok in keep:
            continue
        in_paths = tok in copy_paths
        if tok in copy_text or in_paths:
            entry = {"token": tok, "where": list(where)}
            if in_paths:
                entry["where"].append("⚠️ 出现在导出文件的**路径/文件名**里")
            leaked.append(entry)
    return {"checked": len(atoms), "leaked": leaked, "kept": len(keep),
            "out_dir": str(dst)}


def redact(tok: str) -> str:
    """把敏感原子压成「首尾各留一两个字符」的形状，用于报告里定位而不外泄。"""
    if len(tok) <= 5:
        return tok[:1] + "*" * (len(tok) - 1)
    return f"{tok[:2]}{'*' * (len(tok) - 3)}{tok[-1]}"


def backup_zip(kb_dir: str | Path, dest_dir: str | Path) -> Path:
    """把原始知识库整份压成一个带时间戳的 zip。

    不做删除式轮转：一份原始目录才一两百 KB，留全部的成本可以忽略，而删旧档换来的是
    「唯一一份备份刚好被轮转掉」的风险。要清理请手工挪进回收站。
    """
    src = Path(kb_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"知识库目录不存在: {src}")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest / f"knowledge_base-{stamp}.zip"
    # 同一秒里跑两次会撞名——备份工具最不能有的毛病是悄悄覆盖上一份备份。
    n = 1
    while out.exists():
        out = dest / f"knowledge_base-{stamp}-{n}.zip"
        n += 1
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(src)))
                count += 1
    return out


def summarize(export_result: Dict) -> str:
    cats = export_result.get("categories") or {}
    lines = [f"导出 {export_result.get('files', 0)} 份 markdown → {export_result.get('out_dir')}"]
    for cat, cnt in sorted(cats.items()):
        lines.append(f"  {cat}: {cnt}")
    if not export_result.get("terms_used"):
        lines.append(f"  ⚠️ 词表为空：内部产品名/组织名/客户环境实体号没有被替换，"
                     f"请在知识库里补 {TERM_FILE}（可先跑 write_term_template 生成模板）")
    lines.append(f"  白名单 {export_result.get('keep_used', 0)} 个串（被形状规则误伤的代码线索）")
    lines.append(f"  下一步务必自检：python run.py --audit-kb（比对原件确认零残留）")
    return "\n".join(lines)
