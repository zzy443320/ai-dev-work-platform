# -*- coding: utf-8 -*-
"""门禁的「基线本来就红」判定（含 pipeline 集成）。

背景：41 份提案里有 7 份停在 `gate_failed`，而失败项在**未打补丁时**就是红的——仓库自身
环境问题（缺 @types、lint 配置原本就有错）。人看到的是"闸门没过"，实际是"仓库本来就不绿"，
于是本来可采纳的补丁被一道与它无关的闸门挡下，只能靠 UI 勾强制采纳。

这里的用例守住三件事：
  1. 基线就红的检查不计入失败（提案能正常进 pending）；
  2. **基线是绿的检查必须仍然拦人**（摘除 ≠ 放水）；
  3. 页面复验说"现象仍在"时，闸门照样不通过（这条是假安全，不能碰）。

用法：python tests/check_gate_baseline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.gate import apply_baseline  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402

_PASS = 0
_FAIL = 0


def ok(cond, label, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [ok]   {label}" + (f" — {str(detail)[:110]}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {str(detail)[:200]}" if detail else ""))


def _gate_of(agent, patch=None):
    """只测 _gate_of 这一个方法，不初始化整条流水线（它要配置、AI、网络）。"""
    obj = AIDefectFixerPipeline.__new__(AIDefectFixerPipeline)
    obj.repo_path = "."
    obj.gate_cfg = {}
    return obj._gate_of(agent, patch or {"ok": True, "files": ["a.ts"], "patched_contents": {}})


def _agent(conclusion="verified", checks=None, baseline=None):
    return {
        "conclusion": conclusion,
        "rounds": 3,
        "attempts": [{
            "round": 2, "built": True, "rank": 4,
            "checks": checks if checks is not None else {
                "typecheck": {"cmd": "tsc --noEmit", "ok": False, "returncode": 2,
                              "seconds": 3.0, "output": "error TS2304: Cannot find name"},
                "lint": {"cmd": "eslint .", "ok": True, "returncode": 0, "seconds": 1.0,
                         "output": ""},
            },
            "verification": {"regressions": [], "fixed": ["check"]},
        }],
        "baseline": baseline if baseline is not None else {
            "typecheck": {"ok": False, "returncode": 2, "seconds": 2.0, "output": "same error"},
        },
        "notes": [],
        "page_after": {},
    }


def t_apply_baseline_unit():
    print("[1] apply_baseline 的取舍")
    checks = [{"name": "typecheck", "ok": False}, {"name": "lint", "ok": False},
              {"name": "test", "ok": True}]
    base = {"typecheck": {"ok": False}, "lint": {"ok": True}}
    v = apply_baseline(checks, base)
    ok(v["preexisting"] == ["typecheck"], "基线就红的被摘出", str(v["preexisting"]))
    ok(v["failed"] == ["lint"], "基线是绿的仍然拦人", str(v["failed"]))
    ok(checks[0].get("preexisting") is True, "摘出的检查在 checks 里被标记（不是隐藏）")
    ok(not checks[1].get("preexisting"), "没被摘出的不加标记")
    ok(bool(v["notes"]) and "未打补丁" in v["notes"][0], "notes 里如实说明")

    ok(apply_baseline([{"name": "typecheck", "ok": False}], {})["failed"] == ["typecheck"],
       "没有基线时一律拦（不默认放水）")
    wrapped = apply_baseline([{"name": "balance:src/a.vue", "ok": False}],
                             {"balance:src/a.vue": {"ok": False}})
    ok(wrapped["preexisting"] == ["balance:src/a.vue"], "带 : 的检查名也能对上基线")
    ok(apply_baseline([{"name": "x", "ok": True}], {})["failed"] == [], "通过的检查不进失败")


def t_pipeline_gate():
    print("\n[2] pipeline 闸门：基线红不拦人")
    g = _gate_of(_agent())
    ok(g["ok"] is True, "闸门放行（提案能进 pending）", str(g["failed_checks"]))
    ok(g["failed_checks"] == [], "failed_checks 为空", str(g["failed_checks"]))
    row = [c for c in g["checks"] if c["name"] == "typecheck"][0]
    ok(row["preexisting"] is True, "被摘出的检查仍留在 checks 里并标 preexisting")
    ok(any("未打补丁" in n for n in g["notes"]), "notes 里写明为什么不算失败")

    print("\n[3] 但基线是绿的检查必须拦住")
    g2 = _gate_of(_agent(baseline={"typecheck": {"ok": True}}))
    ok(g2["ok"] is False, "闸门不通过")
    ok("typecheck" in g2["failed_checks"], "失败项被如实列出", str(g2["failed_checks"]))

    print("\n[4] 页面复验说现象仍在 → 照样不通过（假安全不许碰）")
    checks = {"typecheck": {"cmd": "tsc --noEmit", "ok": True, "returncode": 0,
                            "seconds": 1.0, "output": ""}}
    persists = {
        "conclusion": "symptom_persists", "rounds": 3, "notes": [], "baseline": {},
        "page_after": {"evidence": "重新截图中「选不了模型」仍在"},
        "attempts": [{"round": 2, "built": True, "rank": 4, "checks": checks,
                      "verification": {}}],
    }
    g3 = _gate_of(persists)
    ok(g3["ok"] is False and "page-reverify" in g3["failed_checks"],
       "页面复验未过就拦住", str(g3["failed_checks"]))


def main():
    t_apply_baseline_unit()
    t_pipeline_gate()
    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
