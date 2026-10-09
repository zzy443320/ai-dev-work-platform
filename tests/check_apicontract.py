# -*- coding: utf-8 -*-
"""接口契约护栏与后端归因证据的用例。

为什么单独一份：这批规则的作用是**拦住某种行为**（拿容错代替查证、凭语义改字段名、
无证据归因后端）。拦住型规则有两种失败：漏拦（还是把后端问题当缺陷修）和误拦
（普通 UI 改动被反复要求交证据，模型学会怎么绕检查而不是去查真相）。所以两边都要钉。

用例里的行为样本全部来自 2026-10-09 首批真实工单：
  · 把筛选项字段名改成 `modelType` 并用交叉类型断言绕开 typings 不一致；
  · 详情接口结构与列表不一致 → 写归一化兼容层；
  · 企业列表为空 → 拿不到响应样本就把 list/rows/items/data 全列进候选。
"""
import http.server
import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_fix_agent as F  # noqa: E402  复用临时仓库/假模型/补丁回复夹具
from scripts import apicontract as AC  # noqa: E402
from scripts.chat import RepoReader  # noqa: E402
from scripts.fix_agent import (CONCLUSION_BACKEND, CONCLUSION_BACKEND_UNVERIFIED,  # noqa: E402
                               CONCLUSION_ADAPTED, CONCLUSION_VERIFIED, FixAgent)

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def ch(path, diff):
    return {"file_path": path, "diff": diff}


# ------------------------------------------------------------------ 识别与旁证
def t_detection():
    print("\n[1] 契约面改动的识别（要准，不能误伤普通改动）")
    d = ch("a/api.ts", "\n".join([
        "@@ -1,2 +1,3 @@",
        "-  params.resourceType = v",
        "+  params.modelType = v",
        '+  const body = { scope: "all" }',
        '+  return http.get("/api/ai/runtime-logs?pageSize=20")',
    ]))
    got = AC.contract_tokens([d])
    check("改动的请求字段被抓到", "modelType" in got["fields"] and "scope" in got["fields"],
          str(got))
    check("URL 路径与查询参数名被抓到", "/api/ai/runtime-logs" in " ".join(got["urls"])
          and "pageSize" in got["fields"], str(got["urls"]))

    pure_ui = ch("a/View.vue", "\n".join([
        "@@ -1 +1 @@", "+  const tabs = [{ label: '企业', key: 'ent' }]"]))
    check("普通 UI 配置不算契约改动", AC.contract_tokens([pure_ui])["fields"] == [],
          str(AC.contract_tokens([pure_ui])))

    guess = ch("a/api.ts", "\n".join([
        "@@ -53,2 +53,8 @@",
        "+    const candidates = [",
        "+      response.data.list,", "+      response.data.rows,",
        "+      response.data.items,", "+    ];"]))
    sg = AC.shape_guessing([guess])
    check("一次兼容 3 个响应字段被认出（首批第 3 条的行为）",
          sg and sorted(sg[0]["fields"]) == ["items", "list", "rows"], str(sg))
    two = ch("a/api.ts", "+  if (Array.isArray(response.data.content)) return response.data.content;"
             "\n+  if (Array.isArray(response.data.records)) return response.data.records;")
    check("只补两个字段不误拦（避免模型学会绕检查）", AC.shape_guessing([two]) == [],
          str(AC.shape_guessing([two])))
    methods = ch("a/x.ts", "+  const a = response.data.list\n+  const b = response.data.map\n"
                 "+  const c = response.data.filter\n+  const d = response.data.length")
    check("数组方法不被当成接口字段", AC.shape_guessing([methods]) == [],
          str(AC.shape_guessing([methods])))

    hatch = ch("a/api.ts", "+  const p = params as Record<string, unknown> & Extra\n"
               "+  // @ts-ignore\n+  return x as any")
    eh = AC.escape_hatches([hatch])
    check("绕过类型系统的写法被抓出（as any / @ts-ignore / 交叉断言）", len(eh) >= 3, str(eh))


def t_corroborate_and_citations():
    print("\n[2] 旁证与引用核查（编的要被戳穿）")
    repo = F._tempdir("contract-repo-")
    (repo / "packages").mkdir()
    (repo / "packages" / "api.ts").write_text(
        "export interface Q { modelType: string }\n", encoding="utf-8")
    (repo / "packages" / "mine.ts").write_text(
        "const params = { modelType: x }\n", encoding="utf-8")
    check("字段在别的文件里另有出处 → 有旁证",
          AC.corroborate(repo, "modelType", ["packages/mine.ts"]) != [])
    check("只出现在被改文件里 → 判为无据改名",
          AC.corroborate(repo, "modelType", ["packages/api.ts", "packages/mine.ts"]) == [],
          "排除自身后应为空")
    check("不存在的字段也无旁证", AC.corroborate(repo, "totallyMadeUpField", []) == [])

    cites = AC.parse_citations("依据 packages/api.ts:2 与 packages/nope.ts:9 和 docs/a.md:1-3")
    check("引用被解析出来", ("packages/api.ts", 2) in cites and len(cites) == 3, str(cites))
    good, bad = AC.verify_citations(RepoReader(str(repo)), cites)
    check("真存在的引用通过", any(c["ref"] == "packages/api.ts:2" for c in good), str(good))
    check("编造的路径被戳穿", "packages/nope.ts:9" in bad and "docs/a.md:1" in "".join(bad),
          str(bad))


def t_probe():
    print("\n[3] 只读探测：结构可取，数据不外流，主机要白名单")
    payload = {"data": {"list": [{"name": "生产数据-机密-A3X9K", "count": 2}]},
               "total": 7, "token": "sk-secret-abcdef"}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{port}/api/list?pageSize=20"
        r = AC.api_probe(url, allowed_hosts=["127.0.0.1"])
        check("探测成功并回结构", r["ok"] and any(x.startswith("data.list[]") or
                                              "list" in x for x in r.get("shape", [])),
              str(r.get("shape"))[:160])
        check("字段值一个都不回（生产数据不进 prompt/提案）",
              "生产数据-机密" not in json.dumps(r, ensure_ascii=False)
              and "sk-secret-abcdef" not in json.dumps(r, ensure_ascii=False),
              str(r)[:200])
        check("查询参数只留名字不留值", r.get("query_param_names") == ["pageSize"]
              and "pageSize=20" not in r["url"], str(r.get("url")))
        off = AC.api_probe("http://192.0.2.10/api/x", allowed_hosts=["127.0.0.1"])
        check("白名单外的主机直接拒", off["ok"] is False and "不在允许列表" in off["error"],
              str(off)[:120])
        bad = AC.api_probe("ftp://127.0.0.1/x", allowed_hosts=["127.0.0.1"])
        check("非 http(s) 拒", bad["ok"] is False)
    finally:
        srv.shutdown()


# ------------------------------------------------------------- 循环里的硬拦
def agent_with_guard(repo, replies, probe_fn=None):
    ai = F.FakeAI(replies)
    conf = {"max_rounds": 4, "deadline_seconds": 200, "max_stall": 9,
            "per_command_timeout": 60, "repro": "off", "api_probe": "auto"}
    agent = FixAgent(str(repo), ai, agent_cfg=conf,
                     gate_cfg={"commands": [{"name": "check", "cmd": F.CHECK_CMD}]},
                     api_probe_fn=probe_fn)
    agent.verdict = {}
    return agent, ai


def t_guard_in_loop():
    print("\n[4] 护栏在循环里真的拦")
    repo = F.make_repo()
    before = F.snapshot(repo)
    bad_patch = ("这次把字段名改成 modelType\n"
                 "<<<<<<< SEARCH src/a.js\nexport const label = 'BUG';\n=======\n"
                 "export const label = 'modelType is BUG';\n>>>>>>> REPLACE")
    # 直接构造一个含 params 改名的补丁（无旁证）
    sneaky = ("<<<<< 占位\n<<<<<<< SEARCH src/a.js\nexport const label = 'BUG';\n=======\n"
              "export const label = 'BUG';\nconst params = { modelType: x };\n>>>>>>> REPLACE")
    agent, ai = agent_with_guard(repo, [sneaky, bad_patch, bad_patch])
    res = agent.run({"id": "C1", "title": "x"})
    at = (res.get("attempts") or [{}])[0]
    check("无旁证的请求字段被拒绝", at.get("contract_blocked") is True
          or (at.get("errors") and any("modelType" in str(e) for e in at["errors"])),
          str(at.get("errors"))[:220])
    check("拒绝理由给了出路（补证据或改判后端）",
          any("api_probe" in str(e) or "backend_issue" in str(e)
              for e in (at.get("errors") or [])), str(at.get("errors"))[:220])
    check("被拦时一个字都没写进仓库", F.snapshot(repo) == before)

    # 有旁证 → 放行
    (repo / "typings.ts").write_text("export interface Q { modelType: string }\n",
                                     encoding="utf-8")
    agent2, _ai2 = agent_with_guard(repo, [sneaky])
    res2 = agent2.run({"id": "C2", "title": "x"})
    notes = (res2.get("contract_notes") or []) + [
        n for a in res2["attempts"] for n in (a.get("contract_notes") or [])]
    check("有旁证时放行并记录出处", any("旁证" in n for n in notes), str(notes)[:200])


def t_verdict_flow():
    print("\n[5] 后端归因：有据采信、无据降级、兼容层单独成档")
    repo = F.make_repo()
    (repo / "packages").mkdir()
    (repo / "packages" / "api.ts").write_text(
        "export interface LogQuery { resourceType: string } // 后端契约仍是 resourceType\n",
        encoding="utf-8")
    good = json.dumps({"action": "verdict", "kind": "backend_issue",
                       "endpoint": "/api/ai/runtime-logs",
                       "expected": "data 为数组且每项含 type: string",
                       "actual": "data 缺失，结构与列表接口不一致",
                       "citations": ["packages/api.ts:1"],
                       "handoff": "请后端统一两个接口的 resource 结构"}, ensure_ascii=False)
    agent, _ai = agent_with_guard(repo, [good])
    res = agent.run({"id": "V1", "title": "x"})
    check("有仓库引用的后端归因被采信", res["conclusion"] == CONCLUSION_BACKEND,
          res["conclusion"])
    check("引用被系统真读到并列进结论",
          (res.get("verdict") or {}).get("citations_ok") == ["packages/api.ts:1"],
          str(res.get("verdict"))[:200])
    check("这种提案不需要补丁", res["patch_text"] == "", str(res["patch_text"])[:80])
    check("needs_human 为真（要人转交）", res["needs_human"] is True)

    fake = json.dumps({"action": "verdict", "kind": "backend_issue",
                       "citations": ["packages/does-not-exist.ts:99"],
                       "handoff": "看着像后端问题"}, ensure_ascii=False)
    agent2, _ai2 = agent_with_guard(repo, [fake, fake, fake])
    res2 = agent2.run({"id": "V2", "title": "x"})
    check("引用是编的 → 记为未验证归因，不冒充定论",
          res2["conclusion"] == CONCLUSION_BACKEND_UNVERIFIED, res2["conclusion"])
    check("编造路径被列进 citations_bad",
          (res2.get("verdict") or {}).get("citations_bad"), str(res2.get("verdict"))[:160])

    # 兼容层：补丁动了契约面（有旁证所以放行）→ 跑绿后系统会专门问一次归因 → 模型交 verdict
    repo3 = F.make_repo()
    (repo3 / "typings.ts").write_text("export interface Q { scopeAll: string }\n",
                                      encoding="utf-8")
    compat = ("<<<<<<< SEARCH src/a.js\nexport const label = 'BUG';\n=======\n"
              "export const label = 'OK';\nconst params = { scopeAll: 'all' };\n>>>>>>> REPLACE")
    adapted = json.dumps({"action": "verdict", "kind": "adapted_pending_backend",
                          "handoff": "详情接口 resource 结构与列表不一致，建议后端统一",
                          "cleanup": "后端统一后移除 getResourceTypeText 里的对象兼容分支"},
                         ensure_ascii=False)
    agent3, _ai3 = agent_with_guard(repo3, [compat, adapted])
    res3 = agent3.run({"id": "V3", "title": "x"})
    check("动了契约面会被专门问一次归因",
          any("adapted_pending_backend" in p["prompt"] for p in _ai3.prompts),
          str([p["prompt"][-160:] for p in _ai3.prompts])[:200])
    check("补丁有效 + 标为「等上游契约」", res3["conclusion"] == CONCLUSION_ADAPTED,
          res3["conclusion"])
    check("撤销点被记下来", "对象兼容分支" in str((res3.get("verdict") or {}).get("cleanup")),
          str(res3.get("verdict"))[:160])
    check("兼容层本身是跑绿的补丁（结论降级不等于补丁无效）",
          (res3["attempts"][-1].get("conclusion") == CONCLUSION_VERIFIED),
          str([a.get("conclusion") for a in res3["attempts"]])[:160])
    check("这类提案 needs_human 为真，提醒人去追上游", res3["needs_human"] is True)
    check("探针与归因都不影响零写入",
          (repo3 / "src" / "a.js").read_text(encoding="utf-8") == F.SOURCE_BUGGED)


def t_probe_in_loop():
    print("\n[6] 探测结果能让「全都兼容一遍」的补丁过关（有真相就放行）")
    repo = F.make_repo()
    guess = ("<<<<<<< SEARCH src/a.js\nexport const label = 'BUG';\n=======\n"
             "const label = 'BUG';\n"
             "const pick = (r) => [r.data.list, r.data.rows, r.data.items].find(Array.isArray);\n"
             ">>>>>>> REPLACE")
    probe = lambda url: {"ok": True, "status": 200, "content_type": "application/json",
                         "url": url, "bytes": 40,
                         "shape": ["data.list: array(len=2)", "data.total: int"]}
    probe_call = json.dumps({"action": "call", "tool": "api_probe",
                             "arguments": {"url": "http://127.0.0.1:9/api/list"}})
    agent, ai = agent_with_guard(repo, [probe_call, guess, guess], probe_fn=probe)
    res = agent.run({"id": "P1", "title": "x"})
    notes = [n for a in res["attempts"] for n in (a.get("contract_notes") or [])]
    check("探测证实的字段被放行并注明", any("接口探测证实" in n for n in notes), str(notes)[:220])
    check("探测记录进了结论", res.get("probes"), str(res.get("probes"))[:120])
    check("探测只回结构，没把值带进 prompt",
          "list" in json.dumps(res["probes"], ensure_ascii=False))

    agent2, _ai2 = agent_with_guard(repo, [guess, guess, guess])   # 没有 probe_fn
    res2 = agent2.run({"id": "P2", "title": "x"})
    at2 = (res2.get("attempts") or [{}])[0]
    check("没有真相时仍被拦", at2.get("contract_blocked") is True, str(at2)[:160])


def main():
    print("== 接口契约护栏与后端归因证据 ==")
    t_detection()
    t_corroborate_and_citations()
    t_probe()
    t_guard_in_loop()
    t_verdict_flow()
    t_probe_in_loop()
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
