# -*- coding: utf-8 -*-
"""推送前的载荷扫描：密钥、真实本地路径、内网地址、内部标识与指纹串。

用法：
    python tools/prepush_scan.py                  # 扫 origin/main..HEAD 的全部新增行
    python tools/prepush_scan.py HEAD~3..HEAD     # 只扫最近几个提交
    python tools/prepush_scan.py --worktree        # 扫工作区未提交改动（含未跟踪文件）

为什么在仓库里留这个：本仓库有 GitHub 远端，而它日常处理的是内网 ONES 工单、私有仓库
路径与真实工单号。**知识卡片与提案是脱敏导出的，但提交说明、CHANGELOG、注释里的散文
没有脱敏流程** —— 一个 `D:/云枢项目/...` 或一串真实工单号写进注释，就等于把内部结构
推到公开仓库。这类泄漏人眼看不出来，所以要有个机械的最后一道。

退出码：命中即非零（可以直接挂成 git pre-push 钩子）。规则是**提示**而不是白名单：
命中后要人工判定，误报（如合成串 `EXAMPLEid0000001`）靠形状本身放行不了，
所以形状命中后要在下面那份去重清单里逐个确认。
"""
from __future__ import annotations

import re
import subprocess
import sys

BS = chr(92)          # 反斜杠，避免在字符串里再套一层转义歧义

# (名称, 正则) —— 全部只扫**新增行**，历史里的旧问题不该让每次推送都红
PATTERNS = {
    "疑似密钥赋值": r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
    "sk- 型 key": r"\bsk-[A-Za-z0-9]{16,}",
    "JWT 段": r"\beyJ[A-Za-z0-9_\-]{20,}",
    "C:Users 真实用户名": r"C:" + BS + r"{1,2}Users" + BS + r"{1,2}(?!name|You|user|27409)\w{2,}",
    # 盘符路径必须**不是 URL**：`https://…` 后面跟中文很常见，早先的写法会把
    # "s://...)`。注意整行是先" 当成 `D:/云枢/...` 那样的内部路径报出来。
    "含中文的绝对路径": r"(?i)(?:^|[\s\"'(=])[A-Za-z]:" + BS + r"{1,2}(?!//)[^" + BS + r"\"'\s]*[一-鿿]",
    "盘符正斜杠+中文": r"(?i)(?:^|[\s\"'(=])[A-Za-z]:(?!//)[^\"'\s]*[一-鿿]",
    "内网 IP": r"\b(?:10|192\.168|172\.(?:1[6-9]|2[0-9]|3[01]))\.\d{1,3}\.\d{1,3}\b",
    "32 位 hex 指纹": r"\b[0-9a-f]{32}\b",
    # 真实工单号形状（16 位、大小写与数字混合）。合成串 EXAMPLEid0000001 也满足形状，
    # 所以这条命中后要人工看一眼是不是假数据。
    "ONES 记录号样式": r"\b(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{16}\b",
}


def added_lines(spec: str, worktree: bool = False) -> str:
    """要扫的新增内容。"""
    if worktree:
        tracked = subprocess.check_output(
            ["git", "diff", "--unified=0"], stderr=subprocess.DEVNULL
        ).decode("utf-8", "replace")
        untracked = ""
        listing = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True)
        for rel in filter(None, (listing.stdout or b"").split(b"\0")):
            name = rel.decode("utf-8", "replace")
            if not name.startswith(("knowledge_base/", "proposals/", ".workbuddy/")):
                continue     # 这些目录被忽略，本来就不进推送载荷
            untracked += name + "\n"
        plus = "\n".join(l[1:] for l in tracked.splitlines()
                         if l.startswith("+") and not l.startswith("+++"))
        return plus + "\n" + untracked
    cmd = ["git", "diff"] + ([spec] if " " not in spec else spec.split())
    diff = subprocess.check_output(cmd).decode("utf-8", "replace")
    return "\n".join(l[1:] for l in diff.splitlines()
                     if l.startswith("+") and not l.startswith("+++"))


def main() -> int:
    args = [a for a in sys.argv[1:]]
    worktree = "--worktree" in args
    positional = [a for a in args if not a.startswith("-")]
    spec = positional[0] if positional else "origin/main..HEAD"
    try:
        plus = added_lines(spec, worktree)
    except subprocess.CalledProcessError as e:
        print(f"取 diff 失败（远端分支不存在？先 git fetch 或显式传范围）：{e}",
              file=sys.stderr)
        return 2
    print(f"扫描 {spec}{'（工作区）' if worktree else ''} 的 "
          f"{len(plus.splitlines())} 行新增内容\n")
    total = 0
    hits_by_name = {}
    for name, rx in PATTERNS.items():
        found = [m.group(0) for m in re.finditer(rx, plus)]
        hits_by_name[name] = found
        total += len(found)
        uniq = sorted(set(found))
        show = f"  例：{uniq[:3]}" if uniq else ""
        print(f"  {name:<22} {len(found):>3} 处{show}")
    print(f"\n合计命中 {total} 处；下面逐条列出去重值供人工判定")
    seen = set()
    for name, found in hits_by_name.items():
        for v in sorted(set(found)):
            if v in seen:
                continue
            seen.add(v)
            print(f"  [{name}] {v[:80]}")
    if total:
        print(f"\n⚠ 有 {len(seen)} 个待确认值。确认都是合成/无害内容后再推送；"
              "真实内部标识请改写后重新提交。")
    else:
        print("\n✓ 没有命中")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
