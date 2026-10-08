# -*- coding: utf-8 -*-
"""几个被复制过的小工具，收在唯一一处。

为什么值得收：今天两个真 bug 都源于「同一个规则有两份实现、各自漂移」——
路径校验在 artifact 与 fixer 各一份（其中一份漏了 /etc/passwd 那类），采纳落盘在
两个 Applier 各一份（回滚新建文件的行为都不一样）。这里处理的三类是同一个模式的小版，
但注意**不是所有同名函数都该合**：

  - `_clip` 在 chat / kb_patterns / team_run 里有三份，语义各不相同（面板文本要带
    「原文 N 字符」提示；markdown 表格单元格要转义 `|` 并压换行；团队日志只要硬截断），
    所以**没有**收进这里。合并它们会把提示文字或转义规则弄丢。
  - `_slug` 四份只差 (maxlen, default)，可以收，参数保留各自取值。
  - 密钥掩码两份阈值不同（全遮阈值 8 vs 10、头部保留 3 vs 6 位），各有一半更严：
    统一取「阈值 10 + 头部 3 位」，即两份的历史行为都不会被放松。
  - frontmatter 解析两份逐字相同，纯重复。
"""
import re
from typing import Dict


def slug(s, *, maxlen: int = 60, default: str = "local") -> str:
    """把任意字符串压成可当文件名/ id 片段的形态。

    maxlen/default 保留各调用方原有取值（提案 60/local、产出物 40/task、
    会话 40/chat、团队作业 40/run），改的是实现位置，不是行为。
    """
    return (re.sub(r"[^0-9A-Za-z._-]+", "-", str(s or ""))
            .strip("-")[:maxlen] or default)


def mask_secret(v, *, head: int = 3, tail: int = 4, threshold: int = 10) -> str:
    """给界面/日志展示密钥用的掩码：短到看不出结构就全星号，否则只露首尾。

    参数取的是历史上两份实现里**各自更严的那一半**：ai_providers 原先阈值 10（短的
    全遮，比 web 的 8 严）但头部露 6 位（比 web 的 3 位松），web 则相反。统一成
    threshold=10 + head=3 之后，两份都不会比原来更宽松 —— 反过来若照抄 web 的 8，
    9~10 字符的密钥就会从"全遮"退化成"露 7 位"。
    """
    s = str(v or "")
    if len(s) <= threshold:
        return "*" * len(s)
    return f"{s[:head]}…{s[-tail:]} ({len(s)} chars)"


def parse_frontmatter(text: str) -> Dict[str, str]:
    """极简 YAML frontmatter：只取 `key: value` 一行对，去引号。

    刻意不引 yaml 依赖，也不支持嵌套/列表——卡片只要平铺字段。解析不出结构时
    返回 {}，调用方按「没有元数据」处理。
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: Dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out
