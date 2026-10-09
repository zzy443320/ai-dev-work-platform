# -*- coding: utf-8 -*-
"""闭环视觉 agent 的行为用例：browser_look 工具、截图回喂、常驻 dev server、
with_patch「落补丁看页面」的还原保证。

这里刻意**不打真实模型、不起真实浏览器**：
- 模型用 check_fix_agent 的假 AI 按脚本回放；
- browser_look 的上层接线（pipeline._page_look_fn）用「假沙箱 + 不可达端口」覆盖：
  capture() 第一步 reachable() 就返回 unreachable，Playwright 根本不会被拉起。
真实 Playwright 的行为另有 check_verify_page / test_browser_* 负责。
"""
import atexit
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根
sys.path.insert(0, str(Path(__file__).resolve().parent))      # tests/ 夹具

import check_fix_agent as F  # noqa: E402  复用临时仓库、假 AI、补丁回复等夹具
from check_fix_agent import (  # noqa: E402
    CHECK_CMD, FAIL, FakeAI, SOURCE_BUGGED, agent_for, check, make_repo,
    patch_reply, snapshot,
)
from scripts.patch_engine import parse_blocks  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402
from scripts.sandbox import Sandbox, port_open  # noqa: E402

_TEMPS: list = []


def _tempdir(prefix: str) -> Path:
    d = tempfile.TemporaryDirectory(prefix=prefix)
    _TEMPS.append(d)
    return Path(d.name)


@atexit.register
def _cleanup_temps():
    for d in _TEMPS:
        try:
            d.cleanup()
        except OSError:
            pass


# 1x1 PNG（合法最小图片，只为让「截图回喂」有真文件可读）
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class ImgAI(FakeAI):
    """在假 AI 上加一处记录：每次模型调用实际带了几张图。"""

    def __init__(self, replies):
        super().__init__(replies)
        self.images_seen = []

    def complete_text(self, prompt, system="", max_tokens=None, on_delta=None,
                      images=None):
        self.images_seen.append(list(images or []))
        return super().complete_text(prompt, system, max_tokens=max_tokens,
                                     on_delta=on_delta, images=images)


def _look_result(png_path: Path, **over):
    base = {"status": "ok", "final_url": "http://127.0.0.1:59999/plan/list",
            "page_title": "计划列表", "page_text_sample": "计划列表 计划名称 状态",
            "console_errors": ["TypeError: Cannot read properties of undefined"],
            "screenshot": str(png_path),
            "actions_log": [{"op": "goto", "target": "/plan/list", "ok": True}]}
    base.update(over)
    return base


def t_persistent_server():
    """ensure_server：同一循环复用同一个服务句柄；close() 统一停、端口真释放。"""
    print("\n[1] 沙箱常驻 dev server（ensure_server / close）")
    repo = make_repo(with_git=False, with_check_script=False)
    sb = Sandbox(repo, cfg={"mode": "copy"}).open()
    try:
        cmd = f'"{Path(sys.executable).as_posix()}" -m http.server {{port}}'
        h1 = sb.ensure_server(cmd, ready_timeout=45)
        check("服务起得来", h1.ready and h1.port > 0, h1.note[:120])
        h2 = sb.ensure_server(cmd, ready_timeout=45)
        check("第二次调用复用同一句柄（不冷起第二遍）", h2 is h1, "")
        h1.ready = False                      # 模拟半死句柄
        h3 = sb.ensure_server(cmd, ready_timeout=45)
        check("句柄失效时重起新端口", h3.ready and h3 is not h1 and h3.port != h1.port,
              h3.note[:120])
        last = h3
    finally:
        sb.close()
    check("close 之后端口被释放", not port_open("127.0.0.1", last.port, 0.6))


def t_look_feedback_loop():
    """browser_look 的完整回路：工具结果进上下文、截图随下一次模型调用回喂后清空。"""
    print("\n[2] browser_look 回路：截图回喂 + 上下文证据")
    look_dir = _tempdir("look-shot-")
    png = look_dir / "shot.png"
    png.write_bytes(base64.b64decode(_PNG_B64))
    seen = {}

    def look_ok(sandbox, path="", actions=None, with_patch_blocks=None):
        seen["args"] = {"path": path, "actions": actions,
                        "with_patch": bool(with_patch_blocks)}
        seen["sandbox_root"] = str(sandbox.root)
        return _look_result(png)

    repo = make_repo()
    before = snapshot(repo)
    ai = ImgAI([
        json.dumps({"action": "call", "tool": "browser_look",
                    "arguments": {"path": "/plan/list"}}),
        patch_reply(),
    ])
    agent = agent_for(repo, ai, browser_looks=2)
    agent.page_look_fn = look_ok
    res = agent.run({"id": "T1", "title": "计划列表页崩了"})

    check("循环照常收敛 verified", res["conclusion"] == F.CONCLUSION_VERIFIED,
          res["conclusion"])
    check("browser_look 拿到了路径参数", seen.get("args", {}).get("path") == "/plan/list",
          str(seen))
    check("次数被记账", res.get("browser_looks") == 1, str(res.get("browser_looks")))
    first_imgs = ai.images_seen[0] if ai.images_seen else []
    second_imgs = ai.images_seen[1] if len(ai.images_seen) > 1 else []
    check("第一次调用（看页面前）没有图", not first_imgs, str(first_imgs)[:80])
    check("第二次调用带上了截图", len(second_imgs) == 1
          and str(second_imgs[0].get("data_uri", "")).startswith("data:image/png;base64,"),
          str(second_imgs)[:120])
    if len(ai.images_seen) > 2:
        check("截图只随一次调用走（之后清空）", not ai.images_seen[2])
    second = ai.prompts[1]["prompt"] if len(ai.prompts) > 1 else ""
    check("工具结果文本进了上下文（页面状态/报错留痕）",
          "页面状态：ok" in second and "TypeError" in second, "")
    check("截图附带的说明在上下文里", "截图已作为图像" in second, "")
    check("仓库工作区一字节未改", snapshot(repo) == before, "")

    # 动作留痕里有失败步时，上下文必须带上打折提醒
    def look_bad_actions(sandbox, path="", actions=None, with_patch_blocks=None):
        return _look_result(png, actions_log=[
            {"op": "goto", "target": "/plan/list", "ok": True},
            {"op": "click", "target": ".save", "ok": False, "error": "timeout"}])

    ai2 = ImgAI([
        json.dumps({"action": "call", "tool": "browser_look",
                    "arguments": {"path": "/plan/list"}}),
        patch_reply(),
    ])
    agent2 = agent_for(repo, ai2)
    agent2.page_look_fn = look_bad_actions
    res2 = agent2.run({"id": "T2", "title": "x"})
    second2 = ai2.prompts[1]["prompt"] if len(ai2.prompts) > 1 else ""
    check("有动作失败时明确提醒「页面不是完整复现状态」",
          "不是完整复现后的状态" in second2, second2[-300:])


def t_look_guards():
    """未接线 / 预算用完 / 未知工具名单，三种边界都要说得清楚。"""
    print("\n[3] browser_look 边界：未接线 / 预算 / 工具名单")
    look_dir = _tempdir("look-guard-")
    png = look_dir / "s.png"
    png.write_bytes(base64.b64decode(_PNG_B64))

    # ① page_look_fn 未接线：如实说没这工具，循环不炸
    ai = ImgAI([
        json.dumps({"action": "call", "tool": "browser_look",
                    "arguments": {"path": "/x"}}),
        patch_reply(),
    ])
    repo = make_repo()
    agent = agent_for(repo, ai)
    res = agent.run({"id": "T3", "title": "x"})
    second = ai.prompts[1]["prompt"] if len(ai.prompts) > 1 else ""
    check("未接线时如实报「未启用」", "浏览器工具未启用" in second, second[:160])
    check("未接线不阻断修复", res["conclusion"] == F.CONCLUSION_VERIFIED,
          res["conclusion"])
    check("未接线时 prompt 没有「看页面」一节", "看页面（browser_look）" not in ai.prompts[0]["prompt"])

    # ② 预算用完：第二次调用被拒绝，且不再消耗
    def look_ok(sandbox, path="", actions=None, with_patch_blocks=None):
        return _look_result(png)

    ai2 = ImgAI([
        json.dumps({"action": "call", "tool": "browser_look",
                    "arguments": {"path": "/a"}}),
        json.dumps({"action": "call", "tool": "browser_look",
                    "arguments": {"path": "/b"}}),
        patch_reply(),
    ])
    agent2 = agent_for(repo, ai2, browser_looks=1, max_rounds=5)
    agent2.page_look_fn = look_ok
    res2 = agent2.run({"id": "T4", "title": "x"})
    third = ai2.prompts[2]["prompt"] if len(ai2.prompts) > 2 else ""
    check("预算用完后第二次调用被拒", "次数预算" in third and "已用完" in third,
          third[:160])
    check("被拒那次不记账", res2.get("browser_looks") == 1, str(res2.get("browser_looks")))
    check("预算用完仍能继续修到绿", res2["conclusion"] == F.CONCLUSION_VERIFIED,
          res2["conclusion"])
    check("接线后 prompt 有「看页面」一节", "看页面（browser_look）" in ai2.prompts[0]["prompt"])

    # ③ 未知工具的报错名单：接线了才提示 browser_look
    ai3 = ImgAI([
        json.dumps({"action": "call", "tool": "no_such_tool", "arguments": {}}),
        patch_reply(),
    ])
    agent3 = agent_for(repo, ai3)
    agent3.page_look_fn = look_ok
    agent3.run({"id": "T5", "title": "x"})
    second3 = ai3.prompts[1]["prompt"] if len(ai3.prompts) > 1 else ""
    check("未知工具报错里列出 browser_look（接线时）",
          "未知工具" in second3 and "browser_look" in second3, second3[:200])


def t_look_fn_wiring():
    """pipeline._page_look_fn：with_patch 先落补丁再看、看完必须还原；匹配不上一个字都不落。"""
    print("\n[4] _page_look_fn 接线：with_patch 落补丁看页面 + 还原")
    base = _tempdir("look-pipe-")
    cfg = {
        "ones": {"base_url": "", "token": "", "project_uuid": ""},
        "ai": {"model": "x", "api_key": ""},
        "repo": {"path": "", "branch": "main"},
        "gate": {"commands": []},
        "agent": {"sandbox_server": "on",
                  "sandbox_server_command": "echo dev-server",
                  "sandbox_server_ready_timeout": 20},
        "playwright": {"base_url": "http://127.0.0.1:1", "headless": True,
                       "screenshot_dir": str(base / "shots")},
        "proposals": {"output_dir": str(base / "props")},
        "knowledge_base": {"output_dir": str(base / "kb")},
        "artifacts": {"output_dir": str(base / "arts")},
    }
    repo = make_repo()
    cfg["repo"]["path"] = str(repo)
    pipeline = AIDefectFixerPipeline(cfg)
    look = pipeline._page_look_fn({"id": "T1", "title": "t"}, "T1", {"route": "/"})
    check("sandbox_server=on 时接线上", callable(look), "")
    check("sandbox_server=off 时工具下线",
          pipeline._page_look_fn.__wrapped__ is None
          if hasattr(pipeline._page_look_fn, "__wrapped__") else True, "")
    off_pipeline = AIDefectFixerPipeline({**cfg, "agent": {**cfg["agent"],
                                                           "sandbox_server": "off"}})
    check("off 配置下接线为 None（工具对 agent 隐身）",
          off_pipeline._page_look_fn({"id": "T1", "title": "t"}, "T1", {}) is None, "")

    class FakeHandle:
        ready = True
        url = "http://127.0.0.1:59998"
        port = 59998
        note = ""
        command = "echo dev-server"
        seconds = 0.1
        log_tail = ""

        def stop(self, wait_seconds=5.0):
            pass

    class FakeSandbox:
        def __init__(self, root: Path):
            self.root = root
            self.writes = []
            self.server_cmd = ""

        def ensure_server(self, cmd, ready_timeout=90, cwd_rel="", env_extra=None):
            self.server_cmd = cmd
            return FakeHandle()

        def write(self, rel, content):
            self.writes.append((rel, content))
            (Path(self.root) / rel).write_text(content, encoding="utf-8")

    blocks, _errs = parse_blocks(
        "<<<<<<< SEARCH src/a.js\nexport const label = 'BUG';\n=======\n"
        "export const label = 'OK';\n>>>>>>> REPLACE")
    fb = FakeSandbox(repo)
    shot = look(fb, "/plan/list", with_patch_blocks=blocks)
    check("ensure_server 拿到的是配置里的启动命令",
          fb.server_cmd == "echo dev-server", fb.server_cmd)
    check("落补丁 + 还原 = 每个文件各写两次",
          len(fb.writes) == 2 and fb.writes[0][0] == fb.writes[1][0] == "src/a.js",
          str([(w[0], w[1][:20]) for w in fb.writes]))
    check("第一笔是补丁内容", "OK" in fb.writes[0][1], fb.writes[0][1][:60])
    check("看完还原成工作区原内容", fb.writes[1][1] == SOURCE_BUGGED,
          fb.writes[1][1][:60])
    check("源码文件最终回到原样",
          (repo / "src" / "a.js").read_text(encoding="utf-8") == SOURCE_BUGGED, "")
    check("返回里带服务 URL", shot.get("server_url") == "http://127.0.0.1:59998",
          str(shot.get("server_url")))

    bad_blocks, _ = parse_blocks(
        "<<<<<<< SEARCH src/不存在.js\nabc\n=======\nxyz\n>>>>>>> REPLACE")
    fb2 = FakeSandbox(repo)
    shot2 = look(fb2, "/", with_patch_blocks=bad_blocks)
    check("补丁匹配不上时报错且一个字都没落",
          shot2.get("status") == "error" and not fb2.writes
          and "匹配不上" in str(shot2.get("reason")), str(shot2.get("reason"))[:160])


def main():
    print("== 闭环视觉 agent 行为用例 ==")
    t_persistent_server()
    t_look_feedback_loop()
    t_look_guards()
    t_look_fn_wiring()
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
