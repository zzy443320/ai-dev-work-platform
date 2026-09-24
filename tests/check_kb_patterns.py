# -*- coding: utf-8 -*-
"""回归：知识库的模式聚合层（2026-09-23 新增）。

背景：知识库原来只有「一缺陷一卡」，工单越跑越多却沉淀不出「同类缺陷的
共性根因 / 标准改法」。修复方案是在缺陷卡之上派生一层**模式卡**
（scripts/kb_patterns.py），把同类缺陷聚成一个「篮子」，复发次数随之累加。

用真实知识库副本验证：
1. 静默失败族必须把 3 个不同模块的案例聚在一起（模块不同也不许拆散，
   否则模式全变成只出现一次的碎片）；
2. 族内 ≥2 例共享同一模块时，单独成模式（保留「同模块反复出缺陷」信号）；
3. 模式卡的「共性根因 / 处理方式 / 典型案例 / 复发提示」四节齐全，
   且共性根因区分「已验证（applied）」与「待验证」；
4. 重建幂等：连跑两次文件集合与内容一致，旧模式文件会被清掉；
5. 未归类兜底：词表没覆盖的症状不能被硬塞进某个族。

用法：python tests/check_kb_patterns.py（纯离线，不需要服务/目标仓库）
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.kb import KnowledgeBase  # noqa: E402
from scripts.kb_patterns import (  # noqa: E402
    collect_cards, detect_symptom, rebuild_patterns,
)

SRC_KB = PROJECT_ROOT / "knowledge_base"

checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _patterns_of(kb: KnowledgeBase):
    """跑一次完整重建，返回 {pattern_id: meta}。"""
    res = kb.rebuild()  # rebuild 内部会调 kb_patterns.rebuild_patterns
    assert not res.get("pattern_error"), res.get("pattern_error")
    return {p["id"]: p for p in res["patterns"]["list"]}


def _find(metas, defect_id):
    return [m for m in metas.values() if defect_id in m["defects"]]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kb_patterns_"))
    try:
        dst = tmp / "knowledge_base"
        shutil.copytree(SRC_KB, dst)
        kb = KnowledgeBase(str(dst))

        metas = _patterns_of(kb)
        check("重建产出模式卡且无错误", len(metas) > 0, f"{len(metas)} 个模式")

        # ── 1. 跨模块的同类症状必须聚成一族 ──
        silent = [m for m in metas.values() if m["symptom"] == "silent_fail"]
        check("静默失败族只生成一个模式（不同模块不拆散）", len(silent) == 1,
              f"{len(silent)} 个")
        if silent:
            ids = set(silent[0]["defects"])
            want = {"L4Zo7p9zmcaP8ZoF", "L4Zo7p9zF8rXm5CU", "rtsMFkQnKuUXld55"}
            check("静默失败族覆盖 3 个案例（导入静默/时间回退/超长提交）",
                  want <= ids, "、".join(sorted(want & ids)))
            check("静默失败族标出复发次数 ≥2 且带已采纳案例",
                  silent[0]["recurrence"] >= 2 and silent[0]["applied"] >= 1,
                  f"复发 {silent[0]['recurrence']} / 已采纳 {silent[0]['applied']}")

        # ── 2. 同族同模块 → 单独成模式 ──
        val = [m for m in metas.values() if m["symptom"] == "validate_missing"]
        check("校验缺失族按模块切出 schedule 子模式",
              any("schedule" in (m["module"] or "") for m in val),
              " | ".join(m["module"] or "-" for m in val))
        same = [p for p in metas.values() if "L4Zo7p9z4ehvrm44" in p["defects"]]
        check("同一处缺陷的不同工单（含验证用假工单）归入同一模式",
              same and "STREAM-TEST-1" in same[0]["defects"],
              "、".join(same[0]["defects"]) if same else "未命中")

        # ── 3. 模式卡结构 ──
        for pid, m in metas.items():
            body = (dst / "_patterns" / f"{pid}.md").read_text(encoding="utf-8")
            if not all(sec in body for sec in
                       ("## 这类缺陷长什么样", "## 共性根因", "## 处理方式",
                        "## 典型案例", "## 复发提示")):
                check(f"模式卡结构完整：{pid}", False, "缺章节")
                break
            if m["applied"] and "✅ **已验证**" not in body:
                check(f"有已采纳案例时必须标出已验证根因：{pid}", False)
                break
            if not m["applied"] and "尚无已采纳案例" not in body:
                check(f"无已采纳案例时必须标明结论未经证实：{pid}", False)
                break
            if "## 共性根因" in body and m["recurrence"] >= 2 \
                    and "复发 **%d** 次" % m["recurrence"] not in body:
                check(f"复发次数要写进卡片：{pid}", False)
                break
        else:
            check(f"全部 {len(metas)} 张模式卡的章节与结论标注正确", True)

        # 未经验证的根因不许伪装成结论
        unverified = [m for m in metas.values() if m["applied"] == 0]
        check("存在无采纳案例的模式，且其卡片标为未验证",
              all("尚无已采纳案例" in (dst / "_patterns" / f"{m['id']}.md")
                  .read_text(encoding="utf-8") for m in unverified),
              f"{len(unverified)} 个")

        # ── 4. 幂等 + 清理旧模式 ──
        first = {p.name: p.read_text(encoding="utf-8")
                 for p in sorted((dst / "_patterns").glob("*.md"))}
        stale = dst / "_patterns" / "zz_stale_pattern.md"
        stale.write_text("旧模式，应被清理", encoding="utf-8")
        metas2 = _patterns_of(kb)
        second = {p.name: p.read_text(encoding="utf-8")
                  for p in sorted((dst / "_patterns").glob("*.md"))}
        check("重建幂等：模式集合与文件名一致",
              set(first) == set(second), f"{len(first)} → {len(second)}")
        # updated 每轮都变，只比较正文部分
        same_body = all(
            first[k].split("updated:", 1)[-1].split("\n", 1)[-1]
            == second[k].split("updated:", 1)[-1].split("\n", 1)[-1]
            for k in first
        )
        check("重建幂等：模式卡正文逐字稳定（聚类不漂移）", same_body)
        check("不在当前聚类里的旧模式文件被清掉", not stale.exists())
        check("_meta.json 与模式卡同步", json.loads(
            (dst / "_patterns" / "_meta.json").read_text(encoding="utf-8")
        )["patterns"].__len__() == len(metas2))

        # 模式卡不能污染缺陷卡列表（_ 开头目录必须被跳过）
        cards = [c for c in kb._cards() if c.get("category", "").startswith("_")]
        check("模式目录不进入缺陷卡列表", not cards,
              f"杂项 {len(cards)}")

        # ── 5. 兜底与边界 ──
        s = detect_symptom("随便一个标题", "看不出症状的描述", "")
        check("词表外的症状归入「未归类」而非硬塞", s["fam"] == "unclassified",
              s["fam"])
        s2 = detect_symptom("提交后列表不刷新", "", "")
        check("标题命中即判族（权重高于正文）", s2["fam"] == "stale_data", s2["fam"])
        s3 = detect_symptom("普通标题", "仅在根因里顺带提了一句校验", "")
        check("正文单次弱命中不足以成族", s3["fam"] == "unclassified", s3["fam"])
        check("空知识库不报错", rebuild_patterns(tmp / "empty_kb") == [])

        failed = [c for c in checks if not c[1]]
        print(f"\n== {len(checks) - len(failed)}/{len(checks)} 通过 ==")
        for name, _, detail in failed:
            print(f"  FAIL: {name} {detail}")
        return 1 if failed else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
