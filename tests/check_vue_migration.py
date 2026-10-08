# -*- coding: utf-8 -*-
"""前端自检：确认仓库里只有一份 Vue 3 实现，且它真能起来。

收口后守四件事：
  1. **单一实现** —— 旧版 index.html / app.js / debugBridge.js 已删除，
     `/` 直接就是 Vue 产物（防止有人把旧界面又加回来）；
  2. **产物完整** —— /static/v2.html 存在，引用的 js/css 都真能取到（200）；
  3. **契约保留** —— 页面里能拿到 switchTab / 八个页签 / 默认页签；
  4. **API 覆盖** —— 后端接口在新前端的 api 层都有入口，四条 SSE 已登记。

不联网、不调模型；起的是临时实例（数据目录全指到临时目录）。
用法：
    python tests/check_vue_migration.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


# 2026-09-30：配置独立成模块，末尾新增 config 页签（原有 8 个顺序不变）
EXPECTED_TABS = ['stats', 'chat', 'defect', 'team', 'reqdev', 'apidebug', 'codetest',
                 'extensions', 'config']


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vue-mig-"))
    static = PROJECT_ROOT / "web" / "static"

    # ── 1. 单一实现：旧版已退场，Vue 产物就位 ──
    print("\n[1] 单一 Vue 实现（旧版已删除）")
    check("旧版 index.html 已删除", not (static / "index.html").exists())
    check("旧版 app.js 已删除", not (static / "app.js").exists())
    check("迁移期调试桥已删除",
          not (PROJECT_ROOT / "frontend" / "src" / "api" / "debugBridge.js").exists())
    check("旧版 style.css 仍在（共享样式表，前端复用）", (static / "style.css").is_file())
    check("Vue 入口 v2.html 已产出", (static / "v2.html").is_file())

    vue_dir = static / "vue"
    js = sorted(vue_dir.glob("app.*.js")) if vue_dir.is_dir() else []
    check("Vue JS bundle 已产出", len(js) == 1, str([p.name for p in js]))
    check("Vue 资源隔离在 vue/ 子目录", not list(static.glob("app.*.js")))

    # v2.html 里引用的 js/css 必须都真实存在
    if (static / "v2.html").is_file():
        html = (static / "v2.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
        # /static/xxx → web/static/xxx（静态目录本身就挂在 /static 上）
        missing = [r for r in refs if not (static / r[len("/static/"):]).is_file()]
        check("v2.html 引用的资源全部存在", not missing, f"缺失: {missing}" if missing else f"{len(refs)} 个引用")
        check("v2.html 复用 style.css",
              any(r.endswith("style.css") for r in refs), str(refs))

    # ── 2. 后端路由：/ 就是唯一入口 ──
    print("\n[2] 后端路由")
    for k, sub in [("ATTACH_DIR", "attachments"), ("CHAT_DIR", "chat"), ("PROPOSAL_DIR", "proposals"),
                   ("ARTIFACT_DIR", "artifacts"), ("TEAM_DIR", "team"), ("KB_DIR", "kb"),
                   ("USAGE_DIR", "usage"), ("SCREENSHOT_DIR", "shots")]:
        os.environ[k] = str(tmp / sub)
    os.environ["SETTINGS_FILE"] = str(tmp / "ui_settings.json")

    from web import server
    from web import state  # 目录常量与 settings 助手随模块化拆分搬到了 web/state.py
    check("/ 指向 Vue 入口",
          state.VUE_ENTRY.name == "v2.html" and state.VUE_ENTRY.is_file(),
          str(state.VUE_ENTRY))

    def api_paths(app):
        """列出 /api 端点。

        不能直接读 app.routes：新版 FastAPI 的 include_router 是**惰性展开**的，
        app.routes 里放的是 _IncludedRouter 包装对象，既没有 .path 也还没有 .routes，
        静态遍历会把 52 条业务路由看成 0 条（实测踩过）。openapi() 会强制构建路由表，
        所以枚举走它；这也顺带证明「路由确实能被装配出来」，比读内部结构更贴近事实。
        """
        return set(app.openapi().get("paths", {}))

    routes = api_paths(server.app)
    check("/v2 路由已移除（不再需要别名）", "/v2" not in routes)
    check("原有路由未被动过（/api/health 在）", "/api/health" in routes)
    check("业务端点数量未缩水（拆分只搬位置不丢路由）",
          len(routes) >= 49, f"{len(routes)} 条（HEAD 版本为 50）")
    for must in ("/api/proposals/{pid}/approve", "/api/artifacts/{aid}/undo",
                 "/api/chat/stream", "/api/team/stream", "/api/run/stream",
                 "/api/tasks/run", "/api/settings", "/api/extensions/mcp"):
        check(f"端点在位：{must}", must in routes)

    # ── 3. 契约：全局函数与页签 ──
    print("\n[3] 前端契约（保界面用例不失效）")
    src = (PROJECT_ROOT / "frontend" / "src")
    app_vue = (src / "App.vue").read_text(encoding="utf-8")
    main_js = (src / "main.js").read_text(encoding="utf-8")
    tabs_js = (src / "components" / "tabs.js").read_text(encoding="utf-8")

    check("switchTab 暴露到 window（界面用例直接调它）",
          "window.switchTab" in app_vue)
    check("toggleTheme 暴露到 window", "window.toggleTheme" in app_vue)
    check("页签顺序与旧版一致",
          all(f"'{t}'" in tabs_js for t in EXPECTED_TABS) and
          [m for m in re.findall(r"name:\s*'(\w+)'", tabs_js)] == EXPECTED_TABS,
          str(re.findall(r"name:\s*'(\w+)'", tabs_js)))
    check("默认页签仍是 stats", "DEFAULT_TAB = 'stats'" in tabs_js)
    check("页签容器沿用旧 id/class（.tabpane#pane-*）",
          'class="tabpane"' in app_vue and ':id="`pane-${t.name}`"' in app_vue)
    check("流程条仍是 #stage-flow", 'id="stage-flow"' in app_vue)
    # 产出物面板已拆成组件（ArtifactsPanel.vue），id 跟着组件走；
    # App.vue 里应当以 <ArtifactsPanel 的形式挂载。
    # id 允许两种形态：字面量 id="artifact-panel"，或经 pid() 参数化
    # （问答页签的右栏实例带 -chat 后缀避免与全局面板同 id 重复）。
    artifacts_panel = (src / "components" / "ArtifactsPanel.vue")
    panel_src = artifacts_panel.read_text(encoding="utf-8") if artifacts_panel.is_file() else ""
    check("产出物面板仍是 #artifact-panel（在 ArtifactsPanel.vue 里）",
          artifacts_panel.is_file()
          and ("pid('artifact-panel')" in panel_src or 'id="artifact-panel"' in panel_src)
          and "<ArtifactsPanel" in app_vue)
    # 提示已从自绘 #toast 换成 Element Plus 的 ElMessage（见 composables/useToast.js）。
    # 断言随之从「页面上有 #toast 节点」改成「提示走 ElMessage、旧节点已退场」。
    toast_js = (src / "composables" / "useToast.js").read_text(encoding="utf-8")
    check("提示改用 Element Plus 的 ElMessage（自绘 #toast 已退场）",
          "ElMessage" in toast_js and 'id="toast"' not in app_vue)

    # ── 4. API 客户端覆盖度 ──
    print("\n[4] API 客户端与 SSE")
    cli = (src / "api" / "client.js").read_text(encoding="utf-8")
    sse = (src / "composables" / "useSse.js").read_text(encoding="utf-8")
    # 旧版 app.js 已删除，改用「新前端自己声明过的接口」做基准：
    # 四个任务页签 + 问答 + 用量 + 知识库的入口必须都在 api 层或 SSE 登记表里，
    # 不允许视图里散落硬编码的 fetch 路径。
    covered = set(re.findall(r"['\"`](/api/[a-z0-9/_-]+)", cli)) | \
        set(re.findall(r"['\"`](/api/[a-z0-9/_-]+)", sse))
    required = {
        "/api/run", "/api/probe", "/api/proposals", "/api/stats", "/api/cards",
        "/api/patterns", "/api/artifacts", "/api/team/runs", "/api/tasks/run",
        "/api/usage/report", "/api/chat/sessions", "/api/extensions", "/api/settings",
        "/api/health", "/api/changelog", "/api/repo/file", "/api/figma/fetch",
    }
    missed = sorted(p for p in required if p not in covered)
    check("关键接口在新前端的 api 层都有入口",
          len(missed) == 0, f"未覆盖: {missed}" if missed else f"{len(required)} 个")

    # 视图层不许绕过 api 层自己发请求（否则接口口径会两处漂移）
    stray = {}
    for f in (src / "views").rglob("*.vue"):
        hits = re.findall(r"""fetch\(\s*['"`](/api/[^'"`]+)""", f.read_text(encoding="utf-8"))
        if hits:
            stray[f.name] = hits
    check("视图层没有绕过 api 层直接 fetch", not stray, str(stray))

    # 流式接口必须被显式登记（而不是散落在视图里硬编码）
    streams = sorted(re.findall(r"['\"]((/api/\w+/stream))['\"]", cli))
    check("四条 SSE 流式接口已集中登记",
          len([s for s, _ in streams]) == 4, str([s for s, _ in streams]))

    check("SSE 用 POST + ReadableStream（不是 EventSource）",
          "getReader()" in sse and "new EventSource" not in sse and "EventSource(" not in sse)
    check("SSE 处理心跳注释 ': ping'", "startsWith('data:')" in sse)
    check("SSE 以 \\n\\n 分包", r"indexOf('\n\n')" in sse)

    print(f"\n{'全部通过' if not BAD else '失败 %d 项：%s' % (len(BAD), '；'.join(BAD))}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
