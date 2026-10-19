# -*- coding: utf-8 -*-
"""需求开发流水线（scripts.task_modules.run_req）的纯离线用例。

背景：第一版需求开发只做「分诊 + 影响面」，用户的原话是「我想要的是能把整个
需求做完」。这一版改成四阶段流水线（分诊 → 定位 → 逐文件改动 → 自检），这里用
假 AI 把每一段行为钉死，防止改回去：

  1. 完整链路：产出可采纳 files，stages 四段全 done，自检结论跟随；
  2. 锚点匹配失败自动重试：第一次 find 对不上，失败原因回灌后第二次成功；
  3. 定位给的路径在仓库里不存在 → 进 gaps，绝不产出指向不存在文件的补丁；
  4. 「已存在却被当成新文件」→ 转成在原文件上改（edit），不整份覆盖；
  5. deliver=triage → 只出方案，后三个阶段 skipped、没有任何 files。

纯离线：临时目录当仓库、假 AI 不发请求，不依赖 git（_repo_files 走目录遍历兜底）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.task_modules import run_req  # noqa: E402

FAIL: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}]  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAIL.append(name)


class FakeAI:
    """按 system 提示词里的关键短语路由到对应阶段的假响应。"""

    mode = "fake"

    def __init__(self, *, edit_responses=None, locate=None, check=None, triage=None):
        self.edit_responses = list(edit_responses or [])
        self.locate = locate or {}
        self.check = check or {}
        self.triage = triage or {}
        self.edit_calls = 0

    def complete(self, prompt, system="", max_tokens=None, on_delta=None):
        if "只做一件事" in system:                      # locate
            return self.locate
        if "只允许改一个文件" in system:                 # implement（改文件）
            resp = (self.edit_responses[self.edit_calls]
                    if self.edit_calls < len(self.edit_responses)
                    else self.edit_responses[-1])
            self.edit_calls += 1
            return resp
        if "新写一个文件" in system:                     # implement（新文件）
            return {"path": "", "content": "", "notes": ""}
        if "复核工程师" in system:                       # selfcheck
            return self.check
        return self.triage                               # triage


def _triage(change_type="logic", needs_design=False):
    return {"summary": "改常量", "change_type": change_type,
            "needs_design": needs_design, "design_brief": "",
            "plan": "1. 改常量",
            "impacts": [{"path": "src/a.ts", "reason": "常量", "change": "modify"}],
            "endpoints": [], "search_terms": ["export const a"],
            "acceptance": ["a=42"], "checklist": ["确认引用"]}


def _repo():
    root = Path(tempfile.mkdtemp(prefix="req-pipe-"))
    (root / "src").mkdir()
    (root / "src" / "a.ts").write_text(
        "export const a = 1\nexport const b = 2\nexport function hi() { return a; }\n",
        encoding="utf-8")
    return root


def test_full_pipeline():
    print("── 1. 完整流水线：分诊 → 定位 → 改动 → 自检")
    root = _repo()
    ai = FakeAI(
        locate={"targets": [{"path": "src/a.ts", "why": "改常量",
                             "anchors": [{"find": "export const a = 1", "what": "常量"}]}],
                "new_files": [], "verify": ["npm run build"], "gaps": []},
        edit_responses=[{"path": "src/a.ts",
                         "edits": [{"find": "export const a = 1",
                                    "replace": "export const a = 42"}],
                         "notes": "改常量"}],
        check={"verdict": "done", "problems": [], "followups": ["跑构建"],
               "checklist": ["确认引用处"]},
        triage=_triage(),
    )
    out = run_req(ai, {"requirement_doc": "把 a 改成 42", "repo_root": str(root)})
    check("四个阶段全部 done",
          [s["status"] for s in out["stages"]] == ["done"] * 4,
          str([(s["key"], s["status"]) for s in out["stages"]]))
    check("产出一个可采纳 edit 文件",
          len(out["files"]) == 1 and out["files"][0]["action"] == "edit",
          str([(f["path"], f["action"]) for f in out["files"]]))
    check("改动带 +/- 行数统计",
          out["files"][0].get("stats", {}).get("added") == 1,
          str(out["files"][0].get("stats")))
    check("自检结论=done 且不被未完成项降级", out["selfcheck"]["verdict"] == "done")
    check("验收标准进了 checklist",
          "a=42" in out["checklist"], str(out["checklist"]))
    check("verify 命令透出", "npm run build" in out["verify"], str(out["verify"]))
    check("changed=True（界面据此切换可采纳文案）", out["changed"] is True)


def test_retry_on_bad_anchor():
    print("── 2. 锚点匹配失败 → 回灌原因自动重试")
    root = _repo()
    ai = FakeAI(
        locate={"targets": [{"path": "src/a.ts", "why": "改常量", "anchors": []}],
                "new_files": [], "verify": [], "gaps": []},
        edit_responses=[
            {"path": "src/a.ts",
             "edits": [{"find": "这段代码在文件里根本不存在", "replace": "x"}]},
            {"path": "src/a.ts",
             "edits": [{"find": "export const a = 1", "replace": "export const a = 42"}]},
        ],
        check={"verdict": "done", "problems": [], "followups": [], "checklist": []},
        triage=_triage(),
    )
    out = run_req(ai, {"requirement_doc": "把 a 改成 42", "repo_root": str(root)})
    check("重试后改动落进产出物",
          len(out["files"]) == 1 and out["files"][0]["edits"][0]["find"] == "export const a = 1",
          str([(f["path"], f.get("edits")) for f in out["files"]]))
    check("模型被调用了两次（失败原因确实回灌了）", ai.edit_calls == 2, str(ai.edit_calls))
    check("fuzzy/exact 标记是 exact", out["files"][0].get("match") == "exact")


def test_ghost_path_goes_to_gaps():
    print("── 3. 定位到不存在的文件 → 进 gaps，不产出补丁")
    root = _repo()
    ai = FakeAI(
        locate={"targets": [{"path": "src/ghost/never.ts", "why": "幻觉路径",
                             "anchors": []},
                            {"path": "src/a.ts", "why": "真文件", "anchors": []}],
                "new_files": [], "verify": [], "gaps": []},
        edit_responses=[{"path": "src/a.ts",
                         "edits": [{"find": "export const a = 1",
                                    "replace": "export const a = 42"}]}],
        check={"verdict": "done", "problems": [], "followups": [], "checklist": []},
        triage=_triage(),
    )
    out = run_req(ai, {"requirement_doc": "改 a", "repo_root": str(root)})
    check("幻觉路径没有变成补丁",
          all(f["path"] != "src/ghost/never.ts" for f in out["files"]),
          str([f["path"] for f in out["files"]]))
    check("幻觉路径记录进 gaps",
          any("never.ts" in g for g in out["gaps"]), str(out["gaps"]))
    check("真文件照常产出", any(f["path"] == "src/a.ts" for f in out["files"]))


def test_existing_newfile_becomes_edit():
    print("── 4. 「新文件」其实已存在 → 转成在原文件上改")
    root = _repo()
    ai = FakeAI(
        locate={"targets": [],
                "new_files": [{"path": "src/a.ts", "why": "以为要新建"}],
                "verify": [], "gaps": []},
        edit_responses=[{"path": "src/a.ts",
                         "edits": [{"find": "export const a = 1",
                                    "replace": "export const a = 42"}]}],
        check={"verdict": "done", "problems": [], "followups": [], "checklist": []},
        triage=_triage(),
    )
    out = run_req(ai, {"requirement_doc": "改 a", "repo_root": str(root)})
    check("已存在的文件按 edit 处理（不整份覆盖）",
          len(out["files"]) == 1 and out["files"][0]["action"] == "edit",
          str([(f["path"], f["action"]) for f in out["files"]]))


def test_triage_only():
    print("── 5. deliver=triage：只出方案，不动代码")
    root = _repo()
    ai = FakeAI(triage=_triage())
    out = run_req(ai, {"requirement_doc": "改 a", "repo_root": str(root),
                       "deliver": "triage"})
    check("没有任何 files", out["files"] == [], str(out["files"]))
    check("后三个阶段 skipped",
          [s["status"] for s in out["stages"]] == ["done", "skipped", "skipped", "skipped"],
          str([(s["key"], s["status"]) for s in out["stages"]]))
    check("gaps 说明如何继续",
          any("自动完成" in g for g in out["gaps"]), str(out["gaps"]))


def main() -> int:
    test_full_pipeline()
    test_retry_on_bad_anchor()
    test_ghost_path_goes_to_gaps()
    test_existing_newfile_becomes_edit()
    test_triage_only()
    print()
    if FAIL:
        print(f"FAIL: {len(FAIL)} 项失败: {FAIL}")
        return 1
    print("PASS: 需求开发流水线（分诊/定位/改动/自检/交付方式）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
