# -*- coding: utf-8 -*-
"""参数 schema 自检：元数据不许骗人，端点必须被同源闸门罩住。

守护的是 2026-10-13「参数展示形态改版」的第一层（底座）。改版前一个参数被拆在
三处各写一份（标签/说明在 Vue 模板、范围在模板 + config.py 的 clamp、默认值在
composable），所以形态怎么改都收不住。现在统一由 web/params_schema.py 供给，
这条用例查的就是这份供给可不可信：

* schema 自己写错（默认值越界、id 撞车、kind 没有渲染器、必填被折叠藏起来）
  → 界面上会以「填了却跑不对」的形式还回来，必须在这里先红；
* 分档规则（strip / rail）要可解释，加参数会自动翻档，不是每个模块手挑；
* 新端点 /api/params/schema 同样受本机同源闸门保护（它只读，但闸门不能有洞）。

跑法（不依赖 cwd）： PYTHONUTF8=1 .venv/Scripts/python.exe tests/check_params_schema.py
"""
import http.client
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import temp_server  # noqa: E402

from web import params_schema as ps  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


# ---------------------------------------------------------------- 1. 元数据自检
problems = ps.validate()
check("schema.validate() 无问题", not problems, "; ".join(problems[:4]))

MODULE_NAMES = ("req", "defect", "codetest", "apidebug", "reqdev", "team")
check("六个任务模块齐", all(m in ps.MODULES for m in MODULE_NAMES), str(list(ps.MODULES)))

all_ids, missing_default = [], []
for m in MODULE_NAMES:
    for f in ps.MODULES[m]["fields"]:
        all_ids.append(f["id"])
        if "default" not in f:
            missing_default.append(f"{m}.{f['key']}")
        if f["kind"] not in ps._KINDS:
            FAIL.append(f"{m}.{f['key']} kind 未知")
# 前端要靠 default 出「这项没动过」的角标，缺一个就少一个提示
check("每条参数都有 default（角标与重置要用）", not missing_default, ",".join(missing_default))
dup = {i for i in all_ids if all_ids.count(i) > 1}
check("DOM id 全局唯一（用例按 id 点填）", not dup, str(sorted(dup)))
check("id 沿用改版前契约", {"opt-limit", "team-title", "req-figma-mode",
                            "api-doc", "test-file", "rq-doc"} <= set(all_ids))

# ---------------------------------------------------------------- 2. 分档规则
expect = {"defect": "strip", "codetest": "strip",
          "apidebug": "rail", "req": "rail", "reqdev": "rail", "team": "rail"}
got = {m: ps.layout_of(m) for m in MODULE_NAMES}
check("分档结果符合预期", got == expect, str(got))

# 规则必须是「可解释的客观条件」：给 strip 模块加字段/加长文本会自动翻档。
# 用 try/finally 还原，别把凑数字段留在 MODULES 里污染后面的断言。
mod = ps.MODULES["defect"]
_orig = list(mod["fields"])
try:
    check("4 条参数的 defect 默认是 strip", ps.layout_of("defect") == "strip")
    mod["fields"] += [ps._f(f"x{i}", f"opt-x{i}", "凑数", "text",
                            group=ps.COMMON, default="") for i in (1, 2, 3)]
    check("字段数超过 6 条自动翻成 rail", ps.layout_of("defect") == "rail",
          f"{len(_orig)} -> {len(mod['fields'])} 条")
    mod["fields"] = _orig + [ps._f("x9", "opt-x9", "长文本", "textarea",
                                   group=ps.COMMON, default="",
                                   rows=ps.LONG_TEXTAREA_ROWS)]
    check("含长文本（rows>=8）也翻成 rail", ps.layout_of("defect") == "rail",
          f"{len(mod['fields'])} 条")
    mod["fields"] = _orig + [ps._f("x8", "opt-x8", "短文本域", "textarea",
                                   group=ps.COMMON, default="", rows=4)]
    check("4 行的短文本域不算长文本，仍是 strip", ps.layout_of("defect") == "strip")
finally:
    mod["fields"] = _orig
check("还原后 defect 仍是 strip", ps.layout_of("defect") == "strip")

# ---------------------------------------------------------------- 3. 端点
def hit(port, path, *, headers=None):
    """原始 GET：只有 http.client 能精确控制 Origin（沿用 check_origin_guard 的做法）。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request("GET", path, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        conn.close()


with temp_server.serve() as srv:
    data = srv.api("/api/params/schema")
    check("端点里 problems 为空（schema 没骗人）", data.get("problems") == [],
          str(data.get("problems"))[:120])
    check("端点透出五个模块", sorted(data.get("modules") or {}) == sorted(MODULE_NAMES),
          str(sorted(data.get("modules") or {})))
    defect = (data.get("modules") or {}).get("defect") or {}
    check("端点带 layout 与 groups", defect.get("layout") == "strip"
          and bool(defect.get("groups")), str(defect.get("layout")))
    fld = next((f for f in defect.get("fields") or [] if f["id"] == "opt-limit"), {})
    check("number 参数带范围与单位", fld.get("min") == 1 and fld.get("max") == 20
          and fld.get("unit") == "个", str(fld))
    # 只读端点也不能漏出闸门：跨源 Origin 必须 403
    code2, _ = hit(srv.port, "/api/params/schema", headers={"Origin": "http://evil.example"})
    check("跨源 Origin 被闸门拒", code2 == 403, f"code={code2}")
    code3, _ = hit(srv.port, "/api/params/schema", headers={"Origin": srv.base})
    check("同源 Origin 放行", code3 == 200, f"code={code3}")

print(f"\n合计 {len(FAIL)} 个失败")
sys.exit(1 if FAIL else 0)
