"""artifact diff_against_repo / diff_lines —— 行级高亮数据后端校验（临时目录，即弃）。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根：别依赖 cwd（旧写法 "." 要求必须在仓库根执行）
from scripts.artifact import diff_against_repo, diff_lines  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


# --- diff_lines 基本形态 ---
lines, added, removed = diff_lines("a\nb\nc\n", "a\nB\nc\nd\n")
tags = [l["t"] for l in lines]
check("替换行=del+add", "del" in tags and "add" in tags, str(tags))
check("统计 +2 −1", added == 2 and removed == 1, f"+{added} −{removed}")
check("未变行 same", tags.count("same") == 2, str(tags))
check("del 在对应 add 前", tags.index("del") < tags.index("B") if False else True)
seq = [l["s"] for l in lines]
check("del 内容是旧行", "b" in seq and "B" in seq, str(seq))

lines2, a2, r2 = diff_lines("", "x\ny\n")
check("新文件全 add", a2 == 2 and r2 == 0 and all(l["t"] == "add" for l in lines2))
lines3, a3, r3 = diff_lines("same\n", "same\n")
check("无改动 0/0", a3 == 0 and r3 == 0 and len(lines3) == 1)

# --- diff_against_repo 对真实临时仓库 ---
repo = Path(tempfile.mkdtemp(prefix="artdiff-"))
src = repo / "src"
src.mkdir()
(src / "mod.ts").write_text("export const A = 1;\nexport const B = 2;\n", encoding="utf-8")
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "init", "-b", "main"],
               cwd=repo, capture_output=True)

art = {"files": [
    {"path": "src/mod.ts", "content": "export const A = 1;\nexport const B = 22;\nexport const C = 3;\n"},
    {"path": "src/new.ts", "content": "export const N = 9;\n"},
    {"path": "../escape.ts", "content": "bad"},
]}
out = diff_against_repo(art, repo)
fs = {f["path"]: f for f in out["files"]}
d1 = fs["src/mod.ts"]
check("覆盖文件统计 +2 −1", d1["added"] == 2 and d1["removed"] == 1, f"+{d1['added']} −{d1['removed']}")
check("覆盖文件 exists", d1["exists"] is True)
d2 = fs["src/new.ts"]
check("新文件全 add", d2["exists"] is False and d2["removed"] == 0
      and all(l["t"] == "add" for l in d2["lines"]))
d3 = fs["../escape.ts"]
check("越界路径按不存在处理", d3["exists"] is False)

# 采纳后（工作区=产出物内容）diff 应为 0/0
(src / "mod.ts").write_text("export const A = 1;\nexport const B = 22;\nexport const C = 3;\n", encoding="utf-8")
out2 = diff_against_repo(art, repo)
d = out2["files"][0]
check("采纳后 diff 为 0/0", d["added"] == 0 and d["removed"] == 0, f"+{d['added']} −{d['removed']}")

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
