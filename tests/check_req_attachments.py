# -*- coding: utf-8 -*-
"""需求文档附件（md / txt / pdf）离线用例。

为什么要单独一份：需求开发的主输入从「一段粘贴的文字」扩成「文字 + 文件」之后，
最容易坏的是三件事 —— ① PDF 抽不出文字却被当成空需求送进流水线；② 附件正文
与手写需求拼接时丢掉来源标注（模型分不清哪句是用户的原话）；③ 前端与后端对
「一次能传几份」各说一套。这里全部用假文件离线验，不碰网络与真实仓库。

用法：python tests/check_req_attachments.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.chat_attachments import (MAX_DOC_CHARS, AttachmentError,  # noqa: E402
                                      ChatAttachmentStore, classify, text_block)

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + name + (f"  <- {detail}" if not ok and detail else ""))
    if not ok:
        FAILED.append(name)


def make_pdf(text: str) -> bytes:
    """手搓一份最小可解析 PDF（Helvetica + 一行 ASCII 文本）。

    不用 reportlab：多一个构建期依赖只为测试不值；也不用 PdfWriter 造空白页 ——
    那样测不到「有文字层时能抽出来」这条主路径。
    """
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def make_empty_pdf() -> bytes:
    """真正的空白 PDF（无文本层，等价于扫描件）：用 pypdf 自己造，保证格式合法。"""
    from pypdf import PdfWriter
    import io
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="req-att-"))
    store = ChatAttachmentStore(str(tmp / "attachments"))

    # ---------------------------------------------------------------- 1. 类型识别
    print("\n[1] 类型识别：md / txt 是文本，pdf 是文档，其它一律拒")
    check("md → text", classify("text/markdown", "a.md") == "text")
    check("txt（浏览器不给 MIME）按后缀认", classify("", "需求.txt") == "text")
    check("application/octet-stream 的 .md 也认", classify("application/octet-stream", "a.md") == "text")
    check("pdf → doc", classify("application/pdf", "a.pdf") == "doc")
    check("pdf（MIME 为 octet-stream）按后缀认", classify("application/octet-stream", "a.PDF") == "doc")
    check("exe 不被接受", classify("application/x-msdownload", "a.exe") is None)

    # ---------------------------------------------------------------- 2. 文本附件
    print("\n[2] md / txt：落盘后能原样读回")
    md = "# 订单需求\n\n列表页新增「按交付日期筛选」。\n"
    info_md = store.save("需求说明.md", md.encode("utf-8"), "text/markdown")
    check("md 落盘为 text", info_md["kind"] == "text", str(info_md.get("kind")))
    check("md 正文可读", "按交付日期筛选" in (store.text_payload(info_md) or ""))
    check("md 用原名（后缀对齐）", info_md["name"] == "需求说明.md", info_md["name"])

    gbk = "这是一份 GBK 编码的需求.txt".encode("gbk")
    info_gbk = store.save("老系统需求.txt", gbk, "text/plain")
    check("GBK 的 txt 不报错且能读", "GBK 编码" in (store.text_payload(info_gbk) or ""))

    # ---------------------------------------------------------------- 3. PDF
    print("\n[3] PDF：上传时抽正文、缓存起来、预览不带标题行")
    info_pdf = store.save("prd.pdf", make_pdf("Requirement: add date filter"), "application/pdf")
    check("pdf 落盘为 doc", info_pdf["kind"] == "doc", str(info_pdf.get("kind")))
    check("pdf 记录了抽取字符数", int(info_pdf.get("chars") or 0) > 0, str(info_pdf.get("chars")))
    payload = store.text_payload(info_pdf) or ""
    check("抽出的正文含原文", "add date filter" in payload, payload[:120])
    check("正文带来源标注", payload.startswith("### 附件「prd.pdf」"), payload[:60])
    preview = store.preview(info_pdf)
    check("预览从正文开始（不含来源标题）", preview and not preview.startswith("###"), preview[:60])
    check("抽取结果已缓存到磁盘", (tmp / "attachments" / info_pdf["text_file"]).is_file())

    # ---------------------------------------------------------------- 4. 该拒的要拒
    print("\n[4] 抽不出文字 / 假 PDF / 不支持的格式：明确拒绝，而不是静默当空需求")
    try:
        store.save("扫描件.pdf", make_empty_pdf(), "application/pdf")
        check("无文字层的 PDF 被拒绝", False, "居然通过了")
    except AttachmentError as e:
        check("无文字层的 PDF 被拒绝", "没有可提取的文字" in str(e), str(e)[:120])
    try:
        store.save("假货.pdf", b"this is not a pdf at all", "application/pdf")
        check("伪造的 PDF 被拒绝", False, "居然通过了")
    except AttachmentError as e:
        check("伪造的 PDF 被拒绝", "不是可解析的 PDF" in str(e), str(e)[:120])
    try:
        store.save("run.exe", b"MZ\x00\x00", "application/x-msdownload")
        check("exe 被拒绝", False, "居然通过了")
    except AttachmentError as e:
        check("exe 被拒绝", "不支持的文件类型" in str(e), str(e)[:120])

    # ---------------------------------------------------------------- 5. 合并成需求
    print("\n[5] 与手写需求合并：手写在最前、附件在后、各带来源")
    from web.routers.tasks import (REQ_ATT_MAX, REQ_DOC_MAX_CHARS,
                                   _req_doc_with_attachments)
    merged = _req_doc_with_attachments("只改接口，别动页面结构。", [info_md, info_pdf], store)
    check("手写需求排在最前", merged.startswith("只改接口"), merged[:40])
    check("md 附件正文在内", "按交付日期筛选" in merged)
    check("pdf 附件正文在内", "add date filter" in merged)
    check("两份附件各自带来源", merged.count("### 附件「") == 2, str(merged.count("### 附件「")))
    only_att = _req_doc_with_attachments("", [info_md], store)
    check("只传附件也能组成需求", only_att.startswith("### 附件「"), only_att[:40])
    check("空需求 + 空附件 = 空串", _req_doc_with_attachments("", [], store) == "")

    long_doc = "开头标记\n" + ("填充" * (REQ_DOC_MAX_CHARS // 2)) + "\n结尾标记"
    clipped = _req_doc_with_attachments(long_doc, [], store)
    check("超长需求被截断并说明", clipped.startswith("…（需求正文超过"), clipped[:40])
    check("截断保留结尾（验收标准常在文末）", clipped.endswith("结尾标记"), clipped[-20:])

    check("一次最多 6 份（与前端一致）", REQ_ATT_MAX == 6, str(REQ_ATT_MAX))

    # ---------------------------------------------------------------- 6. 截断口径
    print("\n[6] 长文本附件：留头尾并如实标注省略量")
    big = "头" * (MAX_DOC_CHARS + 500)
    info_big = store.save("长文档.txt", big.encode("utf-8"), "text/plain")
    big_payload = store.text_payload(info_big) or ""
    check("超长附件被裁剪", "中间省略" in big_payload, big_payload[20000:20200])

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: " + "; ".join(FAILED))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
