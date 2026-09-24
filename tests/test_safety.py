"""Safety + gate behaviour tests for the rewritten pipeline (temp repo, throwaway)."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scripts.pipeline as _pl  # noqa: E402
from fixture_defect import TEST_DEFECT  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402

# 内置示例工单已移除：测试用自己的固定 fixture 注入，不再依赖全局样本
_pl.SAMPLE_DEFECTS[:] = [TEST_DEFECT]

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=repo, capture_output=True, text=True).stdout.strip()


def make_repo(pkg=None, big=False):
    repo = Path(tempfile.mkdtemp(prefix="fixer-test-"))
    (repo / "src").mkdir()
    if big:
        # 总体积必须超过中等文件全文预算（32KB）才会走窗口化路径；
        # 每行带 // 注释既撑体积，又让 mock 补丁跳过这些填充行。
        lines = ["// big component"]
        for i in range(1200):
            lines.append(f"const v{i} = {i}; // padding comment for windowing test")
        # bug 行含工单描述里的标识符 processItem.template —— 关键词命中
        # 必须把窗口锚定在这一行，否则窗口化后 mock 拿不到病根代码。
        lines.append("const ProcessList = (props) => { const x = props.processItem.template.name; return 1; };")
        for i in range(300):
            lines.append(f"const w{i} = {i};")
        (repo / "src" / "ProcessList.tsx").write_text("\n".join(lines), encoding="utf-8")
    else:
        (repo / "src" / "ProcessList.tsx").write_text(
            "// mock file with TypeError bug\n"
            "const ProcessList = (props) => { const x = props.items.name; return <div>{x}</div>; };\n",
            encoding="utf-8")
    if pkg is not None:
        (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    git(str(repo), "init", "-b", "main")
    git(str(repo), "add", "-A")
    git(str(repo), "commit", "-m", "init")
    return repo


def config_for(repo, gate=None):
    return {
        "ones": {"base_url": "", "token": "", "project_uuid": ""},
        "ai": {"model": "x", "api_key": ""},
        "repo": {"path": str(repo), "branch": "main"},
        "gate": gate or {},
        "playwright": {"base_url": "http://127.0.0.1:1", "screenshot_dir": "./screenshots"},
        "knowledge_base": {"output_dir": str(Path(tempfile.mkdtemp()) / "kb")},
        "proposals": {"output_dir": str(Path(tempfile.mkdtemp()) / "proposals")},
    }


def propose(pipe):
    res = pipe.run(mock=True, defect_id=TEST_DEFECT["id"], limit=1, skip_verify=True)
    return res[0]["proposal"]


# ---------------------------------------------------------------- scenario 1
print("\n[1] package.json 自动探测 —— 通过的闸门")
repo = make_repo(pkg={"scripts": {"type-check": "node -e \"process.exit(0)\"",
                                  "test": "node -e \"process.exit(0)\""}})
pipe = AIDefectFixerPipeline(config_for(repo))
p = propose(pipe)
check("gate level = package_json", p["gate"]["level"] == "package_json", p["gate"]["level"])
check("两个命令都被识别", sorted(c["name"] for c in p["gate"]["checks"]) == ["test", "typecheck"])
check("status pending", p["status"] == "pending", p["status"])
r = pipe.approve(p["id"])
check("采纳成功并写入工作区", r["ok"] and r["result"]["status"] == "ok")
body = (repo / "src" / "ProcessList.tsx").read_text(encoding="utf-8")
check("只改了这一行（items?.name）", "props.items?.name" in body and "// mock file" in body
      and "return <div>" in body, repr(body))
check("采纳不产生 commit", "[AI-proposal]" not in git(str(repo), "log", "--oneline")
      and git(str(repo), "log", "--oneline").count("\n") == 0)
check("改动以未提交形式存在", "M" in git(str(repo), "status", "--porcelain"))
u = pipe.undo(p["id"])
check("撤销采纳成功", u["ok"], json.dumps(u, ensure_ascii=False)[:160])
check("撤销后文件复原", "props.items.name" in (repo / "src" / "ProcessList.tsx").read_text(encoding="utf-8"))

# ---------------------------------------------------------------- scenario 2
print("\n[2] 闸门失败 —— 提前拦截 + 强制采纳 + 写盘后回滚")
repo = make_repo(pkg={"scripts": {"type-check": "node -e \"console.error('TS2345: bad');process.exit(2)\""}})
pipe = AIDefectFixerPipeline(config_for(repo))
p = propose(pipe)
check("gate level = package_json", p["gate"]["level"] == "package_json")
check("提案标为 gate_failed", p["status"] == "gate_failed", p["status"])
check("失败命令记录", p["gate"]["failed_checks"] == ["typecheck"], str(p["gate"]["failed_checks"]))
r = pipe.approve(p["id"])
check("未强制时拒绝执行", not r["ok"] and "强制采纳" in r["error"], r.get("error", ""))
check("未产生 commit", git(str(repo), "log", "--oneline").count("\n") == 0)
check("文件未被改动", "props.items.name" in (repo / "src" / "ProcessList.tsx").read_text(encoding="utf-8"))
rf = pipe.approve(p["id"], force_gate=True)
check("强制采纳后写入工作区", rf["ok"], json.dumps(rf, ensure_ascii=False)[:200])
check("结果标了 forced", rf["result"].get("forced") is True)

# ---------------------------------------------------------------- scenario 3
print("\n[3] 提案漂移 —— 生成后有人改了同一个文件")
repo = make_repo()
pipe = AIDefectFixerPipeline(config_for(repo))
p = propose(pipe)
target = repo / "src" / "ProcessList.tsx"
# Someone else commits an unrelated edit on top: the SEARCH text still matches, but
# the diff we computed at proposal time no longer describes reality.
target.write_text("// touched by someone else\n"
                  + target.read_text(encoding="utf-8"), encoding="utf-8")
git(str(repo), "add", "-A")
git(str(repo), "commit", "-m", "unrelated edit")
r = pipe.approve(p["id"])
check("漂移被拦截", not r["ok"] and r.get("retryable") and "diff" in r.get("error", ""),
      r.get("error", "")[:90])
check("状态仍可复审", pipe.proposals.get(p["id"])["status"] == "pending")
check("漂移拒绝时不产生 commit", git(str(repo), "log", "--oneline").count("\n") == 1)

# ---------------------------------------------------------------- scenario 4
print("\n[4] 脏工作区 —— 采纳只写目标文件，不影响 WIP，也不 commit")
repo4 = make_repo()
pipe4 = AIDefectFixerPipeline(config_for(repo4))
(repo4 / "src" / "Wip.tsx").write_text("export const wip = 1;\n", encoding="utf-8")
p4 = propose(pipe4)
r = pipe4.approve(p4["id"])
check("脏区也能采纳（只写目标文件）", r["ok"], json.dumps(r, ensure_ascii=False)[:160])
check("AI 改动已写入", "props.items?.name" in (repo4 / "src" / "ProcessList.tsx").read_text(encoding="utf-8"))
check("WIP 文件未被触碰", (repo4 / "src" / "Wip.tsx").read_text(encoding="utf-8") == "export const wip = 1;\n")
check("脏区采纳不产生 commit", git(str(repo4), "log", "--oneline").count("\n") == 0)
u4 = pipe4.undo(p4["id"])
check("脏区采纳也能撤销", u4["ok"], json.dumps(u4, ensure_ascii=False)[:160])
check("撤销后 AI 改动还原", "props.items.name" in (repo4 / "src" / "ProcessList.tsx").read_text(encoding="utf-8"))
check("撤销后 WIP 仍在", (repo4 / "src" / "Wip.tsx").read_text(encoding="utf-8") == "export const wip = 1;\n")

# ---------------------------------------------------------------- scenario 5
print("\n[5] 大文件窗口化 —— 只喂报错附近代码，补丁仍能精确命中")
repo = make_repo(big=True)
pipe = AIDefectFixerPipeline(config_for(repo))
p = propose(pipe)
trunc = p["analysis"]["truncation"]
check("给出截断说明", any("仅提供" in t for t in trunc), str(trunc)[:120])
fed = p["analysis"]["read_files"]
check("读了嫌疑文件", len(fed) >= 1, str(fed))
check("补丁 ok", p["patch"]["ok"], str(p["patch"]["errors"])[:160])
check("diff 很小", len(p["patch"]["changes"][0]["diff"].split("\n")) <= 14,
      str(len(p["patch"]["changes"][0]["diff"].split("\n"))))
r = pipe.approve(p["id"])
check("大文件也能安全采纳", r["ok"], r.get("error", "")[:120] if not r["ok"] else "")
body = (repo / "src" / "ProcessList.tsx").read_text(encoding="utf-8")
check("1200 行前面的内容没丢", "const v0 = 0;" in body and "const v1199 = 1199;" in body)
check("后面的内容没丢", "const w299 = 299;" in body)
check("改动正确", "props.processItem?.template?.name" in body)

# ---------------------------------------------------------------- scenario 6
print("\n[6] 无 gate 配置且无 package.json —— 降级")
repo = make_repo(pkg=None)
pipe = AIDefectFixerPipeline(config_for(repo))
p = propose(pipe)
check("level degraded", p["gate"]["level"] == "degraded", p["gate"]["level"])
check("明确告知只是语法检查", any("人工复核" in n for n in p["gate"]["notes"]))
check("语法检查确实跑了", any(c["kind"] == "syntax" for c in p["gate"]["checks"]))

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
