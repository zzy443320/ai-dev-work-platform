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
        pg.wait_for_selector("#chmodal:not(.hidden)", timeout=8000)
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

        # ---- 3. 表单留白 ----
        pg.evaluate("() => switchTab('reqdev')")
        pg.wait_for_timeout(200)
        gaps = pg.evaluate("""() => {
          const pane = document.getElementById('pane-reqdev');
          const fields = [...pane.querySelectorAll(':scope > .panel > .field')];
          const px = el => parseFloat(getComputedStyle(el).marginTop) || 0;
          return fields.map(px);
        }""")
        check("需求表单字段有上边距", len(gaps) >= 3 and all(g >= 10 for g in gaps[1:]), str(gaps))
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

        # ---- 5. 长任务作业：说明条分行 / 标签不折行 / 表单留白 ----
        pg.evaluate("() => switchTab('team')")
        pg.wait_for_timeout(200)
        tn = pg.evaluate("""() => {
          const note = document.querySelector('#team-run-panel .info-note');
          return note ? note.querySelectorAll('br').length : -1;
        }""")
        check("长任务说明条分行", tn >= 2, f"br={tn}")
        nowrap = pg.evaluate("""() => {
          const sp = [...document.querySelectorAll('#team-run-panel .field-inline > span')]
            .find(s => s.textContent.includes('技术栈'));
          return sp ? getComputedStyle(sp).whiteSpace : '';
        }""")
        check("行内字段标签不折行", nowrap == 'nowrap', f"white-space={nowrap}")
        tgap = pg.evaluate("""() => {
          const pane = document.getElementById('pane-team');
          const fields = [...pane.querySelectorAll(':scope > #team-run-panel > .field')];
          const px = el => parseFloat(getComputedStyle(el).marginTop) || 0;
          return fields.map(px);
        }""")
        check("长任务表单字段有间隔", len(tgap) >= 2 and tgap[1] >= 10, str(tgap))
        pg.screenshot(path=str(OUT / "team-layout.png"))

        pg.evaluate("() => switchTab('chat')")
        pg.wait_for_timeout(200)
        mb = pg.evaluate("""() => {
          const el = document.querySelector('#pane-chat .chat-input');
          return parseFloat(getComputedStyle(el).marginBottom) || 0;
        }""")
        check("问答输入区与说明条有间隔", mb >= 8, f"margin-bottom={mb}")
        pg.screenshot(path=str(OUT / "chat-layout.png"))

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
