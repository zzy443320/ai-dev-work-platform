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
        pg.click("#changelog-btn") if pg.locator("#changelog-btn").count() else None
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

        # ---- 3. 需求开发：输入全在参数抽屉里，主区只剩摘要条与过程区 ----
        pg.evaluate("() => switchTab('reqdev')")
        pg.wait_for_timeout(300)
        split = pg.evaluate("""() => {
          const panel = document.getElementById('req-panel');
          const drawer = document.getElementById('mod-drawer-reqdev');
          return {mainFields: panel.querySelectorAll('.field').length,
                  drawerFields: drawer.querySelectorAll('.field').length,
                  bar: !!panel.querySelector('.param-bar'),
                  tree: !!document.getElementById('figma-tree'),
                  open: !drawer.classList.contains('hidden'),
                  padR: parseFloat(getComputedStyle(document.querySelector('.col-main')).paddingRight)};
        }""")
        check("需求开发：主区没有输入字段，只剩摘要条 + 设计稿结构",
              split["mainFields"] == 0 and split["bar"] and split["tree"], str(split))
        check("需求开发：参数抽屉默认打开、装下全部输入",
              split["open"] and split["drawerFields"] >= 5, str(split))
        check("抽屉开着时主列让出右侧空间（非模态，不遮挡过程区）",
              split["padR"] >= 400, str(split["padR"]))
        pg.screenshot(path=str(OUT / "reqdev-layout.png"))

        # ---- 4. 统计说明条：分段之间有间隔 ----
        pg.evaluate("() => switchTab('stats')")
        pg.wait_for_timeout(300)
        ugap = pg.evaluate("""() => {
          const el = document.querySelector('#usage-note > div:nth-child(2)')
            || document.querySelector('#usage-note > div');
          return el ? parseFloat(getComputedStyle(el).marginTop) || 0 : -1;
        }""")
        check("统计说明条分段有间隔", ugap >= 4, f"margin-top={ugap}")

        # ---- 5. 长任务作业：说明条分行 / 输入进抽屉 / 运行中干预留在主区 ----
        pg.evaluate("() => switchTab('team')")
        pg.wait_for_timeout(300)
        tn = pg.evaluate("""() => {
          const note = document.querySelector('#team-run-panel .info-note');
          return note ? note.querySelectorAll('br').length : -1;
        }""")
        check("长任务说明条分行", tn >= 2, f"br={tn}")
        ts = pg.evaluate("""() => {
          const panel = document.getElementById('team-run-panel');
          const drawer = document.getElementById('mod-drawer-team');
          return {mainFields: panel.querySelectorAll('.field').length,
                  drawerFields: drawer.querySelectorAll('.field').length,
                  bar: !!panel.querySelector('.param-bar'),
                  // 干预按钮组留在主区（对着看板用），且空闲时整行隐藏
                  interveneInMain: !!panel.querySelector('#btn-team-pause'),
                  interveneHidden: panel.querySelector('.team-run-actions')
                    .classList.contains('hidden'),
                  // 开始作业在抽屉 footer 里
                  startInDrawer: !!drawer.querySelector('#btn-team-run')};
        }""")
        check("长任务：作业表单全在抽屉里，主区只剩摘要条",
              ts["mainFields"] == 0 and ts["bar"] and ts["drawerFields"] >= 5, str(ts))
        check("长任务：运行中干预留在主区、空闲时整行隐藏",
              ts["interveneInMain"] and ts["interveneHidden"], str(ts))
        check("长任务：「开始作业」在抽屉动作条里", ts["startInDrawer"], str(ts))
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
