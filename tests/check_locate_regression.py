"""回归：工单 L4Zo7p9zF8rXm5CU（迭代106 定时任务「不重复」）定位失败复盘。

2026-09-22 19:13 真实运行把全部读取窗口给了 cloudpivot-form 的 form-* 文件，
真正改动点在 packages/cloudpivot-ai/lui/pages/schedule/，模型按「宁缺勿猜」
护栏拒出补丁 → block_count=0 → status=invalid（用户看到「无法生成补丁」）。

根因（均已修复，本测试逐项守住）：
1. path_tokens 无 ASCII 限制：中文短语（观察日期/时间控件的可编辑性）与账号
   （demo/Acme）混进 token，6 个名额被垃圾占满；
2. target_dirs 取 segments[-1]：URL 残段 …/ai/schedule/create 的末段 create
   抢走目录判定，schedule（被提及 3 次）反而失去 tier-0 资格；
3. _val_rank 作为排序第二位（在得分之前）：「校验」语境下全仓库 form-* 文件
   全局碾压非 form 文件，schedule/api.ts 拿到 4 个标识符 ×8 命中仍被埋掉；
4. hard[:22] 截断前未去重：idents/quoted 重复项吃满名额，path_tokens/segments
   从未进入 grep 关键词表；
5. 无历史先例信号：此前两次人工采纳的补丁都在 schedule-editor.vue。

用法：python tests/check_locate_regression.py（需要目标仓库存在）
"""
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyzer import DefectAnalyzer, load_precedent_files, strip_html  # noqa: E402

FAILING_PROPOSAL_NAME = "L4Zo7p9zF8rXm5CU-20260922-191330.json"


def _proposal_path(name: str) -> Path:
    """优先用本地 proposals/（含真实分析数据），缺失时回退到脱敏夹具。"""
    local = PROJECT_ROOT / "proposals" / name
    if local.exists():
        return local
    return PROJECT_ROOT / "tests" / "fixtures" / "proposals" / name


FAILING_PROPOSAL = _proposal_path(FAILING_PROPOSAL_NAME)

# 旧版正则（无 re.A）在真实工单上产出的脏 path_tokens（按出现顺序重建）
DIRTY_TOKENS = [
    "cn/agent-workspace/schedule",       # URL 残段（test-develop-mysql.cloudpivot.cn/...）
    "demo/Acme",                         # 测试账号
    "agent-workspace/schedule",          # 正文里的路径提示
    "观察日期/时间控件的可编辑性",         # 中文短语（Unicode \w 误捕）
    "api/api/runtime/ai/schedule/create",  # 接口路径
]

checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("== 定位算法回归（L4Zo7p9zF8rXm5CU）==")
    prop = json.loads(FAILING_PROPOSAL.read_text(encoding="utf-8"))
    defect = prop["defect"]
    saved_kws = prop["analysis"]["keywords"]

    settings_file = PROJECT_ROOT / "ui_settings.json"
    if not settings_file.exists():
        print("  [SKIP] 无 ui_settings.json（未配置目标仓库），本测试需要真实目标仓库")
        return 0
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    repo = settings.get("repo", {}).get("path", "")
    if not repo or not Path(repo).is_dir():
        print(f"  [SKIP] 目标仓库不存在：{repo}")
        return 0

    a = DefectAnalyzer(repo, None, precedent_dir=str(PROJECT_ROOT / "proposals"))
    text = f"{defect.get('title', '')}\n{strip_html(defect.get('description', ''))}"

    # 1. path_tokens 清洗
    kws, ptoks = a._extract_keywords(text)
    check("path_tokens 全部为 ASCII", all(p.isascii() for p in ptoks), str(ptoks))
    check("路径提示 agent-workspace/schedule 被保留",
          any("agent-workspace/schedule" in p for p in ptoks))
    check("路径段进入 grep 关键词表（去重后不再被挤出）",
          any("schedule" in k for k in kws),
          f"kws[:14]={kws[:14]}")

    # 2. 先例加载
    a._wants_validation = True  # 工单含「校验」，与真实 analyze() 判定一致
    precedent = load_precedent_files(str(PROJECT_ROOT / "proposals"))
    check("load_precedent_files 找到历史采纳文件",
          any("schedule-editor" in p for p in precedent), str(precedent[:4]))
    a._precedent_files = precedent

    # 3. 新鲜关键词 + 重建的脏 token：投票制必须选出 schedule 目录
    files = a._scan_repo(kws, DIRTY_TOKENS)
    check("脏 path_tokens 下 schedule 目录文件进入嫌疑列表",
          any("pages/schedule/" in f for f in files), str(files[:6]))

    # 4. 用 19:13 失败运行保存的原始关键词重放：读取窗口必须包含 schedule 模块
    files2 = a._scan_repo(saved_kws, DIRTY_TOKENS)
    snippets2, _ = a._read_contexts(files2, saved_kws)
    check("失败运行的关键词重放后 schedule 文件进入读取窗口",
          any("pages/schedule/" in f for f in snippets2), str(list(snippets2)))
    check("schedule-editor.vue（历史先例）进入嫌疑列表",
          any("schedule-editor" in f for f in files2), str(files2[:6]))

    # 5. 校验语境不再全局碾压：得分最高的非 form 文件必须能进入前 4 读取窗口
    read4 = list(snippets2)[: a.max_files]
    check("读取窗口不再被 form-* 文件全部占满",
          any("pages/schedule/" in f for f in read4), str(read4))

    # 6. named 提前不得丢文件（旧写法 named[:2]+其余 会把 named[2:] 整段丢掉，
    #    tier-0 修好后 top 里几乎全是 schedule-* 文件，高分文件就此消失）
    check("嫌疑列表未因 named 提前而丢文件（返回数达候选上限）",
          len(files2) >= 6, f"len={len(files2)}")

    # 7. 二次定位：模型点名的具体文件必须能强制开窗
    #    （桌面版 schedule-repeat.vue 排序是第 5 名，靠排序进不了 4 个名额）
    rc = ("真正的缺陷点在 packages/cloudpivot-ai/lui/pages/schedule/"
          "schedule-repeat.vue，该文件未提供")
    hints = a._model_hints({"root_cause": rc}, ptoks)
    file_hints = [h for h in hints
                  if h.rsplit(".", 1)[-1].lower() in {"vue", "ts", "tsx", "js"}]
    check("_model_hints 能抽出模型点名的具体文件路径",
          "packages/cloudpivot-ai/lui/pages/schedule/schedule-repeat.vue" in file_hints,
          str(hints))
    forced_ok = False
    for fp in file_hints[:2]:
        t, _note = a._read_one(fp, saved_kws)
        if t:
            forced_ok = True
            break
    check("模型点名的文件可被直接读取（强制开窗路径可用）", forced_ok)

    # 8. 病根代码必须真正进入窗口 —— 本 bug 的最后一层：
    #    窗口曾停在模板行（@change="handleTimeChange"），把方法实现
    #    `time || '08:00'`（失焦回退的元凶）留在窗口外，模型据此拒出补丁。
    #    修法是顺 Vue 模板 handler 引用展开到方法定义。
    repeat_rel = "packages/cloudpivot-ai/lui/pages/schedule/schedule-repeat.vue"
    win, _note = a._read_one(repeat_rel, saved_kws)
    # 病根是「窗口只停在模板 @change 引用行，方法体在窗外」——所以断言窗口
    # 必须覆盖方法定义（定义带括号，模板引用不带）。'08:00' 兜底签名不能断言：
    # 该缺陷被修复后签名会从文件里消失（2026-09-23 傍晚上游已修）。
    check("schedule-repeat.vue 窗口中含病根 handleTimeChange 的实现",
          bool(win) and bool(re.search(r"handleTimeChange\s*\(", win)),
          f"len={len(win) if win else 0}")
    check("schedule-repeat.vue 窗口中含 noRepeat 分支模板（handler 调用点）",
          win and 'scheduleType === \'noRepeat\'' in win)

    failed = [c for c in checks if not c[1]]
    print(f"\n== {len(checks) - len(failed)}/{len(checks)} 通过 ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
