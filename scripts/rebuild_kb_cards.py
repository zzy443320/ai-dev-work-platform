"""用存量提案重刷知识库卡片。

卡片正文格式升级后跑一次即可：从 proposals/*.json 反向重建每张卡片，
幂等（同一提案重刷结果一致）。INDEX.md / _stats.json 由 record() 顺带重建。

用法：python scripts/rebuild_kb_cards.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.kb import KnowledgeBase  # noqa: E402
from scripts.proposal import ProposalStore  # noqa: E402


def main() -> int:
    kb = KnowledgeBase(str(PROJECT_ROOT / "knowledge_base"))
    store = ProposalStore(str(PROJECT_ROOT / "proposals"))
    # 同一缺陷的多份提案共用一张卡片（按 defect_id 命名）：按时间升序重刷，
    # 最新的提案最后写入，最终卡片内容=最新提案。
    summaries = sorted(store.list(), key=lambda s: s.get("created") or "")
    ok = 0
    for s in summaries:
        full = store.get(s.get("id", ""))
        if not full:
            continue
        try:
            path = kb.record_from_proposal(full)
            ok += 1
            print(f"[ok] {s.get('id')} -> {path}")
        except Exception as e:
            print(f"[err] {s.get('id')}: {e}")
    print(f"\n重刷 {ok}/{len(summaries)} 张卡片")
    return 0 if ok == len(summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
