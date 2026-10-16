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
    print("== 定位算法回归 3（重试文件提示预算 / 214872 + 215890）==")

    # ── ④ 带扩展名的完整文件路径不许被 [:8] 截掉（2026-10-16，工单 215890）──
    # 这一段是**纯函数**断言，不需要真实仓库，所以放在 SKIP 之前。
    # 旧实现把「正则 1（不带扩展名）」整体串在「正则 2（带扩展名）」之前，于是
    # 只要不带扩展名的候选攒够 8 条，带扩展名的完整路径**一条都留不下**：
    # 模型在 explanation 里写全了 cloudpivot-ai/chat/chat-kit/components/composer/
    # mobile/index.vue（真实落点 packages/…，718 行），抽出来的却是「点名文件 -」，
    # 真凶没进窗口 → 修复 agent 只能靠 grep 重找 → 轮次耗尽认输。
    noext = " ".join(f"packages/app/a{i}/b{i}/c-component" for i in range(10))
    ai_215890 = {
        "root_cause": noext + " lui/mobile/pages/schedule/schedule-editor",
        "explanation": ("缺陷只可能落在移动端 Composer（cloudpivot-ai/chat/chat-kit/"
                        "components/composer/mobile/index.vue）内部：其模型入口的显示条件、"
                        "向父组件派发的事件名，或模型弹层的层级。"),
        "prevention": "",
    }
    hints4 = DefectAnalyzer._model_hints(None, ai_215890, [])
    f4 = [h for h in hints4 if h.rsplit(".", 1)[-1].lower()
          in ("vue", "ts", "tsx", "js", "jsx")]
    check("10 条无扩展名候选之后，带扩展名的完整路径仍被抽出",
          any(h.endswith("composer/mobile/index.vue") for h in f4), str(hints4))
    check("文件提示排在提示表最前（先于任何目录提示）",
          bool(f4) and hints4 and hints4[0] == f4[0], str(hints4[:2]))
    check("同路径「去扩展名孪生」不再与文件提示抢预算",
          not any(h.endswith("composer/mobile/index") for h in hints4), str(hints4))
    # 邻居扩散：点名文件的「同目录 + 上一级目录」——215890 第 4 趟模型明说
    # 「缺陷取决于三份未被提供的文件」，而那三份正是它的同目录/上级目录邻居
    # （composer-mobile-toolbar.vue / composer.mixin.ts）。上一级必须给：同一功能
    # 模块常拆成 `mobile/` + 上一级的 `xxx.mixin.ts`。
    sd = DefectAnalyzer._sibling_dirs(
        "packages/x/chat/chat-kit/components/composer/mobile/index.vue")
    check("邻居目录取「同目录 + 上一级」（且在上一层就停）",
          sd == ["packages/x/chat/chat-kit/components/composer/mobile",
                 "packages/x/chat/chat-kit/components/composer"], str(sd))
    check("裸文件名/空串没有邻居目录（不许把仓库根当目录扫）",
          DefectAnalyzer._sibling_dirs("index.vue") == []
          and DefectAnalyzer._sibling_dirs("") == []
          and DefectAnalyzer._sibling_dirs("a/b.vue", up=1) == ["a"], "")

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

    # ── ⑥ 端到端：重试**没拿到补丁**时，强制开窗的文件也必须留在窗口里 ──
    # 2026-10-16（215890）：旧实现只在「重试拿到了补丁」时才提交 snippets2/fresh，
    # 重试没补丁就整批丢弃 —— 可「模型说『我得先看到那个文件』」正是触发重试的理由，
    # 这一路才是常态。后果是记录自相矛盾：说明里写着根因在 composer/mobile/index.vue，
    # 窗口里却全是 lui/**/schedule/*，修复 agent 只能自己 grep 重找、轮次耗尽认输。
    # 这里用真实 analyze() 跑完整链路（真仓库 + 桩模型），断言「文件真的进了 read_files」——
    # 只断言 _model_hints 抽出了路径是不够的，后半段（解析→强制开窗→提交窗口）才是死因。
    stub = {
        "root_cause": ai_result.get("root_cause") or "",
        "explanation": ai_result.get("explanation") or "",
        "prevention": "", "category": "其他",
        "patch_blocks": "", "fix_suggestion": "",
    }

    class _Stub:
        mode = "stub"
        model = "stub"

        def complete(self, prompt, system="", max_tokens=None, on_delta=None):
            return dict(stub)   # 永远不给补丁 —— 就是出问题的那条路

    defect = prop.get("defect") or {}
    out = DefectAnalyzer(repo, _Stub()).analyze(
        {"id": defect.get("id"), "title": defect.get("title"),
         "description": defect.get("description") or defect.get("desc") or ""})
    rf = out.get("read_files") or []
    check("重试未拿到补丁时，模型点名强制开窗的文件仍留在窗口里（read_files）",
          any("method-extra-fields" in f for f in rf), str(rf))
    check("窗口里仍保留第一轮的上下文（不是被替换掉）",
          any(f in rf for f in read_files) and len(rf) > len(read_files), str(len(rf)))

    print()
    failed = [c for c in checks if not c[1]]
    print(f"== {len(checks) - len(failed)}/{len(checks)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
