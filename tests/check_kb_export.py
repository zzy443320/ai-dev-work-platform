# -*- coding: utf-8 -*-
"""知识库备份/脱敏导出的回归用例（自带合成夹具，不读真实知识库）。

这块的用例价值全在**两个方向都要断言**：
  - 该挡的挡掉：内网链接、账号口令、工单记录号、绝对路径、邮箱、迭代号；
  - 该留的留下：frontmatter 的字段名（kb_version / defect_id / failed_checks 这些
    跟客户环境实体号长得一模一样）、驼峰代码标识符、公开文档链接、现象/根因正文。

第二组断言才是重点。第一版实现为了挡 `vd_customer` 加了一条 snake_case 规则，结果
一次导出把 312 处字段名一起洗掉——知识被"脱敏"脱没了。所以这里逐条钉住形状。

跑法（不依赖 cwd）：
    PYTHONUTF8=1 .venv/Scripts/python.exe tests/check_kb_export.py
"""
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))

from scripts.kb_backup import (  # noqa: E402
    KEEP_TERM_FILE, TERM_FILE, audit_masked, backup_zip, export_masked,
    seed_terms_from_credentials, write_term_template)

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


CARD = """---
defect_id: EXAMPLEid0000001
title: "【迭代106】导入未选必填字段不拦截"
category: 逻辑
kb_version: 2
ai_mode: openai
---

# 【迭代106】导入未选必填字段不拦截

## 现象

【测试环境】

https://test-intranet.customer.example.cn/application/vd11LQY/application-list/vd_customer

复现机 repo-mirror.internal.cn 上也有同样表现（不带协议头写的时候，URL 规则抓不到）。

【测试账号】

someuser/Passw0rd@2026

本地路径 D:\\客户环境\\zzportal\\src\\Import.tsx 里复现，联系人 someone@customer.example.cn

## 根因

`validateImportRows2` 只在提交时校验，取数走 `getUserItems2`，未选必填字段时
`failed_checks` 为空数组，真值判断直接放过。官方文档见
https://react.dev/reference/react/useState

## 修复

提交前用 `hasRequiredFields` 先拦一道。该向导只在 ZZPORTAL 平台开启。
"""

CARD2 = """---
defect_id: EXAMPLEid0000002
title: "列表分页越界"
category: UI
kb_version: 2
---

## 现象
第 3 页删除最后一条后停在空白页。

## 预防
分页参数改动必须补 `pageOverflowGuard` 用例。
"""

PATTERN = """---
name: 静默失败
occurrences: 4
---

## 共性
校验函数返回空数组被当成「通过」。
"""


def build_kb(root: Path) -> None:
    (root / "逻辑").mkdir(parents=True)
    (root / "UI").mkdir(parents=True)
    (root / "_patterns").mkdir(parents=True)
    (root / "逻辑" / "EXAMPLEid0000001.md").write_text(CARD, encoding="utf-8")
    (root / "UI" / "EXAMPLEid0000002.md").write_text(CARD2, encoding="utf-8")
    (root / "_patterns" / "静默失败.md").write_text(PATTERN, encoding="utf-8")
    (root / "_stats.json").write_text('{"cards": 2, "repo": "D:/客户环境/x"}',
                                      encoding="utf-8")


def main() -> int:
    kb = Path(tempfile.mkdtemp(prefix="kb-src-"))
    out = Path(tempfile.mkdtemp(prefix="kb-dst-")) / "masked"
    build_kb(kb)

    # ---- 词表：缺失时能生成模板；填了词就会被替换 ----
    tmpl = write_term_template(kb)
    check("能生成词表模板", tmpl.is_file() and "内部标识" in tmpl.read_text(encoding="utf-8"))
    kb_terms = kb / TERM_FILE
    kb_terms.write_text("# 注释\nZZPORTAL\n", encoding="utf-8")
    # 白名单：validateImportRows2 与工单号同形状（≥12 位、大小写+数字混合），形状规则
    # 分不开，只能点名捞回；getUserItems2 故意不写进去，用来验证默认是「宁可多洗」。
    (kb / KEEP_TERM_FILE).write_text("validateImportRows2\n", encoding="utf-8")

    res = export_masked(kb, out)

    # ---- 1. 结构与数量：知识不能整块丢掉 ----
    check("卡片数不变", res["files"] == 3, str(res["files"]))
    check("类目目录保留", all((out / c).is_dir() for c in ("逻辑", "UI", "_patterns")),
          str([p.name for p in out.iterdir()]))
    check("写了导出说明", (out / "EXPORT-NOTE.md").is_file())
    check("不导出 json（里面有真实路径）", not list(out.rglob("*.json")),
          str([p.name for p in out.rglob("*.json")]))
    check("词表文件本身不导出", not (out / TERM_FILE).exists())

    card = (out / "逻辑" / "EXAMPLEid0000001.md").read_text(encoding="utf-8")

    # ---- 2. 该挡的：逐条断言 ----
    check("内网链接被挡", "test-intranet.customer.example.cn" not in card and
          "<已脱敏链接>" in card)
    check("实体短号随链接一起消失", "vd_customer" not in card and "vd11LQY" not in card)
    check("账号口令被挡", "Passw0rd@2026" not in card and "someuser" not in card)
    check("凭据位留下占位符", "<已脱敏账号凭据>" in card)
    check("【测试账号】标签仍在（知道这里原本是什么）", "测试账号" in card)
    check("工单记录号被挡", "EXAMPLEid0000001" not in
          card.split("---", 2)[2] and "<已脱敏ID>" in card)
    check("绝对路径被挡", "D:\\" not in card and "<已脱敏路径>" in card)
    check("邮箱被挡", "someone@customer.example.cn" not in card)
    check("迭代号留形状去数值", "【迭代106】" not in card and "【迭代N】" in card)
    check("词表命中：平台名被换成占位符",
          "ZZPORTAL" not in card and "该向导只在 <内部标识> 平台开启" in card)

    # ---- 3. 该留的：这一组是防「脱敏把知识洗没了」的回归 ----
    head = card.split("---", 2)[1]
    for key in ("defect_id", "kb_version", "ai_mode", "title", "category"):
        check(f"frontmatter 键完好：{key}", key + ":" in head, head.replace("\n", " ")[:90])
    check("字段名 failed_checks 没被当实体号洗掉", "failed_checks" in card)
    check("白名单捞回：validateImportRows2 原样保留", "validateImportRows2" in card)
    check("不在白名单的同形状串按 ID 洗掉（默认宁可多洗）",
          "getUserItems2" not in card)
    check("普通驼峰标识不受影响", "hasRequiredFields" in card)
    check("公开文档链接保留", "https://react.dev/reference/react/useState" in card)
    check("裸域名也被挡（不带协议头的那种）",
          "repo-mirror.internal.cn" not in card and "复现机 <已脱敏链接> 上" in card)
    check("现象/根因正文仍在", "## 根因" in card and "## 现象" in card)
    check("技术结论整句保留",
          "只在提交时校验" in card and "真值判断直接放过" in card)
    check("模式卡也导出", (out / "_patterns" / "静默失败.md").is_file())

    # ---- 4. 备份：原始目录整份可还原 ----
    dest = Path(tempfile.mkdtemp(prefix="kb-bak-"))
    zp = backup_zip(kb, dest)
    check("生成了 zip 备份", zp.is_file() and zp.stat().st_size > 0, zp.name)
    with zipfile.ZipFile(zp) as zf:
        names = set(zf.namelist())
        check("备份含全部卡片与 json", "逻辑/EXAMPLEid0000001.md" in names
              and "_stats.json" in names, f"{len(names)} 项")
        bad = zf.testzip()
        check("zip 完整性校验通过", bad is None, str(bad))
    again = backup_zip(kb, dest)
    check("重复备份不会覆盖旧档", again != zp and len(list(dest.glob('*.zip'))) == 2)

    # ---- 4b. 导出后自检：规则是启发式的，漏没漏必须比对原件才知道 ----
    rep = audit_masked(kb, out)
    check("自检从原件抽到了敏感原子", rep["checked"] >= 8, f"checked={rep['checked']}")
    check("自检：当前副本零残留", not rep["leaked"],
          str([r["token"][:6] + "…" for r in rep["leaked"][:3]]))
    check("白名单串不计入残留", rep["kept"] == 1, str(rep["kept"]))

    # 自检要防的退化是「规则哪天不生效了，副本里还是原文」。构造一份这样的副本：
    # 直接把**原始卡片**放进导出目录，自检必须逐项点出来。
    # （注意契约方向：自检比对的是「原件里的敏感原子是否存活」，所以往副本里塞一个
    # 原件没有的串，它不报——这不是漏检，是不在比对范围内。）
    leaky = Path(tempfile.mkdtemp()) / "leaky"
    shutil.copytree(out, leaky)
    (leaky / "逻辑" / "EXAMPLEid0000001.md").write_text(CARD, encoding="utf-8")
    rep2 = audit_masked(kb, leaky)
    toks = {r["token"] for r in rep2["leaked"]}
    check("规则失效时自检报出多项残留", len(rep2["leaked"]) >= 4,
          f"{len(rep2['leaked'])} 项")
    check("内网主机名被点出", "test-intranet.customer.example.cn" in toks, str(sorted(toks))[:90])
    check("工单记录号被点出", "EXAMPLEid0000001" in toks)
    check("账号口令被点出", "Passw0rd@2026" in toks)
    check("报告定位到具体文件行",
          any("EXAMPLEid0000001.md" in w for r in rep2["leaked"] for w in r["where"]),
          str(rep2["leaked"][:1])[:160])
    from scripts.kb_backup import redact  # noqa: E402
    shape = redact("test-intranet.customer.example.cn")
    check("报告里敏感值只以打码形状出现",
          "test-intranet" not in shape and "*" in shape, shape)

    # ---- 4c. 账号名自动入表：裸出现的账号名，形状规则挡不住 ----
    kb2 = Path(tempfile.mkdtemp()) / "kb2"
    (kb2 / "逻辑").mkdir(parents=True)
    card_path = kb2 / "逻辑" / "c1.md"
    card_path.write_text(
        "---\ndefect_id: Xyz1234567890Abcd\n---\n"
        "【测试账号】\n\nzhangsan/Passw0rd@9\n\n"
        "复现人是 zhangsan，找他确认过。\n", encoding="utf-8")
    out2 = Path(tempfile.mkdtemp()) / "out2"
    seeded = seed_terms_from_credentials(kb2)
    check("从凭据行抽出账号名", seeded == ["zhangsan"], str(seeded))
    check("重跑不重复入表（幂等）", seed_terms_from_credentials(kb2) == [])
    raw_card = card_path.read_text(encoding="utf-8")
    export_masked(kb2, out2)
    card2 = (out2 / "逻辑" / "c1.md").read_text(encoding="utf-8")
    check("不补词表时裸账号名确实在原文里", "zhangsan" in raw_card)
    check("补了词表后，凭据行与裸出现的账号名都消失", "zhangsan" not in card2,
          card2.strip()[-60:].replace("\n", " "))
    check("正文叙述仍完整保留", "复现人是" in card2 and "找他确认过" in card2)
    check("自动入表会写注释说明来源",
          "自动补入" in (kb2 / TERM_FILE).read_text(encoding="utf-8"))

    # ---- 5. 防呆 ----
    try:
        export_masked(kb, kb)
        check("导出目录=知识库本身时拒绝", False, "竟然放行了")
    except ValueError:
        check("导出目录=知识库本身时拒绝", True)
    try:
        export_masked(kb / "nope", out)
        check("源目录不存在时报错", False)
    except FileNotFoundError:
        check("源目录不存在时报错", True)

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
