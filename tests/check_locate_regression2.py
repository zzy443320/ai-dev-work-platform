# -*- coding: utf-8 -*-
"""回归：路由型工单定位（2026-09-23 第二轮修复）。

案例 A（L4Zo7p9zvc8sla0W，AI 运行日志时间 -8h）：
  描述含 admin/#/ai-skill-runtime-log，但 route_tokens 曾被
  _clean_path_tokens 的「必须含 /」检查全部丢弃 → path_tokens 为空 →
  读取窗口全给了 schedule 文件，真改动点在 ai-runtime-log 组件。

案例 B（rtsMFkQnKuUXld55，移动端个性化设置静默失败）：
  真改动点在 lui/mobile/components/project-detail/memory/，
  路径提示 api/api/runtime/ai/memory/update 的 memory 段必须赢得 tier-0。

用法：python tests/check_locate_regression2.py（需 ui_settings.json 指向一个 `packages/` 结构的前端仓库；
      没有 ui_settings.json 或指向别的仓库时会明确 SKIP，不会报一堆假红）
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyzer import (  # noqa: E402
    DefectAnalyzer, detect_non_frontend, strip_html,
)
from repo_guard import require_frontend_monorepo  # noqa: E402

def _proposal_path(name: str) -> Path:
    """优先用本地 proposals/（含真实分析数据），缺失时回退到脱敏夹具。

    回退是给「新克隆」用的：proposals/ 属于私有业务数据、已被 .gitignore 排除，
    没有回退的话这个用例在别人的机器上必然 SKIP 或报错。
    """
    local = PROJECT_ROOT / "proposals" / name
    if local.exists():
        return local
    return PROJECT_ROOT / "tests" / "fixtures" / "proposals" / name


CASE_A = _proposal_path("L4Zo7p9zvc8sla0W-20260923-104226.json")
CASE_B = _proposal_path("rtsMFkQnKuUXld55-20260922-185500.json")

checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def load_defect(path: Path):
    prop = json.loads(path.read_text(encoding="utf-8"))
    return prop.get("defect") or prop


def main() -> int:
    print("== 定位算法回归 2（路由型工单）==")
    settings_file = PROJECT_ROOT / "ui_settings.json"
    if not settings_file.exists():
        print("  [SKIP] 无 ui_settings.json（未配置目标仓库），本测试需要真实目标仓库")
        return 0
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    repo = settings.get("repo", {}).get("path", "")
    if not require_frontend_monorepo(repo, what="本次定位回归 2"):
        return 0

    a = DefectAnalyzer(repo, None, precedent_dir=str(PROJECT_ROOT / "proposals"))

    # ── 案例 A：hash 路由必须转化为 path_tokens ──
    defect = load_defect(CASE_A)
    text = f"{defect.get('title', '')}\n{strip_html(defect.get('description', ''))}"
    kws, ptoks = a._extract_keywords(text)
    check("hash 路由 ai-skill-runtime-log 进入 path_tokens（不再被斜杠检查丢弃）",
          "ai-skill-runtime-log" in ptoks, str(ptoks))
    check("测试账号/分支名不再进入关键词（噪音过滤）",
          not any(k.lower() in {"demo", "acme", "admin", "develop"} for k in kws),
          str(kws[:8]))
    check("32 位 hex Trace 残段不再进入关键词",
          not any(len(k) >= 16 and k.isalnum() for k in kws))
    a2 = DefectAnalyzer(repo, None, precedent_dir=str(PROJECT_ROOT / "proposals"))
    a2._wants_validation = any(t in text.lower() for t in ("必填", "校验"))
    files = a2._scan_repo(kws, ptoks)
    check("ai-runtime-log 组件目录文件占据嫌疑列表前排",
          sum(1 for f in files[:4] if "/ai-runtime-log/" in f) >= 2, str(files[:4]))
    snips, _meta = a2._read_contexts(files, kws)
    check("读取窗口包含 ai-runtime-log 渲染组件（而非 schedule 文件）",
          any("/ai-runtime-log/" in f for f in snips)
          and not any("pages/schedule/" in f for f in snips), str(list(snips)))

    # ── 案例 B：目录段投票兜底不被路由 token 干扰 ──
    defect_b = load_defect(CASE_B)
    text_b = f"{defect_b.get('title', '')}\n{strip_html(defect_b.get('description', ''))}"
    kws_b, ptoks_b = a._extract_keywords(text_b)
    files_b = a._scan_repo(kws_b, ptoks_b)
    snips_b, _meta_b = a._read_contexts(files_b, kws_b)
    check("memory 目录组件进入读取窗口（memory-modal-content.vue）",
          any("memory-modal-content" in f for f in snips_b), str(list(snips_b)))

    # ── 二次定位：模型回答里的路由与裸文件名 ──
    rc = ("根因在 admin/#/ai-skill-runtime-log 列表渲染，具体见 "
          "runtime-log-table.vue 中触发时间的格式化")
    hints = a._model_hints({"root_cause": rc}, ptoks)
    check("_model_hints 能从模型回答解析出路由目录提示",
          any("runtime-log" in h for h in hints), str(hints))
    check("_model_hints 能抽出裸文件名（无斜杠的 .vue）",
          any(h.endswith(".vue") and "/" not in h for h in hints), str(hints))
    resolved = a._resolve_file_hint("runtime-log-table.vue")
    check("裸文件名可解析为仓库内真实路径",
          resolved.endswith("runtime-log-table.vue"), resolved)

    # ── 锚点不落在 import 区 ──
    big = "packages/cloudpivot-ai/ai-platform/components/ai-runtime-log/index.vue"
    full_text, _note = a._read_full(big)
    if full_text and len(full_text) > a.file_cap_bytes:
        lines = full_text.split("\n")
        anchor = a._anchor_line(lines, kws)
        seg = "\n".join(lines[max(0, anchor - 2):anchor + 2])
        check("无 ident 命中时锚点不落在 import 行", not seg.lstrip().startswith("import"),
              f"line {anchor + 1}")

    # ── 非前端缺陷识别（工单 214162 的真实模型原话）──
    real_backend_rc = (
        "现有证据不足以在前端给出确定的最小改动：如果是 common.ts 中 SKILL 分支把 "
        "triggerUser/triggerMode 映射成了错误字段名或直接丢弃，需要在 common.ts 里改映射；"
        "但从抓包看参数名、值已原样发出且其它 logType 正常，说明前端链路无误，"
        "问题落在 /api/api/ai/agent/logs/query 对 logType=SKILL 的条件匹配与字段存储上。"
        "因此不修改任何前端文件可避免把后端缺陷掩盖成前端脏补丁。")
    check("识别「根因在后端」的自由措辞结论（214162 真实回答）",
          detect_non_frontend({"root_cause": real_backend_rc}, 0))
    check("有补丁时不得判为非前端缺陷",
          not detect_non_frontend({"root_cause": real_backend_rc}, 2))
    check("窗口不足的前端失败不得误判为后端缺陷",
          not detect_non_frontend({"root_cause": (
              "四个嫌疑文件窗口中均不含定时任务「不重复」表单逻辑，真正的缺陷点"
              "（重复类型切换为 ONCE 时把 startTime 归一化的 watch 处理器）"
              "不在提供的代码窗口内。")}, 0))
    check("仅顺带提一次「后端」不得判为非前端缺陷",
          not detect_non_frontend({"explanation": (
              "接口返回的数据在前端未做兜底，后端字段缺失时应在前端补齐默认值，"
              "改动点在该组件的 valueGetter。")}, 0))

    failed = [c for c in checks if not c[1]]
    print(f"\n== {len(checks) - len(failed)}/{len(checks)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
