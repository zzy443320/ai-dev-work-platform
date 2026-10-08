# -*- coding: utf-8 -*-
"""「问答」模块的离线自检（不联网、不起服务、不调真实模型）。

覆盖：
1. ChatStore：落盘 / 追加 / 自动起标题 / 列表按更新时间倒序 / 清空 / 删除，
   以及会话 id 被当成路径片段时的 slug 安全；
2. RepoReader 路径安全：`../` 越界、绝对路径、盘符、不存在文件一律拒绝——
   问答是「只读」能力，越界读取是唯一真正危险的动作；
3. RepoReader 三个只读工具的真实行为：列目录、带行号读文件、正则搜索（含
   二进制跳过、glob 过滤、max_hits 上限、坏正则的友好报错）；
4. tools_enabled=False（用户关掉「允许查仓库」）时工具表为空；
5. 工具调用解析 `_as_tool_call`：只认「整段就是一个 action=call 的 JSON」，
   正文里的 JSON 例子、无 action 的 JSON、超长文本都不算；
6. `<PROPOSAL>` 抽取 `split_proposal`：无块 / 合法块 / 块后还有正文 /
   块内 JSON 坏掉（保留原文并给出原因，不能静默吞掉）；
7. `proposal_payload` 归一：缺 content 的文件被丢弃，全空时给出 error；
8. `run_chat` 多轮循环（假 AI）：先调工具再回答、工具事件与增量事件都发出、
   提案从回答里摘掉、模型报错如实返回、最后一轮仍在调工具不会死循环；
9. mock 输出明确标注 mock，且只在「像要改代码」时才附演示提案；
10. 用量/产出物的类型字典已登记 chat（否则统计面板与产出物面板看不到它）。

用法：
    python tests/check_chat.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import artifact as artifact_mod  # noqa: E402
from scripts import chat as chat_mod  # noqa: E402
from scripts import chat_attachments as att_mod  # noqa: E402
from scripts import usage as usage_mod  # noqa: E402
from scripts import ai_providers  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.chat import ChatStore, RepoReader, mock_chat_reply  # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


def _rejects(fn) -> bool:
    """调用必须抛出 AttachmentError（用来断言「不该被接受的附件确实被拒了」）。"""
    try:
        fn()
    except att_mod.AttachmentError:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------- 假 AI
class FakeAI:
    """按脚本逐轮返回。每轮把 prompt 存下来，便于断言提示词里有什么。"""

    def __init__(self, replies, mode="openai", error_at=None):
        self.replies = list(replies)
        self.mode = mode
        self.prompts = []
        self.systems = []
        self.images = []
        self.error_at = error_at
        self.calls = 0

    def complete_text(self, prompt, system="", max_tokens=None, on_delta=None,
                      images=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        self.images.append(images)
        idx = self.calls
        self.calls += 1
        if self.error_at is not None and idx == self.error_at:
            return {"text": "", "error": "网关 502（测试注入）"}
        text = self.replies[idx] if idx < len(self.replies) else self.replies[-1]
        if on_delta:
            on_delta("reasoning", "想一下…")
            on_delta("content", text)
        return {"text": text, "error": ""}


def make_repo(root: Path) -> None:
    (root / "src" / "views").mkdir(parents=True, exist_ok=True)
    (root / "src" / "views" / "Schedule.vue").write_text(
        "<template>\n  <div>{{ title }}</div>\n</template>\n\n"
        "<script setup lang=\"ts\">\nconst title = '日程';\n</script>\n",
        encoding="utf-8")
    (root / "src" / "api.ts").write_text(
        "export const WEEK_TOKEN = 'week';\n"
        "export function getWeek() { return WEEK_TOKEN; }\n",
        encoding="utf-8")
    (root / "README.md").write_text("# demo\n周视图配置在 src/api.ts\n", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02\x03week\x00")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="chat-check-"))
    try:
        sessions = tmp / "sessions"
        repo = tmp / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        make_repo(repo)

        # ── 1. 会话存储 ──
        print("\n[1] ChatStore 会话存储")
        st = ChatStore(str(sessions))
        s1 = st.create()
        check("会话落盘", (sessions / f"{s1['id']}.json").is_file(), s1["id"])
        check("新会话默认标题", s1["title"] == "新会话", s1["title"])
        st.append(s1["id"], "user", "这个项目的路由是怎么配的？")
        st.append(s1["id"], "assistant", "看 `src/router/index.ts`。", {"tools": []})
        got = st.get(s1["id"])
        check("消息按顺序追加", [m["role"] for m in got["messages"]] == ["user", "assistant"],
              str([m["role"] for m in got["messages"]]))
        check("首条用户消息自动当标题",
              got["title"].startswith("这个项目的路由"), got["title"])
        check("assistant 的 meta 被保留", "meta" in got["messages"][1])

        s2 = st.create()
        st.append(s2["id"], "user", "第二条会话")
        lst = st.list()
        check("列表按更新时间倒序（最新在前）", lst and lst[0]["id"] == s2["id"],
              str([x["id"] for x in lst]))
        check("列表带条数与预览", lst[0]["count"] == 1 and "第二条" in lst[0]["preview"],
              json.dumps(lst[0], ensure_ascii=False))

        # 清空保留会话、删除移除文件
        st.clear(s2["id"])
        check("清空后消息为空但会话还在",
              st.get(s2["id"])["messages"] == [] and st.get(s2["id"]) is not None)
        check("删除会话", st.delete(s2["id"]) and st.get(s2["id"]) is None)
        check("删除不存在的会话返回 False", st.delete("chat-nope") is False)

        weird = st.append("../../evil", "user", "x")
        check("会话 id 被 slug 化（不越出目录）",
              not (tmp / "evil.json").exists(), "evil.json 不应出现在临时根")
        check("slug 化后的会话可读回", st.get("../../evil") is not None)
        st.delete(weird["id"])

        # ── 2/3. 只读检索 ──
        print("\n[2] RepoReader 路径安全")
        rd = RepoReader(str(repo))
        for bad, label in [("../../etc/passwd", "父目录穿越"),
                           ("C:/Windows/win.ini", "绝对路径/盘符"),
                           ("src/../../outside.txt", "绕行穿越")]:
            out = rd.read_file(bad)
            check(f"拒绝 {label}", out.startswith("[错误]"), out[:70])
        check("拒绝越界列目录", rd.list_dir("../").startswith("[错误]"))
        check("读不存在的文件给出可读错误",
              rd.read_file("src/nope.vue").startswith("[错误]"))
        check("坏正则不抛异常", rd.grep("[unclosed").startswith("[错误]"))
        check("空 pattern 被拒", rd.grep("").startswith("[错误]"))

        print("\n[3] RepoReader 三个只读工具")
        ls = rd.list_dir("src")
        check("列目录包含子目录与文件", "src/views/" in ls and "src/api.ts" in ls, ls.replace("\n", " | ")[:100])
        rf = rd.read_file("src/api.ts")
        check("读文件带行号", "1\texport const WEEK_TOKEN" in rf, rf[:80].replace("\n", " "))
        rf2 = rd.read_file("src/api.ts", 2, 2)
        check("按行区间读取只回那一行", "getWeek" in rf2 and "WEEK_TOKEN = 'week'" not in rf2,
              rf2.replace("\n", " | ")[:90])
        g = rd.grep("WEEK_TOKEN")
        check("grep 命中并给出 路径:行号", "src/api.ts:1:" in g, g.replace("\n", " | ")[:100])
        check("grep 跳过二进制文件", "bin.dat" not in rd.grep("week"))
        g1 = rd.grep("week", max_hits=1)
        check("grep 尊重 max_hits", g1.count(":") >= 1 and "上限" in g1, g1.replace("\n", " | ")[:80])
        check("grep 支持 glob 过滤",
              "README.md" in rd.grep("周视图", glob=".md")
              and "README.md" not in rd.grep("周视图", glob=".ts"))
        check("工具清单三项",
              [t["name"] for t in rd.spec()] == ["repo_list", "repo_read", "repo_grep"],
              str([t["name"] for t in rd.spec()]))
        check("未知工具返回错误文本而不是抛异常",
              rd.call("repo_rm", {}).startswith("[错误]"))
        rd_off = RepoReader(str(repo), tools_enabled=False)
        check("关掉仓库检索后工具表为空", rd_off.spec() == [])

        # ── 3b. 大仓 grep 引擎：git grep 优先 + 截断诚实化 ──
        # 真实案例（2026-09-30）：8000+ 文件的单仓，语言包文件排在 ls-files 序的 6000 之后，
        # 旧实现 6000 处静默截断 → 模型只找到 $t() 引用、找不到定义，还断言「全仓只有引用」。
        print("\n[3b] repo_grep 大仓引擎（定义 vs 引用）")
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
        (repo / "packages" / "locale" / "locales").mkdir(parents=True, exist_ok=True)
        (repo / "packages" / "locale" / "locales" / "zh-CN.ts").write_text(
            "export default {\n  personalization: {\n    personalizationSaved: '保存成功',\n  },\n};\n",
            encoding="utf-8")
        (repo / "packages" / "app" / "pages").mkdir(parents=True, exist_ok=True)
        (repo / "packages" / "app" / "pages" / "index.vue").write_text(
            "<script setup>\n"
            "const tip = $t('languages.aiPlatform.personalization.personalizationSaved');\n"
            "</script>\n", encoding="utf-8")
        rd2 = RepoReader(str(repo))  # 新实例：重扫文件清单（含刚加的语言包/页面）
        gdef = rd2.grep("personalizationSaved")
        check("定义与引用都命中（语言包 + 调用点）",
              "locales/zh-CN.ts" in gdef and "pages/index.vue" in gdef,
              gdef.replace("\n", " | ")[:160])
        check("定义行带字段名与值", "personalizationSaved: '保存成功'" in gdef)
        check("git 引擎无扫描截断提示", "⚠" not in gdef and "截" not in gdef)
        gts = rd2.grep("personalizationSaved", glob=".ts")
        check("glob 限定扩展名走 pathspec（只剩 .ts 命中）",
              "zh-CN.ts" in gts and "index.vue" not in gts, gts.replace("\n", " | ")[:120])
        gvue = rd2.grep("personalizationSaved", glob="packages/app/**")
        check("glob 限定目录（:(glob) 前缀）",
              "index.vue" in gvue and "zh-CN.ts" not in gvue, gvue.replace("\n", " | ")[:120])
        gnone = rd2.grep("不存在的词xyzzy")
        check("git 引擎无命中时如实报全仓无命中", gnone.startswith("[无命中]") and "全仓" in gnone,
              gnone[:80])
        old_cap = chat_mod.GREP_SCAN_CAP
        chat_mod.GREP_SCAN_CAP = 2
        orig_git_grep = RepoReader._git_grep
        RepoReader._git_grep = lambda self, p, g, m: None  # 强制走 Python 兜底
        try:
            gtrunc = rd2.grep("personalizationSaved")
        finally:
            RepoReader._git_grep = orig_git_grep
            chat_mod.GREP_SCAN_CAP = old_cap
        check("兜底扫描截断时如实提示前缀覆盖（不是全仓结论）",
              "前缀" in gtrunc and "不要把本结果当成全仓结论" in gtrunc,
              gtrunc.replace("\n", " | ")[:200])
        check("截断提示带仓库文件总数", "个文件" in gtrunc)

        # ── 4/5. 提示词与工具调用解析 ──
        print("\n[4] 提示词与工具调用解析")
        prompt = chat_mod.build_prompt(
            [{"role": "user", "content": "第一个问题"},
             {"role": "assistant", "content": "第一个回答"}], "第二个问题")
        check("历史按先后顺序进入提示词",
              prompt.index("第一个问题") < prompt.index("第一个回答") < prompt.index("第二个问题"))
        check("空历史也不炸", "（无，这是第一句）" in chat_mod.build_prompt([], "只问一句"))

        tc = chat_mod._as_tool_call('{"action": "call", "tool": "repo_grep", '
                                    '"arguments": {"pattern": "week"}}')
        check("识别标准工具调用", tc and tc["tool"] == "repo_grep" and tc["arguments"]["pattern"] == "week")
        tc2 = chat_mod._as_tool_call('```json\n{"action":"call","tool":"repo_read",'
                                     '"arguments":{"path":"a.ts"}}\n```')
        check("识别带围栏的工具调用", tc2 and tc2["tool"] == "repo_read")
        check("正文不算工具调用",
              chat_mod._as_tool_call("这段配置是这样：\n```json\n{\"a\":1}\n```") is None)
        check("有 JSON 但没有 action 不算",
              chat_mod._as_tool_call('{"summary": "x"}') is None)
        check("超长文本不算工具调用",
              chat_mod._as_tool_call('{"action":"call", "x":"' + "a" * 5000 + '"}') is None)
        # DeepSeek 系模型会把内部 DSML 工具模板泄漏进正文（真实案例 2026-09-29）：
        # JSON 工具调用 + <｜｜DSML｜｜ ...> 泄漏块。要能识别调用且把泄漏洗掉。
        dsml_case = ('{"action": "call", "tool": "repo_grep", '
                     '"arguments": {"pattern": "定时任务"}}\n\n'
                     '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="repo_grep">\n'
                     '<｜｜DSML｜｜ parameter name="arguments" string="false">'
                     '{"pattern": "定时任务", "max_hits": 60}</｜｜DSML｜｜ parameter>\n'
                     '</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>')
        tc3 = chat_mod._as_tool_call(dsml_case)
        check("DSML 泄漏不挡工具调用识别", tc3 and tc3["tool"] == "repo_grep")
        cleaned = chat_mod._strip_dsml(dsml_case)
        check("DSML 泄漏块被整块清洗",
              "DSML" not in cleaned and "invoke" not in cleaned
              and cleaned.startswith('{"action"'))
        check("无 DSML 的文本原样返回",
              chat_mod._strip_dsml("普通回答，没有泄漏。") == "普通回答，没有泄漏。")

        # ── 6/7. 提案抽取 ──
        print("\n[5] 提案抽取与归一")
        plain, p, e = chat_mod.split_proposal("就是一段普通回答。")
        check("没有提案块时原样返回", plain == "就是一段普通回答。" and p is None and e == "")
        body = {"summary": "补一个字段", "files": [
            {"path": "src/api.ts", "action": "overwrite", "content": "export const A = 1;\n"}]}
        txt = ("改法是在 src/api.ts 里加常量。\n\n<PROPOSAL>\n"
               + json.dumps(body, ensure_ascii=False) + "\n</PROPOSAL>\n后面这句不该丢。")
        reply, patch, err = chat_mod.split_proposal(txt)
        check("提案被摘出且不出现在回答里",
              "<PROPOSAL>" not in reply and "src/api.ts 里加常量" in reply, repr(reply[:60]))
        check("提案块后面的正文被保留", "后面这句不该丢" in reply, repr(reply[-30:]))
        check("提案 JSON 解析成功且没有错误", err == "" and patch and patch["summary"] == "补一个字段",
              str(err))
        _, bad_patch, bad_err = chat_mod.split_proposal(
            "<PROPOSAL>\n{不是 JSON}\n</PROPOSAL>")
        check("坏提案给出原因而不是静默丢弃",
              bad_patch is None and "格式不合法" in bad_err, bad_err)

        pay = chat_mod.proposal_payload(body, "openai")
        check("提案归一成产出物 payload",
              pay["task"] == "chat" and len(pay["files"]) == 1 and pay["files"][0]["path"] == "src/api.ts")
        check("缺 content 的文件被丢弃（否则会写坏目标文件）",
              chat_mod.proposal_payload({"files": [{"path": "a.ts", "content": "  "}]})["files"] == [])
        check("没有可用文件时如实报错",
              "没有可用的文件" in chat_mod.proposal_payload({"files": []})["error"])

        # ── 8. run_chat 多轮循环 ──
        print("\n[6] run_chat 多轮循环")
        plan = ('{"action": "call", "tool": "repo_grep", "arguments": {"pattern": "WEEK_TOKEN"}}')
        final = ("周视图的配置在 `src/api.ts:1`。\n\n<PROPOSAL>\n"
                 + json.dumps(body, ensure_ascii=False) + "\n</PROPOSAL>")
        ai = FakeAI([plan, final])
        events = []
        res = chat_mod.run_chat(ai, rd, [], "周视图配置在哪？顺便把常量加上",
                                on_event=events.append, title="t")
        check("两轮完成（先查仓库再回答）", res["rounds"] == 2 and ai.calls == 2,
              f"rounds={res['rounds']} calls={ai.calls}")
        check("仓库工具真的被执行", res["tools"] and res["tools"][0]["name"] == "repo_grep",
              json.dumps(res["tools"], ensure_ascii=False)[:100])
        check("工具结果进入了第二轮提示词",
              "src/api.ts:1:" in ai.prompts[1], ai.prompts[1][-200:].replace("\n", " "))
        check("回答里没有提案块", "<PROPOSAL>" not in res["reply"] and "src/api.ts:1" in res["reply"])
        check("提案被解析成 payload", res["patch"] and res["patch"]["files"])
        kinds = {e.get("type") for e in events}
        check("事件包含 stage / tool / ai_delta",
              {"stage", "tool", "ai_delta"} <= kinds, str(sorted(kinds)))
        check("增量事件区分思考与正文",
              any(e.get("type") == "ai_delta" and e.get("kind") == "reasoning" for e in events)
              and any(e.get("type") == "ai_delta" and e.get("kind") == "content" for e in events))
        check("系统提示词带上提案规则与「不能写文件」的能力边界",
              "<PROPOSAL>" in ai.systems[0] and "没有写文件的权限" in ai.systems[0])
        check("提示词带「找定义而不是只找到引用」的检索指引（i18n/截断诚实化）",
              "$t(" in ai.prompts[0] and "语言包" in ai.prompts[0]
              and "不要当成全仓结论" in ai.prompts[0])
        # 「一直在不停询问」的反面：提示词必须明令禁止反问、禁止向用户索要文件内容
        check("系统提示词明令「不要反问」且禁止让用户贴文件内容",
              "不要反问" in ai.systems[0] and "贴给你" in ai.systems[0], ai.systems[0][-120:])
        check("默认口径写进提示词：改 i18n 各语言一起改，不用先问",
              "zh-TW" in ai.systems[0] and "一起改" in ai.systems[0])
        check("提案规则给了局部改动写法（大文件不必吐全量内容）",
              "action" in ai.systems[0] and "edit" in ai.systems[0] and "edits" in ai.systems[0])
        check("提示词要求「改代码就必须给提案」，堵死用追问收场",
              "一定要给出提案" in ai.systems[0])
        check("读文件指引要求分段读而不是退回去问用户",
              "分段读" in ai.prompts[0] or "start" in ai.prompts[0])

        ai2 = FakeAI(['{"action":"call","tool":"repo_list","arguments":{"path":"src"}}'])
        res2 = chat_mod.run_chat(ai2, rd, [], "列一下 src", max_rounds=2)
        check("轮次用尽不会死循环", ai2.calls == 2 and res2["reply"], f"calls={ai2.calls}")
        check("最后一轮仍是工具调用时给出说明而不是把 JSON 当回答",
              "仍在请求工具" in res2["reply"], res2["reply"][:80])

        # 第一轮先查仓库，第二轮才失败——单轮就答完的脚本根本走不到报错分支
        ai3 = FakeAI([plan, "不会用到"], error_at=1)
        res3 = chat_mod.run_chat(ai3, rd, [], "再问一句")
        check("模型报错如实返回且不抛异常", res3["error"].startswith("网关 502"),
              f"rounds={res3['rounds']} err={res3['error']}")
        check("报错时没有伪造提案", res3["patch"] is None)

        ai4 = FakeAI(["这是最终回答。"])
        off_reader = RepoReader(str(repo), tools_enabled=False)
        res4 = chat_mod.run_chat(ai4, off_reader, [], "只回答不查仓库")
        check("关掉仓库检索时只有一轮", ai4.calls == 1, f"calls={ai4.calls}")
        check("无工具时提示词明说看不到仓库",
              "没有开启仓库访问" in ai4.prompts[0], ai4.prompts[0][-160:].replace("\n", " "))

        ai5 = FakeAI(["不要提案。"])
        res5 = chat_mod.run_chat(ai5, rd, [], "问个问题", allow_patch=False)
        check("allow_patch=False 时不注入提案规则",
              "<PROPOSAL>" not in ai5.systems[0], ai5.systems[0][-80:])

        # ── 6b. 附件（图片多模态 / 文本注入）──
        print("\n[6b] 附件：图片透传与文本注入")
        att = att_mod.ChatAttachmentStore(str(tmp / "attach"))
        img = att.save("报错截图.png", b"\x89PNG\r\n\x1a\n" + b"z" * 20, "image/png")
        log = att.save("console.log", "TypeError: x is undefined".encode(),
                       "application/octet-stream")
        check("图片被识别为 image", img["kind"] == "image", img["kind"])
        check("无 MIME 的 .log 按扩展名识别为文本（不能误判成二进制）",
              log["kind"] == "text", f"{log['kind']} / {log['content_type']}")
        check("落盘文件名由服务端生成（不含用户给的路径）",
              (tmp / "attach" / img["file"]).is_file() and ".." not in img["file"],
              img["file"])
        check("路径穿越的 id 取不到附件",
              att.get("../../../etc/passwd") is None)
        pub = att.public(img)
        check("回显元数据带可预览 url",
              pub["url"].startswith("/attachments/") and pub["size"] > 0,
              json.dumps(pub, ensure_ascii=False))
        check("认不出的文件类型被拒绝（不落盘）",
              _rejects(lambda: att.save("a.zip", b"PK\x03\x04", "application/zip")))
        check("二进制伪装成文本被拒绝",
              _rejects(lambda: att.save("a.log", b"\x00\x01\x02", "text/plain")))

        # 展示名必须与实际落盘后缀一致，否则界面显示 a.sh 而链接打开的是 a.txt
        evil = att.save("../../evil.sh", b"#!/bin/sh\n", "text/plain")
        check("用户给的路径被剥掉（只留基名）",
              evil["name"] == "evil.txt" and ".." not in evil["name"], evil["name"])
        check("展示名后缀与实际落盘后缀一致",
              evil["name"].endswith("." + evil["ext"])
              and Path(evil["file"]).suffix.lstrip(".") == evil["ext"],
              f"{evil['name']} / {evil['ext']} / {evil['file']}")
        check("落盘路径始终在附件目录内",
              Path(evil["path"]).parent == (tmp / "attach").resolve()
              or Path(evil["path"]).parent == (tmp / "attach"),
              evil["path"])

        imgs = att_mod.images_for(att, [img, log])
        check("只有图片进入多模态片段（文本不进）",
              len(imgs) == 1 and imgs[0]["data_uri"].startswith("data:image/png;base64,"),
              str(imgs)[:80])
        tblock = att_mod.text_block(att, [img, log])
        check("文本附件内容被拼进提示词并标注来源",
              "console.log" in tblock and "TypeError" in tblock, tblock[:70])
        check("图片不重复出现在文本块里", "报错截图" not in tblock)

        ai6 = FakeAI(["看到图了。"])
        chat_mod.run_chat(ai6, rd, [], "这张截图是什么问题？",
                          images=imgs, attachment_note=att_mod.attach_note([img, log]),
                          attachment_text=tblock)
        check("图片随第一轮提问发给模型", bool(ai6.images[0]),
              str(ai6.images[0])[:60])
        check("系统提示词告知本轮带图并要求「看不到就说看不到」",
              "本轮带图" in ai6.systems[0] and "看不到图片" in ai6.systems[0])
        check("附件清单与内容都进了提示词",
              "console.log" in ai6.prompts[0] and "用户附件" in ai6.prompts[0],
              ai6.prompts[0][:120].replace("\n", " "))

        ai7 = FakeAI(['{"action":"call","tool":"repo_list","arguments":{"path":"src"}}',
                      "查完了。"])
        chat_mod.run_chat(ai7, rd, [], "带图查一下", images=imgs, max_rounds=3)
        check("图片只在第一轮发送（后续工具轮不重复烧 token）",
              bool(ai7.images[0]) and not ai7.images[1],
              f"first={bool(ai7.images[0])} second={bool(ai7.images[1])}")

        ai8 = FakeAI(["没问题。"])
        chat_mod.run_chat(ai8, rd, [], "纯文字提问")
        check("没有附件时提示词不带附件说明",
              "本轮带图" not in ai8.systems[0] and "用户附件" not in ai8.prompts[0])

        # 多模态请求体形状：两种 provider 的图片块结构完全不同，必须各自正确
        oai = ai_providers.AIConfig.from_dict({"provider": "openai", "api_key": "k",
                                              "base_url": "https://gw/v1", "model": "m"})
        oai_body = oai.build("hi", "sys", imgs)[3]
        oai_content = oai_body["messages"][-1]["content"]
        check("OpenAI 形态：image_url + text 块",
              oai_content[0]["type"] == "image_url"
              and oai_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
              and oai_content[1]["type"] == "text", json.dumps(oai_content)[:100])
        ant = ai_providers.AIConfig.from_dict({"provider": "anthropic", "api_key": "k",
                                              "model": "m"})
        ant_body = ant.build("hi", "sys", imgs)[3]
        ant_content = ant_body["messages"][0]["content"]
        check("Anthropic 形态：base64 image 源块",
              ant_content[0]["type"] == "image"
              and ant_content[0]["source"]["type"] == "base64"
              and ant_content[0]["source"]["media_type"] == "image/png",
              json.dumps(ant_content)[:100])
        check("没有图片时消息体与旧版完全一致（不影响既有链路）",
              ai_providers.AIConfig.from_dict(
                  {"provider": "openai", "api_key": "k", "base_url": "https://gw/v1",
                   "model": "m"}).build("hi", "sys")[3] == oai.build("hi", "sys")[3])
        check("非法图片地址被丢弃（不发网关不认的块）",
              ai_providers._image_blocks([{"data_uri": "ftp://x"},
                                          {"data_uri": "data:image/png;base64,BB"}])
              == [{"type": "image_url",
                   "image_url": {"url": "data:image/png;base64,BB"}}])

        # ── 9. mock ──
        print("\n[7] mock 输出")
        m1 = mock_chat_reply("这个函数是干什么的？")
        check("mock 明确标注 mock", "Mock" in m1["reply"] or "mock" in m1["reply"])
        check("纯提问不附演示提案", m1["patch"] is None)
        m2 = mock_chat_reply("帮我把 loading 状态加上")
        check("像要改代码时附演示提案", m2["patch"] and m2["patch"]["files"])

        # ── 10. 类型登记 ──
        print("\n[8] 类型登记与文本模式接口")
        check("用量类型字典登记了 chat", usage_mod.KIND_LABELS.get("chat") == "问答",
              str(usage_mod.KIND_LABELS.get("chat")))
        check("产出物类型字典登记了 chat", artifact_mod.TYPE_LABELS.get("chat") == "问答",
              str(artifact_mod.TYPE_LABELS.get("chat")))
        check("AIModel 有文本模式接口", callable(getattr(AIModel, "complete_text", None)))
        mockres = AIModel({"mock": True}).complete_text("hi")
        check("mock 模式下 complete_text 不报错", mockres.get("error") == "", str(mockres))

        print(f"\n通过 {len(OK)} 项，失败 {len(BAD)} 项")
        if BAD:
            print("失败项：" + "；".join(BAD))
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
