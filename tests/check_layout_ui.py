# -*- coding: utf-8 -*-
"""排版自检：更新日志分点换行 + 说明条分行 + 表单留白。

对着已运行的服务（默认 http://127.0.0.1:8765）做纯展示层断言，
不改任何数据。截图存 .workbuddy/ 供人工过目。

用法：
    .venv/Scripts/python.exe tests/check_layout_ui.py [base_url]

可选环境变量 TEST_REPO：设了就校验服务是否指向该仓库。本用例纯只读（只点弹窗、读
DOM、截图），不像 test_browser_e2e/guards 那样会写目标仓库，所以这道闸门是可选的。
⚠ 别把具体仓库路径写死在这里——那是本机私有信息，不该进版本库。
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from server_guard import require_repo  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
REPO = os.environ.get("TEST_REPO", "").strip()
OUT = Path(__file__).resolve().parent.parent / ".workbuddy"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def run():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 920})
        pg.goto(BASE, wait_until="domcontentloaded")

        # ---- 1. 更新日志：①②③ 分点各自一行 ----
        # goto 用的是 domcontentloaded，此时 Vue 可能还没挂载（顶栏按钮是组件渲染出来的）。
        # 原来「有按钮就点、没有就算了」会静默跳过点击，接着等一个永远不会开的弹窗，
        # 冷启动偶发 0/1 假红（同一份产物重跑就好）。这里先等按钮出现再点。
        pg.wait_for_selector("#changelog-btn", timeout=15000)
        pg.click("#changelog-btn")
        # 等「真的弹出来」要看对话框本体：#chmodal 只是常驻壳（承载稳定 id 与
        # hidden 语义），el-dialog 的 overlay 是 position:fixed，不撑开父盒，
        # 所以那个壳的 bounding box 恒为 0 高，Playwright 判它不可见。
        pg.wait_for_selector("#chmodal .el-dialog", timeout=8000)
        pg.wait_for_selector(".ch-item", timeout=8000)
        nums = pg.evaluate("""() => {
          const els = [...document.querySelectorAll('.ch-val .ch-para.num')];
          return els.slice(0, 6).map(e => e.textContent.slice(0, 14));
        }""")
        check("更新日志存在 ①②③ 分点行", len(nums) >= 3, str(nums[:3]))
        mono = pg.evaluate("""() => {
          // 任一 .ch-val 里，序号行不允许和其它文本挤在同一行
          for (const val of document.querySelectorAll('.ch-val')) {
            for (const para of val.querySelectorAll('.ch-para')) {
              const t = para.textContent.trim();
              if (/^[\\u2460-\\u2473]/.test(t) && para.textContent.length < 4) return false;
            }
          }
          return true;
        }""")
        check("分点行内容完整（非空壳）", mono)
        # ① 不能出现在非行首（即没有被拆开）
        inline = pg.evaluate("""() => {
          for (const para of document.querySelectorAll('.ch-val .ch-para')) {
            const t = para.textContent;
            const m = t.match(/[\\u2460-\\u2473]/g) || [];
            if (m.length > 1) return t.slice(0, 40);
          }
          return '';
        }""")
        check("一行内不残留多个序号", not inline, str(inline))
        pg.screenshot(path=str(OUT / "changelog-layout.png"))

        # ---- 2. 说明条分行 ----
        pg.keyboard.press("Escape")
        brs = pg.evaluate("""() => {
          const out = {};
          for (const id of ['dry-warn', 'req-figma-hint']) {
            const el = document.getElementById(id);
            out[id] = el ? el.querySelectorAll('br').length : -1;
          }
          const chatNote = document.querySelector('#pane-chat .info-note');
          out['chat'] = chatNote ? chatNote.querySelectorAll('br').length : -1;
          return out;
        }""")
        check("缺陷页说明条分行", brs.get("dry-warn", 0) >= 2, str(brs))
        check("需求 Figma 提示分行", brs.get("req-figma-hint", 0) >= 1, str(brs))
        check("问答说明条分行", brs.get("chat", 0) >= 2, str(brs))

        # ---- 3. 需求开发：输入在常驻参数轨里，主区只剩过程区 ----
        pg.evaluate("() => switchTab('reqdev')")
        pg.wait_for_timeout(300)
        # 参数形态改版（2026-10-13）：抽屉（fixed 浮层 + 主列让位）换成**布局第二列**。
        # 原来锁的三条意图逐条平移，不降标准：
        #   「主区只剩摘要条与过程区」→ 主区只剩过程区，输入一格都不在里头；
        #   「抽屉装得下全部输入」    → 轨里字段数 >= 5；
        #   「开着让位不遮挡过程区」  → 更强：轨不是浮层（position 不是 fixed），
        #                              主列也不再靠 padding-right 腾地方，列数是稳定的。
        split = pg.evaluate("""() => {
          const panel = document.getElementById('req-panel');
          const rail = document.getElementById('param-rail-reqdev');
          const pane = document.getElementById('pane-reqdev');
          return {mainFields: panel.querySelectorAll('.pf, .field').length,
                  railFields: rail.querySelectorAll('.pf').length,
                  railPos: getComputedStyle(rail).position,
                  railW: Math.round(rail.getBoundingClientRect().width),
                  panelW: Math.round(panel.getBoundingClientRect().width),
                  tree: !!document.getElementById('figma-tree'),
                  drawerGone: !document.getElementById('mod-drawer-reqdev'),
                  padR: parseFloat(getComputedStyle(document.querySelector('.col-main')).paddingRight)};
        }""")
        check("需求开发：主区只剩过程区，输入一格都不在里头",
              split["mainFields"] == 0 and split["tree"], str(split))
        check("需求开发：参数轨装下全部输入（>=5 格）", split["railFields"] >= 5, str(split))
        check("参数轨是布局的一列而不是浮层",
              split["railPos"] != "fixed" and split["railW"] >= 280, str(split))
        check("主列不再靠 padding-right 让位（列数稳定）",
              split["padR"] < 400 and split["drawerGone"], str(split))
        check("主区与轨并排后仍各有宽度（没被挤成 0）",
              split["panelW"] >= 480, str(split))
        # 空洞回归锁。判据必须是「面板底边与轨底边的差」，不能只看 align-items：
        # 上一版这里写的是 `align 为 start 就算过`，而 start 恰恰是把空洞从面板
        # 里面挪到了面板**外面** —— 1440 实拍 reqdev 空 425px、apidebug 空 673px，
        # 用例却一路绿灯。现在量真实差值，并把 apidebug / team 一起锁上。
        hole = pg.evaluate("""() => {
          const panel = document.getElementById('req-panel');
          const rail = document.getElementById('param-rail-reqdev');
          return {gap: Math.round(Math.abs(rail.getBoundingClientRect().bottom
                                          - panel.getBoundingClientRect().bottom)),
                  panelH: Math.round(panel.getBoundingClientRect().height),
                  railH: Math.round(rail.getBoundingClientRect().height)};
        }""")
        check("需求开发：主区面板填满轨撑起的行（空洞 ≤ 40px）",
              hole["gap"] <= 40, str(hole))
        # 面板填进行之后，内部不能留空：舞台区（终端块）必须跟着长。
        stage = pg.evaluate("""() => {
          const p = document.getElementById('req-panel');
          const t = document.getElementById('figma-tree');
          return {panelH: Math.round(p.getBoundingClientRect().height),
                  treeH: Math.round(t.getBoundingClientRect().height)};
        }""")
        check("需求开发：舞台区吃掉多出来的高度（终端块 ≥ 面板高 60%）",
              stage["treeH"] >= stage["panelH"] * 0.6, str(stage))
        pg.screenshot(path=str(OUT / "reqdev-layout.png"))

        # ---- 3b. 接口联调同一套空洞锁 + 回显区真的有内容 ----
        pg.evaluate("() => switchTab('apidebug')")
        pg.wait_for_timeout(400)
        ahole = pg.evaluate("""() => {
          const panel = document.getElementById('api-panel');
          const rail = document.getElementById('param-rail-apidebug');
          const stage = document.getElementById('api-stage');
          return {gap: Math.round(Math.abs(rail.getBoundingClientRect().bottom
                                          - panel.getBoundingClientRect().bottom)),
                  stageH: Math.round(stage.getBoundingClientRect().height),
                  placeholder: getComputedStyle(stage, '::before').content !== 'none'};
        }""")
        check("接口联调：主区面板填满轨撑起的行（空洞 ≤ 40px）",
              ahole["gap"] <= 40, str(ahole))
        check("接口联调：接口文档回显区存在且有高度、空态有占位文案",
              ahole["stageH"] >= 200 and ahole["placeholder"], str(ahole))
        pg.fill("#api-doc", '{"paths":{"/api/v1/demo":{"get":{}}}}')
        pg.wait_for_timeout(300)
        check("接口联调：贴进文档后原样回显（Vue 插值，不是 v-html）",
              pg.eval_on_selector("#api-stage", "el => el.textContent.includes('/api/v1/demo')"))

        # ---- 3c. 长任务：六面板按体量配对，行内空洞受控 ----
        pg.evaluate("() => switchTab('team')")
        pg.wait_for_timeout(500)
        thole = pg.evaluate("""() => {
          const secs = [...document.querySelectorAll('#pane-team > section')];
          let hole = 0, worst = null;
          for (const s of secs) {
            const b = s.getBoundingClientRect();
            let rowBottom = b.bottom;
            for (const o of secs) {
              const ob = o.getBoundingClientRect();
              if (Math.abs(ob.top - b.top) < 40) rowBottom = Math.max(rowBottom, ob.bottom);
            }
            const h = Math.round(rowBottom - b.bottom);
            if (h > hole) { hole = h; worst = s.id; }
          }
          return {hole, worst};
        }""")
        check("长任务：同行两面板底边对齐（空洞 ≤ 60px，基线 452px）",
              thole["hole"] <= 60, str(thole))

        # ---- 3c+. 面板 stretch 的配套护栏：列表行不许被压扁 ----
        #   面板改成 stretch 后，列表容器拿到确定高度；Grid 的 auto 行会被
        #   压到容器高 ÷ 行数（el-card 自带 overflow:hidden，可以被压扁），
        #   历史卡实测被压成 26px 空壳、文字全无。这里锁卡片高度与内容可见。
        cards = pg.evaluate("""() => {
          const pick = (box, sel) => {
            const el = box.querySelector(sel);
            return el ? {h: Math.round(el.getBoundingClientRect().height),
                         txt: (el.textContent||'').replace(/\\s+/g,' ').trim().length} : null;
          };
          return {history: pick(document.getElementById('team-history'), '.pcard'),
                  agents: pick(document.getElementById('team-agents'), '.team-agent')};
        }""")
        check("长任务：历史卡/角色卡保持内容高（≥ 56px 且有文字）",
              all(v and v["h"] >= 56 and v["txt"] > 8 for v in cards.values() if v), str(cards))

        # ---- 3d. 参数轨 sticky 偏移必须让开顶栏（top:12 < 顶栏 34 会永久切掉轨头）----
        # 长任务的轨 2026-10-14 起并入发起面板（static、不吸顶），吸顶契约由仍是
        # 独立列的需求开发 / 接口联调轨背书 —— 挑一个不在浮层里的查。
        pg.evaluate("() => switchTab('reqdev')")
        pg.wait_for_timeout(300)
        stick = pg.evaluate("""() => {
          const rail = document.getElementById('param-rail-reqdev');
          const cs = getComputedStyle(rail);
          const tb = getComputedStyle(document.querySelector('.topbar'));
          return {top: cs.top, topbarH: tb.height,
                  token: getComputedStyle(document.documentElement).getPropertyValue('--topbar-h').trim()};
        }""")
        check("参数轨吸顶偏移让开顶栏（top ≥ 顶栏高）",
              float(stick["top"].replace("px", "")) >= float(stick["topbarH"].replace("px", "")),
              str(stick))

        # ---- 3e. 配置页吸底条不透明（半透明渐变会把下面那行字切成半截）----
        pg.evaluate("() => switchTab('config')")
        pg.wait_for_timeout(400)
        foot = pg.evaluate("""() => {
          const el = document.querySelector('.config-foot');
          const cs = getComputedStyle(el);
          const m = (cs.backgroundColor || '').match(/rgba\\([^)]*,\\s*([0-9.]+)\\)/);
          return {bg: cs.backgroundColor, alpha: m ? parseFloat(m[1]) : 1,
                  border: cs.borderTopWidth};
        }""")
        check("配置页吸底条背景不透明且有顶边线",
              foot["alpha"] > 0.95 and float(foot["border"].replace("px", "")) >= 1, str(foot))

        # ---- 4. 统计说明条：分段之间有间隔 ----
        pg.evaluate("() => switchTab('stats')")
        pg.wait_for_timeout(300)
        ugap = pg.evaluate("""() => {
          const el = document.querySelector('#usage-note > div:nth-child(2)')
            || document.querySelector('#usage-note > div');
          return el ? parseFloat(getComputedStyle(el).marginTop) || 0 : -1;
        }""")
        check("统计说明条分段有间隔", ugap >= 4, f"margin-top={ugap}")

        # ---- 5. 长任务作业：说明条分行 / 输入进发起面板 / 运行中干预留在主区 ----
        pg.evaluate("() => switchTab('team')")
        pg.wait_for_timeout(300)
        tn = pg.evaluate("""() => {
          const note = document.querySelector('#team-run-panel .info-note');
          return note ? note.querySelectorAll('br').length : -1;
        }""")
        check("长任务说明条分行", tn >= 2, f"br={tn}")
        # 参数形态第五轮（2026-10-14）：参数轨不再独立成列，**嵌进发起面板**
        # 说明文字下方（inline 模式）—— 用户点名「说明下面那块空白放参数，
        # 就不用独立一块参数区域了」。原三条意图平移：
        #   「表单装得下全部输入」   → 轨里字段数 >= 5；
        #   「参数是布局不是浮层」   → position 不是 fixed；
        #   「开始作业就在参数旁边」 → 动作条仍在轨底。
        ts = pg.evaluate("""() => {
          const panel = document.getElementById('team-run-panel');
          const rail = document.getElementById('param-rail-team');
          return {paramsInPanel: !!(panel && rail && panel.contains(rail)),
                  railFields: rail ? rail.querySelectorAll('.pf').length : 0,
                  railPos: rail ? getComputedStyle(rail).position : 'missing',
                  drawerGone: !document.getElementById('mod-drawer-team'),
                  // 干预按钮组留在主区（对着看板用），且空闲时整行隐藏
                  interveneInMain: !!panel.querySelector('#btn-team-pause'),
                  interveneHidden: panel.querySelector('.team-run-actions')
                    .classList.contains('hidden'),
                  // 开始作业在参数轨的常驻动作条里
                  startInRail: !!rail.querySelector('#btn-team-run')};
        }""")
        check("长任务：作业参数并入发起面板（无独立参数列）",
              ts["paramsInPanel"] and ts["railFields"] >= 5 and ts["drawerGone"], str(ts))
        check("长任务：参数区不是浮层", ts["railPos"] != "fixed", str(ts))
        check("长任务：运行中干预留在主区、空闲时整行隐藏",
              ts["interveneInMain"] and ts["interveneHidden"], str(ts))
        check("长任务：「开始作业」在参数轨的常驻动作条里", ts["startInRail"], str(ts))
        # 布局塌陷回归锁：ParamLayout 曾把六面板包进一层 div，`#pane-team > section`
        # 的子代选择器失效 → 面板掉回一列（459px 窄条）、右边空出 559px。
        # 参数并入面板后分两档锁：1440 视口（容器 <1240）单列、不再有「主列|参数轨」
        # 两列；1920 视口（容器 ≥1240）desk.css 第 13 节「战场 | 控制塔」areas 双栏照常。
        tcols = pg.evaluate("""() => {
          const pane = document.getElementById('pane-team');
          const rail = document.getElementById('param-rail-team');
          const panel = document.getElementById('team-run-panel');
          return {cols: getComputedStyle(pane).gridTemplateColumns.split(' ').length,
                  railInPanel: !!(panel && rail && panel.contains(rail))};
        }""")
        check("长任务：参数并入发起面板，页签双栏、不再有第三列参数轨",
              tcols["cols"] == 2 and tcols["railInPanel"], str(tcols))
        pg.set_viewport_size({"width": 1920, "height": 950})
        pg.wait_for_timeout(400)
        tcols2 = pg.evaluate("""() => {
          const pane = document.getElementById('pane-team');
          const board = document.getElementById('team-board-panel');
          const ck = document.getElementById('team-cockpit-panel');
          return {cols: getComputedStyle(pane).gridTemplateColumns.split(' ').length,
                  boardW: Math.round(board.getBoundingClientRect().width),
                  gapRight: Math.round(innerWidth - ck.getBoundingClientRect().right)};
        }""")
        check("长任务：配置|运行监视等高两块（areas 双栏，运行台贴主列右缘）",
              tcols2["cols"] == 2 and tcols2["boardW"] >= 400, str(tcols2))
        check("参数并入后主列铺满，右侧不留空洞", tcols2["gapRight"] <= 60, str(tcols2))
        pg.screenshot(path=str(OUT / "team-layout-wide.png"))
        pg.set_viewport_size({"width": 1440, "height": 920})
        pg.screenshot(path=str(OUT / "team-layout.png"))

        pg.evaluate("() => switchTab('chat')")
        pg.wait_for_timeout(200)
        mb = pg.evaluate("""() => {
          const el = document.querySelector('#pane-chat .chat-input');
          return parseFloat(getComputedStyle(el).marginBottom) || 0;
        }""")
        check("问答输入区与说明条有间隔", mb >= 8, f"margin-bottom={mb}")
        pg.screenshot(path=str(OUT / "chat-layout.png"))

        # ---- 多栏断点：问「主列有多宽」而不是「视口有多宽」（容器查询）----
        # 侧边工作台先吃掉左导航 212 + 间距 18 + 页面留白 56 = 286px。旧断点按视口
        # 1200 判定，1280 笔记本上主列只剩 994px 却仍劈成两栏，每栏 ~480px 读不了。
        # 量扩展能力页签（两块面板并排）—— 表单进抽屉后它是少数还带多栏谱系的模块。
        COLS = """() => {
          const cm = document.querySelector('.col-main');
          const pane = document.getElementById('pane-extensions');
          return {mainW: Math.round(cm.getBoundingClientRect().width),
                  cols: pane ? getComputedStyle(pane).gridTemplateColumns : null};
        }"""
        for w, expect_two in ((1280, False), (1600, True)):
            pg.set_viewport_size({"width": w, "height": 900})
            pg.wait_for_timeout(400)
            pg.evaluate("() => switchTab('extensions')")
            pg.wait_for_timeout(400)
            g = pg.evaluate(COLS)
            two = bool(g["cols"]) and g["cols"] != "none" and len(g["cols"].split(" ")) > 1
            check(f"{w} 视口（主列 {g['mainW']}px）多栏判定{'开' if expect_two else '关'}",
                  two is expect_two, f"cols={g['cols']}")

        # ---- 空洞回归：#tabs 不许被拉满 ----
        # 产出物面板（#artifact-panel）在 .col-main 里是 #tabs 的**兄弟节点**，不是 pane
        # 的后代。一旦 #tabs 走 1fr 吃满剩余高度（只有 FILL_TABS 里的问答页该这样），
        # 模块面板与产出物面板中间就空出一整块 —— 2560×1400 实测 603px，用户看到的
        # 就是「模块内间距怎么这么大」。这里锁住：两者间距 ≈ grid gap，且 #tabs 底边
        # 不超出 pane 内容底边。
        pg.set_viewport_size({"width": 2560, "height": 1400})
        pg.wait_for_timeout(400)
        pg.evaluate("() => switchTab('reqdev')")
        pg.wait_for_timeout(500)
        void = pg.evaluate("""() => {
          const pane = document.querySelector('.tabpane.active');
          const kids = [...pane.children].filter(c => getComputedStyle(c).position !== 'fixed');
          const bottom = Math.max(...kids.map(c => c.getBoundingClientRect().bottom));
          const ap = document.getElementById('artifact-panel');
          const tabs = document.getElementById('tabs');
          return {gap: Math.round(ap.getBoundingClientRect().top - bottom),
                  tabsVoid: Math.round(tabs.getBoundingClientRect().bottom - bottom)};
        }""")
        check("大屏下模块面板与产出物面板之间没有空洞",
              void["gap"] <= 24 and void["tabsVoid"] <= 24, str(void))
        # 问答页反过来：必须仍然占满整屏剩余高度
        pg.evaluate("() => switchTab('chat')")
        pg.wait_for_timeout(500)
        fill = pg.evaluate("""() => {
          const cm = document.querySelector('.col-main');
          const p = document.querySelector('#pane-chat .chat-pane');
          return {fill: cm.classList.contains('pane-fill'),
                  h: Math.round(p.getBoundingClientRect().height),
                  avail: Math.round(cm.getBoundingClientRect().height)};
        }""")
        check("问答页仍占满主列剩余高度（pane-fill 生效）",
              fill["fill"] is True and abs(fill["h"] - fill["avail"]) <= 2, str(fill))
        pg.set_viewport_size({"width": 1440, "height": 920})
        pg.wait_for_timeout(300)

        b.close()


def main():
    if REPO and not require_repo(BASE, REPO, what="check_layout_ui（纯只读展示自检）"):
        return 2
    try:
        run()
    except Exception as e:
        check("脚本执行", False, repr(e))
    ok = sum(1 for _, s, _ in results if s)
    for name, s, d in results:
        print(f"[{'ok' if s else 'FAIL'}] {name}" + (f"  -> {d}" if d and not s else ""))
    print(f"CHECKS {ok}/{len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
