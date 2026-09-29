# -*- coding: utf-8 -*-
"""回归：二次定位的文件提示预算（2026-09-29，工单 214872 SAP 适配器）。

模型按「宁缺勿猜」护栏拒出补丁时，在分析里点了 3 个文件提示：
  1. edit-method_table.vue   —— 第一轮已读过（跳过即可，不占预算）
  2. edit-method_sap.vue     —— 模型幻觉名，仓库里不存在（解析为空）
  3. method-extra-fields.vue —— 真改动点（SAP 结构体/表名字段应加在这里）

旧代码在解析前就 file_hints[:2] 截断：名额被 1、2 两条废提示占满，
第 3 条永远轮不到 → fresh 为空 → 第二轮 AI 不跑 → 提案 invalid。
同时正文里的「SingleObject/List」（返回值类型枚举复述，不是路径）会被
_resolve_dir_hint 尾缀匹配误解析到 docs/ai-coding/list/，烧掉目录预算。

用法：python tests/check_locate_regression3.py（需 ui_settings.json 指向一个
      `packages/` 结构的前端仓库；没有时明确 SKIP，不会报假红）
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyzer import (  # noqa: E402
    DefectAnalyzer, strip_html,
)
from repo_guard import require_frontend_monorepo  # noqa: E402

CASE_214872 = PROJECT_ROOT / "proposals" / "59t17wuy0RduU4cD-20260929-160250.json"
if not CASE_214872.exists():
    CASE_214872 = (PROJECT_ROOT / "tests" / "fixtures" / "proposals"
                   / "59t17wuy0RduU4cD-20260929-160250.json")

checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("== 定位算法回归 3（重试文件提示预算 / 214872）==")
    settings_file = PROJECT_ROOT / "ui_settings.json"
    if not settings_file.exists():
        print("  [SKIP] 无 ui_settings.json（未配置目标仓库），本测试需要真实目标仓库")
        return 0
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    repo = settings.get("repo", {}).get("path", "")
    if not require_frontend_monorepo(repo, what="本次定位回归 3"):
        return 0

    prop = json.loads(CASE_214872.read_text(encoding="utf-8"))
    ai_result = prop.get("analysis") or {}
    read_files = list(ai_result.get("read_files") or [])

    a = DefectAnalyzer(repo, None)

    # ── ① 伪路径过滤：SingleObject/List 是枚举复述，不是仓库路径 ──
    hints = a._model_hints(ai_result, read_files)
    check("正文里的 SingleObject/List 伪路径被过滤",
          "SingleObject/List" not in hints, str(hints))
    check("真提示 method-extra-fields.vue 仍被抽出",
          "method-extra-fields.vue" in hints)

    # ── ② 幻觉名解析为空，但不得挤掉后面的真提示 ──
    exts = {"vue", "ts", "tsx", "js", "jsx"}
    file_hints = [h for h in hints if h.rsplit(".", 1)[-1].lower() in exts]
    check("幻觉名 edit-method_sap.vue 解析失败", a._resolve_file_hint("edit-method_sap.vue") == "")

    snippets2 = {k: "x" for k in read_files}
    opened, opened_count = [], 0
    for fp in file_hints:
        if opened_count >= 2 or len(snippets2) >= a.max_files + 4:
            break
        resolved = a._resolve_file_hint(fp)
        if not resolved or resolved in snippets2:
            continue
        t, _ = a._read_one(resolved, [])
        if t is not None:
            snippets2[resolved] = t
            opened.append(resolved)
        opened_count += 1

    check("已读文件/幻觉名不占预算，method-extra-fields.vue 被强制开窗",
          any("method-extra-fields" in f for f in opened), str(opened))
    check("真提示解析到 modals/components/ 下的组件",
          any(f.endswith("/method-extra-fields.vue") for f in opened))

    # ── ③ 模拟重试链路终点：fresh 非空（第二轮 AI 会跑） ──
    fresh = [f for f in snippets2 if f not in read_files]
    check("fresh 非空 → 触发第二轮 AI 分析", bool(fresh), str(fresh))

    print()
    failed = [c for c in checks if not c[1]]
    print(f"== {len(checks) - len(failed)}/{len(checks)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
