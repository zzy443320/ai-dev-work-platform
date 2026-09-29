# -*- coding: utf-8 -*-
"""回归：补丁生成三层兜底（2026-09-29，工单 214872 类失败的根治）。

覆盖：
1. ai_model._extract_json 的解析前清洗——DSML 训练模板泄漏 / <think> 标签
   不再破坏 JSON 提取与字段抢救；
2. analyzer._patch_only_fallback 第三轮「只出补丁」兜底——前两轮确认根因
   却拿不到合法补丁块时，定向重发一次填空式调用；
3. 失败路径——模型自述 cannot_patch 原因要透传给用户，不能闷头报 0 块。

用法：python tests/check_patch_fallback.py（纯桩测试，不需要目标仓库）
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ai_model import AIModel  # noqa: E402
from scripts.analyzer import DefectAnalyzer  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


VALID_PATCH = (
    "<<<<<<< SEARCH packages/a/src/fields.vue\n"
    "        entityTable: '',\n"
    "=======\n"
    "        structName: '',\n"
    "        entityTable: '',\n"
    ">>>>>>> REPLACE"
)


def _as_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class StubAI:
    """complete() 按脚本逐次返回；记录每次收到的 prompt。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def complete(self, prompt, max_tokens=0, on_delta=None, system=""):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def main() -> int:
    print("== 补丁兜底回归（patch-only 第三轮 / 噪音清洗）==")

    # ── ① 解析前清洗：DSML 泄漏不再破坏 JSON 提取 ──
    m = AIModel.__new__(AIModel)
    dirty = (_as_json({"root_cause": "ok", "patch_blocks": "X"})
             + "\n<｜｜DSML｜｜ calls>garbage</｜｜DSML｜｜ invoke>")
    out = m._extract_json(dirty)
    check("DSML 泄漏不破坏 JSON 提取", out.get("root_cause") == "ok"
          and out.get("patch_blocks") == "X", str(out)[:120])
    thinky = ("<think>hidden reasoning { not json }</think>\n"
              + _as_json({"root_cause": "r2", "patch_blocks": ""}))
    out2 = m._extract_json(thinky)
    check("<think> 块被剥掉、JSON 正常解析", out2.get("root_cause") == "r2"
          and not out2.get("error"), str(out2)[:120])
    clean = m._extract_json(_as_json({"root_cause": "r3"}))
    check("干净输出行为不变（无回归）", clean.get("root_cause") == "r3")

    # ── ② 第三轮兜底：成功路径 ──
    ana = DefectAnalyzer(repo_path=str(PROJECT_ROOT), ai_model=None)
    ai_result = {"root_cause": "SAP 适配器缺结构体/表名字段",
                 "explanation": "在 method-extra-fields.vue 按 returnType 分支"}
    stub = StubAI([{"patch_blocks": VALID_PATCH}])
    ana.ai = stub
    blocks, raw, note, errs = ana._patch_only_fallback(
        ai_result, {"packages/a/src/fields.vue": "content"})
    check("兜底调用拿到合法补丁块", bool(blocks), str(errs))
    check("补丁文本原样返回", raw == VALID_PATCH)
    check("note 标注成功来源", "成功" in note, note)
    check("prompt 含已确认根因", "结构体/表名字段" in stub.prompts[0])
    check("prompt 含文件窗口", "packages/a/src/fields.vue" in stub.prompts[0])
    check("prompt 明令禁止再分析", "不要重复分析" in stub.prompts[0]
          and "cannot_patch" in stub.prompts[0])

    # ── ③ 第三轮兜底：拒绝路径，cannot_patch 原因透传 ──
    stub2 = StubAI([{"patch_blocks": "",
                     "cannot_patch": "缺少 SAP 适配器模板文件"}])
    ana.ai = stub2
    blocks2, raw2, note2, errs2 = ana._patch_only_fallback(
        ai_result, {"packages/a/src/fields.vue": "content"})
    check("拒绝路径 blocks 为空", not blocks2)
    check("cannot_patch 原因进入 note", "缺少 SAP 适配器模板文件" in note2, note2)
    check("拒绝路径仍给出 parse_errors", bool(errs2), str(errs2))

    print()
    failed = [c for c in checks if not c[1]]
    print(f"== {len(checks) - len(failed)}/{len(checks)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
