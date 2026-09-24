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
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import artifact as artifact_mod  # noqa: E402
from scripts import chat as chat_mod  # noqa: E402
from scripts import usage as usage_mod  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.chat import ChatStore, RepoReader, mock_chat_reply  # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    (OK if cond else BAD).append(name)
    return cond


# --------------------------------------------------------------- 假 AI
class FakeAI:
    """按脚本逐轮返回。每轮把 prompt 存下来，便于断言提示词里有什么。"""

    def __init__(self, replies, mode="openai", error_at=None):
        self.replies = list(replies)
        self.mode = mode
        self.prompts = []
        self.systems = []
        self.error_at = error_at
        self.calls = 0

    def complete_text(self, prompt, system="", max_tokens=None, on_delta=None):
        self.prompts.append(prompt)
        self.systems.append(system)
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
