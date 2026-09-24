# -*- coding: utf-8 -*-
"""回归：更新日志（2026-09-23 新增）。

背景：用户要求在右上角有个入口，能看到「每次改了什么、怎么改的、什么时候改的」。
方案是**以项目根 CHANGELOG.md 为唯一数据源**（scripts/changelog.py 解析、
/api/changelog 只读暴露、前端弹窗展示），不做模型二次归纳——否则「改了什么」
会出现第二个真相源，跟仓库里的文本对不上。

本测试守住两件事：
1. 解析器容错：写日志是顺手的事，格式不能太脆（头部顺序任意、可缺省、
   字段同义词、缩进续行、代码围栏与注释要跳过、认不出的键不能丢）。
2. 真实日志质量：每条都得有日期/标签/标题/内容/做法，否则界面上会出现
   只有标题没有内容的空条，等于没写。

用法：python tests/check_changelog.py（纯离线，不需要服务/目标仓库）
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.changelog import (  # noqa: E402
    TAGS, build_payload, load_changelog, parse_changelog, split_files,
)

CHANGELOG_MD = PROJECT_ROOT / "CHANGELOG.md"

checks = []


def check(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def entries(text):
    return [e for e in build_payload(text)["entries"]]


def main() -> int:
    print("== 1. 头部解析：日期/时间/标签顺序任意、可缺省 ==")
    e = entries("## 2026-09-23 16:20 · 新增 · 标题甲")[0]
    check("日期+时间+标签+标题", (e["date"], e["time"], e["tag"], e["title"])
          == ("2026-09-23", "16:20", "新增", "标题甲"), str(e["when"]))

    e = entries("## 新增 · 2026-09-23 · 标题乙")[0]
    check("顺序颠倒也能认（标签在前）", (e["date"], e["tag"], e["title"])
          == ("2026-09-23", "新增", "标题乙"))

    e = entries("## 2026-09-23 · 标题丙")[0]
    check("只有日期（无时间无标签）", (e["date"], e["time"], e["tag"], e["title"])
          == ("2026-09-23", "", "改进", "标题丙"), f"标签回退为 {e['tag']}")

    e = entries("## 标题丁")[0]
    check("什么都没有也不崩", (e["date"], e["tag"], e["title"])
          == ("", "改进", "标题丁"))

    e = entries("## 2026-09-23 · 新增 · 标题 · 带 · 分隔符")[0]
    check("标题里的分隔符不被吃掉",
          e["title"] == "标题 · 带 · 分隔符", e["title"])

    e = entries("## 2026-09-23 | 修复 | 竖线也能当分隔符")[0]
    check("竖线分隔符", (e["tag"], e["title"]) == ("修复", "竖线也能当分隔符"))

    e = entries("## 2026-09-23 · 未收录标签 · 标题戊")[0]
    check("标签不在词表内 → 回退默认且不丢字",
          e["tag"] == "改进" and e["title"] == "未收录标签 · 标题戊", e["title"])

    print("== 2. 正文字段：同义词、中英文冒号、多行 ==")
    e = entries("""## 2026-09-23 · 新增 · 标题
- 改了什么：用户能感知的变化
- 怎么改的: 英文冒号也认
- 涉及文件：a.py、b.js
- 注意：需重启后端""")[0]
    check("字段同义词归一到 content/how/files/impact",
          e["content"] == "用户能感知的变化" and e["how"] == "英文冒号也认"
          and e["files_list"] == ["a.py", "b.js"]
          and e["impact"] == "需重启后端")

    e = entries("""## 2026-09-23 · 新增 · 标题
- 做法：第一层说明
  续行一；
  续行二。""")[0]
    check("缩进续行并入上一字段",
          e["how"] == "第一层说明\n• 续行一；\n• 续行二。", repr(e["how"]))

    e = entries("""## 2026-09-23 · 新增 · 标题
- 内容：正文
- 回归负责人：张三""")[0]
    check("认不出的键进 _extra 而不是被丢弃",
          e["_extra"] == "回归负责人：张三", repr(e["_extra"]))

    e = entries("""## 2026-09-23 · 新增 · 标题
- 内容：第一句
  第二句续行""")[0]
    check("无冒号的续行也接上", e["content"] == "第一句\n• 第二句续行", repr(e["content"]))

    print("== 3. 不该被当成条目的内容 ==")
    text = """# 更新日志

<!--
## 2026-01-01 · 新增 · 注释里的示例不算
-->

```markdown
## 2026-01-02 · 新增 · 代码围栏里的示例也不算
```

## 2026-09-23 · 新增 · 真条目"""
    got = entries(text)
    check("HTML 注释与代码围栏内的 ## 被跳过",
          len(got) == 1 and got[0]["title"] == "真条目", f"解析出 {len(got)} 条")

    print("== 4. 派生字段 ==")
    check("文件切分支持 、，逗号与空格",
          split_files("a.py、b.js，c.ts d.vue") == ["a.py", "b.js", "c.ts", "d.vue"])

    e = entries("## 2026-09-23 · 新增 · 标题\n- 影响：需重启后端")[0]
    check("影响提到重启 → 打上重启标记", e["restart"] is True)

    e = entries("## 2026-09-23 · 新增 · 标题\n- 做法：重启浏览器通道")[0]
    check("只有正文提到重启 → 不误标", e["restart"] is False)

    e = entries("## 2026-09-23 · 新增 · 标题\n- 影响：刷新页面即生效")[0]
    check("不影响重启 → 不标记", e["restart"] is False)

    id_a = entries("## 2026-09-23 10:00 · 新增 · 同一标题")[0]["id"]
    id_b = entries("## 2026-09-23 10:00 · 新增 · 同一标题")[0]["id"]
    id_c = entries("## 2026-09-23 10:00 · 新增 · 另一个标题")[0]["id"]
    check("id 内容寻址：同内容稳定、改标题即变",
          id_a == id_b and id_a != id_c, f"{id_a} / {id_c}")

    print("== 5. 排序与分组 ==")
    payload = build_payload("""## 2026-09-21 09:00 · 新增 · 旧
- 内容：a
## 2026-09-23 09:00 · 修复 · 新
- 内容：b
## 2026-09-23 09:00 · 新增 · 同日同时刻后写
- 内容：c""")
    order = [e["title"] for e in payload["entries"]]
    check("按日期倒序，同刻后写的靠前",
          order == ["同日同时刻后写", "新", "旧"], str(order))
    check("按日期分组且组内顺序不变",
          [g["date"] for g in payload["groups"]] == ["2026-09-23", "2026-09-21"]
          and [e["title"] for e in payload["groups"][0]["entries"]]
          == ["同日同时刻后写", "新"])
    check("latest = 最新一条的 id", payload["latest"] == payload["entries"][0]["id"])

    print("== 6. 空/缺失输入不抛错 ==")
    check("空文本 → 空载荷", build_payload("")["total"] == 0)
    missing = load_changelog(PROJECT_ROOT / "_no_such_changelog.md")
    check("文件不存在 → 空载荷而非异常", missing["total"] == 0 and missing["groups"] == [])

    print("== 7. 真实 CHANGELOG.md 的质量 ==")
    real = load_changelog(CHANGELOG_MD)
    check("日志文件存在且被解析出条目", real["total"] >= 29,
          f"{real['total']} 条 / {real['days']} 天")

    bad = []
    for e in real["entries"]:
        missing_fields = [k for k in ("date", "tag", "title", "content", "how")
                          if not e.get(k)]
        if missing_fields:
            bad.append(f"{e['title'][:20]} 缺 {'/'.join(missing_fields)}")
        if e["tag"] not in TAGS:
            bad.append(f"{e['title'][:20]} 标签非法 {e['tag']}")
    check("每条都有 日期/标签/标题/内容/做法，且标签合法",
          not bad, "；".join(bad[:3]))

    no_files = [e["title"] for e in real["entries"] if not e["files_list"]]
    check("每条都列了涉及文件（写日志的人容易忘的一栏）",
          not no_files, f"缺文件栏：{no_files[:3]}")

    dates = [g["date"] for g in real["groups"]]
    check("覆盖多个日期且日期降序",
          len(dates) >= 4 and dates == sorted(dates, reverse=True), str(dates))

    print("== 8. 只读接口 ==")
    try:
        from fastapi.testclient import TestClient
        import web.server as server
        client = TestClient(server.app)
        r = client.get("/api/changelog")
        body = r.json()
        ok = (r.status_code == 200 and body.get("total", 0) >= 29
              and isinstance(body.get("groups"), list)
              and body.get("latest"))
        check("GET /api/changelog 返回 200 且结构完整", ok,
              f"{r.status_code} / {body.get('total')} 条")

        r2 = client.get("/api/changelog", params={"tag": "修复"})
        b2 = r2.json()
        check("tag 过滤生效", r2.status_code == 200 and b2["total"] > 0
              and all(e["tag"] == "修复" for e in b2["entries"]),
              f"{b2.get('total')} 条修复")

        r3 = client.get("/api/changelog", params={"limit": 3})
        check("limit 生效", len(r3.json()["entries"]) == 3)
    except Exception as exc:  # 环境缺 fastapi 时跳过，不误报
        check("GET /api/changelog 接口可用", False, str(exc))

    print()
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"FAILED {len(failed)}/{len(checks)}:")
        for name, _, detail in failed:
            print(f"  - {name}" + (f" — {detail}" if detail else ""))
        return 1
    print(f"ALL {len(checks)} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
