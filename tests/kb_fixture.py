# -*- coding: utf-8 -*-
"""脱敏的知识库夹具：自检用例的**唯一**数据来源，不再对着生产知识库断言。

背景：`check_kb_patterns.py` / `check_kb_render.py` / `check_kb_patterns_ui.py`
原来直接读项目根的 `knowledge_base/`（真实工单 + 真实分析结论，已被 .gitignore 排除），
还断言了真实工单号 `L4Zo7p9z4ehvrm44`、`STREAM-TEST-1` 这些字面量。后果有两个：
别人克隆下来这三个用例必然红；而为了保住它们，生产知识库里那两条测试用假工单
也一直不敢删（README「已知限制」里记着这笔）。

这里用与生产**同一条代码路径**（`KnowledgeBase.record`）造一批假工单卡，
覆盖模式聚类要验证的全部形态：

  * 静默失败族：3 个**不同模块**的同类症状 → 必须聚成 1 个模式（不许按模块拆散）；
  * 校验缺失族：同一模块（schedule）出现 2 次 → 单独切出子模式；
  * 同一处缺陷的两个工单 → 必须落进同一个模式；
  * 已采纳 / 待验证两种状态都要有（模式卡要区分「已验证根因」与「结论未证实」）。

工单号统一用 `DEMO-` 前缀、路径统一用 `src/modules/...`，不含任何真实项目信息。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.kb import KnowledgeBase  # noqa: E402

_UPDATED = "2026-01-01T10:00:00"


def _card(defect_id: str, title: str, description: str, category: str,
          root_cause: str, files: List[str], status: str = "pending",
          patch: str = "", diff: str = "") -> Dict:
    """组装一条「缺陷 + 分析 + 提案」，字段形状与生产落盘的一致。"""
    changes = [{"file_path": p, "applied_blocks": 1, "stats": {"added": 2, "removed": 1},
                "warnings": []} for p in files]
    return {
        "defect": {"id": defect_id, "title": title, "description": description,
                   "priority": "P2", "repro_steps": ["打开页面", "按描述操作", "观察结果"]},
        "analysis": {"category": category, "root_cause": root_cause,
                     "explanation": f"（脱敏夹具）{title} 的补充说明。",
                     "patch_canonical": patch or f"<<<<<<< SEARCH {files[0]}\nold\n=======\nnew\n>>>>>>> REPLACE",
                     "prevention": "落库前统一走一次校验，失败必须有可见反馈。",
                     "suspect_files": files, "keywords": ["demo", "fixture"],
                     "ai_mode": "mock"},
        "proposal": {"id": f"{defect_id}-20260101-100000", "created": _UPDATED,
                     "status": status,
                     "apply": {"sha": "deadbeef" if status == "applied" else ""},
                     "gate": {"level": "degraded", "ok": status == "applied",
                              "checks": [], "failed_checks": []},
                     "verify": {"status": "skipped", "reason": "夹具不跑页面验证"},
                     "patch": {"changes": changes,
                               "combined_diff": diff or f"--- a/{files[0]}\n+++ b/{files[0]}\n-old\n+new"}},
    }


# ── 1. 静默失败族：三个不同模块（import / schedule / approval）──
SILENT_FAIL = [
    _card(
        "DEMO-1001", "导入附件后没有任何提示，实际一条都没进来",
        "<p>【现象】</p><p>1、在导入弹窗选择附件</p><p>2、点击导入</p>"
        "<ul><li>弹窗直接关闭</li><li>列表里什么都没有</li><li>没有任何提示</li></ul>",
        "逻辑",
        "ImportDialog.vue 的 catch 分支只 console.error 不上报，异常被吞掉后静默关闭弹窗。",
        ["src/modules/import/ImportDialog.vue"], status="applied",
        diff="--- a/src/modules/import/ImportDialog.vue\n"
             "+++ b/src/modules/import/ImportDialog.vue\n"
             "-  catch (e) { console.error(e); }\n"
             "+  catch (e) { console.error(e); message.error('导入失败：' + e.message); }",
    ),
    _card(
        "DEMO-1002", "保存后时间回退到 08:00，界面无任何提示",
        "<p>【现象】</p><p>1、打开日程页</p><p>2、只填日期不填时间</p><p>3、保存并重开</p>"
        "<ul><li>时间变成 08:00</li><li>没有任何反馈，用户以为保存成功</li></ul>",
        "逻辑",
        "ScheduleForm 在时间未填时用 `time || '08:00'` 兜底，改动被静默接受，用户感知不到数据被替换。",
        ["src/modules/schedule/ScheduleForm.vue"], status="pending",
    ),
    _card(
        "DEMO-1003", "提交后没有任何提示，任务实际没有触发",
        "<p>【现象】</p><p>提交审批单时长度超出上限，接口返回成功但任务未触发，"
        "界面没有任何提示。</p>",
        "UI",
        "ApprovalSubmit 直接提交，超出长度时下游丢弃请求，前端无反馈，用户以为已提交。",
        ["src/modules/approval/ApprovalSubmit.vue"], status="applied",
    ),
]

# ── 2. 校验缺失族：schedule 模块出现 2 次（含「同一缺陷的两个工单」）──
VALIDATE = [
    _card(
        "DEMO-2001", "重复周期应该必填，空着也能提交",
        "日程组件的重复周期字段没有校验必填，终止时间勾选了也应该必填。",
        "逻辑",
        "重复周期字段缺少必填校验，空值时直接提交，下游按默认值处理导致日程不生效。",
        ["src/modules/schedule/RecurrenceForm.vue"], status="pending",
    ),
    _card(
        "DEMO-2001-RETRY", "重复周期应该必填，空着也能提交",
        "同一处缺陷的第二次工单：日程组件的重复周期字段没有校验必填，终止时间勾选了也应该必填。",
        "逻辑",
        "重复周期字段缺少必填校验，空值时直接提交；第二次工单确认了根因并补上了拦截。",
        ["src/modules/schedule/RecurrenceForm.vue", "src/modules/schedule/api.ts"],
        status="pending",
    ),
    _card(
        "DEMO-2003", "起止日期未校验，非法区间也能保存",
        "草稿里开始时间晚于结束时间时没有拦截，非法值直接落库。",
        "接口",
        "DraftForm 只校验了空值，没有校验区间顺序，非法区间被原样提交。",
        ["src/modules/draft/DraftForm.vue"], status="pending",
    ),
]

CASES: List[Dict] = SILENT_FAIL + VALIDATE


def build(dst, cases: Optional[List[Dict]] = None) -> Path:
    """在 `dst` 生成一套夹具知识库（覆盖卡片 + 派生视图），返回目录。"""
    kb = KnowledgeBase(str(dst))
    for c in (cases if cases is not None else CASES):
        kb.record(c["defect"], c["analysis"], c["proposal"])
    return Path(dst)


def main() -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description="生成脱敏夹具知识库，打印目录路径")
    ap.add_argument("--dst", default=None, help="目标目录（默认临时目录，跑完保留）")
    args = ap.parse_args()
    dst = Path(args.dst) if args.dst else Path(tempfile.mkdtemp(prefix="kb_fixture_"))
    build(dst)
    print(f"[ok] 夹具知识库：{dst}")
    for c in CASES:
        print(f"     {c['defect']['id']:18} {c['analysis']['category']}  "
              f"{c['defect']['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
