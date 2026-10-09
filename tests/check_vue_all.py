# -*- coding: utf-8 -*-
"""迁移自检总入口（Vite + Vue 3 已收口：`GET /` 是唯一实现）。

跑两组东西：
  1. 仍对唯一实现成立的既有界面用例 —— 用 tests/temp_server.py 起临时实例，
     把它们硬编码的 http://127.0.0.1:8765 替换成临时地址后执行。
     本机 8765 被一个本地代理占着（起服务也会 502），所以不能直接用那个端口。
     只保留不依赖旧版全局函数的用例（见 LEGACY 注释里退役清单的说明）。
  2. 自带临时实例的用例 —— 它们本来就自己起服务。

用法：.venv\\Scripts\\python.exe tests/check_vue_all.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT))

from temp_server import serve                                  # noqa: E402

# 旧版原生界面（index.html + app.js）已在 Vue 3 收口时删除，`GET /` 现在直接返回
# Vue 产物。所以这里只保留**不依赖旧版全局函数**的既有用例：它们只用页签切换
# 入口（迁移期保留的 window.switchTab 兼容层）+ DOM/CSS 断言，对唯一实现仍成立。
#
# 下面这些用例**已退役**（清单留此备查，不再执行）——它们直调旧版挂到 window 上
# 的命令式函数，而在 Vue 版里这些是 composable 内部状态，没有同名全局，
# 一律 `ReferenceError`（实测）：
#   - check_stages_ui.py【已收掉】→ 曾直调 window 上的 stagesFromRunResults；纯函数分支
#                                判定已迁到 frontend/tests/pure.test.mjs（由
#                                tests/check_pure_js.py 收进 pytest），DOM 接线仍由
#                                check_vue_defect_ui.py 第 4b 步守
#   - check_live_ui.py         → healthCache / liveSetup / liveLine / liveAI 未定义；
#                                覆盖面（日志区 420px、空态占位、实时视图）已迁到
#                                check_vue_defect_ui.py 第 3、4 步
#   - check_expand_modal.py    → renderRunResults 未定义；覆盖面已迁到
#                                check_vue_defect_ui.py 第 4 步（放大按钮 + #runmodal）
#   - check_kb_patterns_ui.py  → loadKb / setKbView 未定义；双层视图 + 开关持久化
#                                已迁到 check_vue_defect_ui.py 的知识库部分
#   - check_changelog_ui.py    → 找不到旧版 DOM 而超时；#chmodal / #ch-body /
#                                .ch-chip / 关闭 已由 check_vue_settings_ui.py 覆盖
#   - check_kb_render.py【已收掉】→ 曾直调 window 上的 renderMarkdown；表格/代码块/
#                                空行不腰斩/内外链分流等渲染规则已迁到
#                                frontend/tests/pure.test.mjs，弹窗接线仍由
#                                check_vue_settings_ui.py 的 #modal-body 断言覆盖
#   - check_chat_ui.py         → 旧版问答用例；已由 check_vue_chat_ui.py 全面承接
#   - check_pager.py           → proposalsCache / renderProposals / gotoPropPage /
#                                propPage 未定义（2026-10-09 实测 ReferenceError）；
#                                分页契约已由本文件调度的 check_vue_defect_ui.py
#                                第 5 步承接（灌 30 条真提案测每页 8 条 / 翻页 /
#                                筛选重置），页码折叠序列另由 pure.test.mjs 守
#
# 2026-10-09：上面这批"清单留此备查"的死壳文件本身也收掉了（进系统回收站，可恢复）。
# 留着它们只有一个坏处——名字看起来还像在守什么，实际一跑就 ReferenceError，
# 于是没人跑、没人知道。覆盖面迁移去向都写在上面每一行里，按图找得到。
LEGACY = [
    "check_panel_fold.py",
    "check_layout_ui.py",
]
# 自带临时实例，直接跑。
SELF_CONTAINED = [
    "check_vue_migration.py",
    "check_vue_shell_ui.py",
    # 顶部导航布局的页签条：几何 / 溢出箭头 / 切页签自动滚动 / 换布局复原
    "check_vue_topbar_tabs.py",
    "check_vue_stats_ui.py",
    "check_vue_chat_ui.py",
    "check_vue_defect_ui.py",
    "check_vue_team_ui.py",
    "check_vue_tasks_ui.py",
    "check_vue_extensions_ui.py",
    "check_vue_settings_ui.py",
    "check_chat.py",
    "check_usage.py",
    "check_usage_ui.py",
]

report = []
# 必须待在仓库根下的一层子目录里：这些脚本用 `Path(__file__).resolve().parents[1]`
# 反推仓库根，扔进系统临时目录它们就找不到 scripts/ 与 tests/ 夹具了。
SCRATCH = ROOT / ".scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)

with serve(kb="fixture") as srv:
    for name in LEGACY:
        src = TESTS / name
        if not src.exists():
            report.append([name, "missing", ""])
            continue
        code = src.read_text(encoding="utf-8").replace("http://127.0.0.1:8765", srv.base)
        # 改写副本写到系统临时目录，不再落在 tests/ 里。以前落在 tests/ 靠 `_tmp_*`
        # 的 gitignore 规则兜着，代价是仓库目录里长期躺着五六个"看起来像源码"的
        # 生成物（还被人手工留过一份没前缀的副本，分不清哪个是在用的）。
        dst = SCRATCH / name
        dst.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(dst)], cwd=str(ROOT),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300,
                env={**os.environ, "PYTHONIOENCODING": "utf-8",
                     "PYTHONPATH": os.pathsep.join([str(ROOT), str(TESTS)])},
            )
        except subprocess.TimeoutExpired:
            report.append([name, "TIMEOUT", ""])
            continue
        out = (proc.stdout or "") + (proc.stderr or "")
        fails = out.count("[FAIL]") + out.count("FAIL:")
        tail = [l for l in out.splitlines() if "CHECKS" in l or "PASS" in l]
        report.append([name, f"exit={proc.returncode} fails={fails}",
                       tail[-1][:100] if tail else ""])

for name in SELF_CONTAINED:
    src = TESTS / name
    if not src.exists():
        report.append([name, "missing", ""])
        continue
    try:
        proc = subprocess.run(
            [sys.executable, str(src)], cwd=str(ROOT),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=420,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        report.append([name, "TIMEOUT", ""])
        continue
    out = (proc.stdout or "") + (proc.stderr or "")
    fails = out.count("[FAIL]") + out.count("FAIL:")
    tail = [l for l in out.splitlines()
            if "CHECKS" in l or "PASS" in l or "全部通过" in l or "失败" in l]
    report.append([name, f"exit={proc.returncode} fails={fails}",
                   tail[-1][:100] if tail else ""])

bad = [r for r in report if r[1].startswith("exit=") and not r[1].startswith("exit=0")]
print(json.dumps(report, ensure_ascii=False, indent=1))
print(f"\n合计 {len(report)} 个用例，非零退出 {len(bad)} 个")
sys.exit(1 if bad else 0)
