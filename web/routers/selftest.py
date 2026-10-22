"""自测模块路由：三条链路（测试用例 / 需求文档 / Figma 设计稿）共用一个入口。

为什么单独一条路由而不是并进 `/api/tasks/run`
----------------------------------------------
任务路由的 ctx 是「一段文本 → 代码文件」，返回结构与产出物都是按 files[] 设计的；
自测返回的是**逐条判定表**（verdicts[] + 环境证据）。硬塞进同一个端点会让两边
都要写一堆 if，而且会污染 codetest 的既有契约。

安全边界：自测**只读**——不开分支、不改代码、不写目标仓库。它唯一落盘的是
`artifacts/*.json`（报告）与 `screenshots/`（证据截图）。
"""
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from web.state import (_acquire_run, _astore, _astore_att, _body_json,
                       _busy_detail, _load_settings, _pipeline_config,
                       _release_run, _run_progress, _run_snapshot, _skills_text)
from scripts import usage                                        # noqa: E402
from scripts.ai_model import AIModel                             # noqa: E402
from scripts.artifact import ArtifactStore                       # noqa: E402
from scripts.chat_attachments import (AttachmentError, classify,  # noqa: E402
                                      merge_doc_text)
from scripts.selftest import (LANE_LABELS, SelfTestError,        # noqa: E402
                              fetch_ones_cases, normalize_lanes)
from scripts.selftest import run as run_selftest                 # noqa: E402

router = APIRouter()

# 一次自测最多带几份需求文档。与需求开发同口径（REQ_ATT_MAX=6），但常量各存一份：
# 两者是不同模块的上限，将来放开其中一个不该被另一个绑住。
SELFTEST_ATT_MAX = 6
# 只有「文本 / PDF」算需求文档；图片附件在自测里没有消费方（采证用的截图是
# 运行时自己拍的），传进来只会让人以为模型读过。
_DOC_KINDS = ("text", "doc")


def _doc_attachments(ids: List[str], store) -> List[Dict]:
    """按 id 取附件元数据，只留可当文档读的（文本 / PDF）。认不出的 id 静默丢弃。

    静默丢弃而不是报错：附件 id 存在浏览器 localStorage 里，删过 `attachments/`
    目录或用另一台机器打开页面都会出现过期 id，为这个拦住整次自测不值。
    """
    wanted = [str(i).strip() for i in (ids or []) if str(i or "").strip()]
    if not wanted:
        return []
    return [a for a in store.many(wanted[:SELFTEST_ATT_MAX])
            if str(a.get("kind") or "") in _DOC_KINDS]


@router.post("/api/selftest/attachments")
async def selftest_upload_attachment(req: Request):
    """自测：上传需求文档（md / txt / pdf），服务端抽成文本后返回元数据。

    与 `/api/req/attachments` 落的是同一个附件库（同一套 PDF 抽取、同一份
    白名单），但仍是独立端点：两个模块的「一次最多几份」「允许哪些类型」是各自
    的规则，共用端点意味着改自测的限制会顺手改掉需求开发的行为。
    """
    try:
        form = await req.form()
    except Exception as e:
        raise HTTPException(400, f"读取上传内容失败: {e}")
    upload = form.get("file")
    if upload is None:
        raise HTTPException(400, "没有收到文件（字段名应为 file）")
    if isinstance(upload, str):
        raise HTTPException(400, "file 字段必须是文件内容")
    try:
        data = await upload.read()
    except Exception as e:
        raise HTTPException(400, f"读取文件内容失败: {e}")
    name = str(getattr(upload, "filename", "") or "")
    ctype = str(getattr(upload, "content_type", "") or "")
    # 先判类型再落盘：图片等落下来再拒绝会留下一个谁也读不到的孤儿文件。
    if classify(ctype, name) not in _DOC_KINDS:
        raise HTTPException(400, f"「{name or '未命名文件'}」不是文档："
                                 "自测的需求文档只支持 md / txt / pdf")
    store = _astore_att()
    try:
        # PDF 解析是 CPU 活（几十页要几百毫秒到几秒），不能占住事件循环。
        info = await run_in_threadpool(store.save, name, data, ctype)
    except AttachmentError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"保存附件失败: {e}")
    return {"attachment": store.public(info), "preview": store.preview(info)}



@router.get("/api/selftest/cases")
async def selftest_cases(issue: str = Query("")):
    """预览：从 ONES 工单「测试情况」拉关联用例（不跑任何判定）。

    单独做一个 GET 是为了让人**先看见拉到了什么**再决定跑不跑：ONES 各部署的
    测试接口形状不一，静默拉到 0 条然后跑出一份「无法判断」报告是最糟的体验。
    """
    s = _load_settings()
    got = await run_in_threadpool(fetch_ones_cases, issue, s.get("ones") or {})
    items: list = got.get("items") or []
    return {
        "issue": got.get("issue", ""),
        "count": len(items),
        "note": got.get("note", ""),
        "items": [{"title": str(it.get("name") or it.get("title")
                                or it.get("case_name") or "")[:200],
                   "steps": str(it.get("steps") or it.get("step") or "")[:600],
                   "expected": str(it.get("expected") or it.get("expect")
                                   or it.get("result") or "")[:400]}
                  for it in items[:40] if isinstance(it, dict)],
    }


@router.post("/api/selftest/run")
async def selftest_run(req: Request):
    """跑一次自测：采证 → 三条链路判定 → 落成产出物。"""
    if _run_snapshot().get("running"):
        return JSONResponse({"error": _busy_detail("跑自测")}, status_code=409)
    body = await _body_json(req)
    s = _load_settings()
    lanes = normalize_lanes(body.get("lanes"))
    ctx: Dict = {
        "lanes": lanes,
        "url": str(body.get("url") or "").strip(),
        "routes": str(body.get("routes") or "").strip(),
        "issue": str(body.get("issue") or "").strip(),
        "cases_text": str(body.get("cases_text") or ""),
        "req_doc": str(body.get("req_doc") or ""),
        "figma_url": str(body.get("figma_url") or "").strip(),
        "limit": int(body.get("limit") or 12),
    }
    # 需求文档附件：与手贴正文合并成一份（手写的约束在前，附件正文在后）。
    # 附件只传引用、正文在**运行时**由服务端读：页面刷新或重新打开后手写内容
    # 与附件都还在，不会出现「前端预填文本」与「后端附件正文」两份互相漂移。
    # 顺序很重要：先合并再校验，否则「只传了 PDF、没贴正文」会被误判成全空。
    raw_ids = body.get("attachment_ids")
    if not isinstance(raw_ids, list):
        raw_ids = []
    store = _astore_att()
    atts = _doc_attachments([str(i) for i in raw_ids], store)
    if atts:
        ctx["req_doc"] = merge_doc_text(store, ctx["req_doc"], atts)
        # 落进产出物的 ctx：报告里要能看出「这次判的是哪几份文档」，
        # 否则改天回看只剩一句结论，判据无从追溯。
        ctx["attachments"] = [{"id": a.get("id"), "name": a.get("name"),
                               "size": a.get("size"), "chars": a.get("chars"),
                               "ext": a.get("ext")} for a in atts]
    if not ctx["req_doc"].strip() and not ctx["cases_text"].strip() \
            and not ctx["issue"] and not ctx["figma_url"]:
        raise HTTPException(400, "三条链路都没有输入：请填工单编号、粘贴用例、"
                                 "粘贴或上传需求文档（md / txt / pdf），或填 Figma 链接")

    ai = AIModel(dict(_pipeline_config(s)["ai"]))
    skills_text = _skills_text(s, "selftest")
    label = "自测 · " + ("/".join(LANE_LABELS[x] for x in lanes) or "-")
    cancel = _acquire_run("selftest", label=label, source=str(body.get("source") or ""))
    if cancel is None:
        return JSONResponse({"error": _busy_detail("跑自测"),
                             "run": _run_snapshot()}, status_code=409)
    try:
        def _job():
            def _progress(stage, detail=""):
                _run_progress(cancel, stage=str(stage), stage_detail=str(detail)[:200])

            with usage.task("selftest", title=label, step="selftest"):
                return run_selftest(ai, ctx, s, progress=_progress,
                                    skills_text=skills_text)

        payload = await run_in_threadpool(_job)
    except SelfTestError as e:
        raise HTTPException(400, str(e))
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(500, f"自测执行失败: {e}")
    finally:
        _release_run(cancel)

    title = str(body.get("title") or "").strip() \
        or (f"自测 {ctx['issue']}" if ctx["issue"] else
            f"自测 {'/'.join(r for r in ctx['routes'].split(',') if r.strip())[:60]}".strip())
    artifact = _astore().create("selftest", title or "自测", payload, ctx)
    return {
        "ok": not payload.get("error"),
        "artifact": ArtifactStore.summary(artifact),
        "ai_mode": payload.get("ai_mode", ""),
        "result": {
            "summary": payload.get("summary", ""),
            "counts": payload.get("counts") or {},
            "lanes": payload.get("lanes") or [],
            "verdicts": payload.get("verdicts") or [],
            "environment": payload.get("environment") or {},
            "notes": payload.get("notes") or [],
            "error": payload.get("error", ""),
        },
    }
