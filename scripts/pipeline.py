"""End-to-end orchestrator.

ONES → locate（种子线索）→ **agentic 修复循环**（读代码 → 出补丁 → 沙箱真跑验收 →
读报错 → 自我修复，见 scripts/fix_agent.py）→ SEARCH/REPLACE patch → 闸门（沙箱真实
结果优先）→ **proposal awaiting approval**。This module never writes to the target
repository. Writing happens only through `approve()`, which is what the Web UI's
采纳 button calls.
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import usage
from .ai_model import AIModel
from .analyzer import DefectAnalyzer
from .artifact import ArtifactStore
from .fix_agent import FixAgent
from .fixer import CodeFixer
from .gate import resolve_commands, run_gate
from .kb import KnowledgeBase
from .mock_data import SAMPLE_DEFECTS
from .ones_fetcher import OnesClient
from .proposal import (
    STATUS_APPLIED,
    STATUS_APPLY_FAILED,
    STATUS_REJECTED,
    ProposalStore,
)
from .repo_paths import UnsafePath, resolve_in_repo
from .verifier import ScreenshotVerifier


def _emit(emit, evt: Dict) -> None:
    """Best-effort event sink: 回调异常绝不能炸掉流水线本身。"""
    if emit is None:
        return
    try:
        emit(evt)
    except Exception:
        pass


def _ai_on_delta(emit, defect_id: str):
    """把 AI 流式增量（reasoning/content）转成 ai_delta 事件。"""
    if emit is None:
        return None

    def _cb(kind: str, text: str) -> None:
        _emit(emit, {"type": "ai_delta", "defect": str(defect_id),
                     "kind": kind, "text": text})

    return _cb


class AIDefectFixerPipeline:
    def __init__(self, config: Dict):
        self.config = config
        ones_cfg = config["ones"]
        self.ones = OnesClient(
            ones_cfg.get("base_url", ""),
            token=ones_cfg.get("token", ""),
            project_uuid=ones_cfg.get("project_uuid", ""),
            team_uuid=ones_cfg.get("team_uuid", ""),
            email=ones_cfg.get("email", ""),
            password=ones_cfg.get("password", ""),
        )

        ai_cfg = dict(config.get("ai", {}) or {})
        self.ai = AIModel(ai_cfg)

        repo_cfg = config.get("repo", {})
        self.repo_path = repo_cfg.get("path") or ""
        self.branch = repo_cfg.get("branch", "") or ""
        self.gate_cfg = config.get("gate", {}) or {}
        # agentic 修复循环的预算与沙箱策略（config `agent:` 段，全部有安全默认值）
        self.agent_cfg = config.get("agent", {}) or {}
        self.agent_enabled = str(self.agent_cfg.get("enabled", True)).strip().lower() \
            not in ("false", "0", "no", "off")

        self.fixer: Optional[CodeFixer] = None
        if self.repo_path and Path(self.repo_path).is_dir():
            self.analyzer: Optional[DefectAnalyzer] = DefectAnalyzer(
                self.repo_path, self.ai,
                # 历史已采纳提案目录：定位时给"人工确认过的修改点"先例加权
                precedent_dir=config.get("proposals", {}).get(
                    "output_dir", "./proposals"),
            )
            self.fixer = CodeFixer(self.repo_path, self.branch, self.gate_cfg)
        else:
            self.analyzer = None

        pw_cfg = config.get("playwright", {})
        self.verifier = ScreenshotVerifier(
            base_url=pw_cfg.get("base_url", "http://localhost:3000"),
            headless=pw_cfg.get("headless", True),
            screenshot_dir=pw_cfg.get("screenshot_dir", "./screenshots"),
            ai=self.ai,  # 复现步骤 → Playwright 动作编排用
            # 页面登录凭据（账号密码可选；storage_state 用于跨工单延续登录态）
            auth=pw_cfg.get("auth") or {},
        )

        kb_cfg = config.get("knowledge_base", {})
        self.kb = KnowledgeBase(kb_cfg.get("output_dir", "./knowledge_base"))
        self.proposals = ProposalStore(
            config.get("proposals", {}).get("output_dir", "./proposals")
        )
        # 复现用例要能进仓库，但**不能走提案的补丁通道**（补丁引擎不支持新建文件）。
        # 产出物这条路本来就支持新建 + 备份 + 撤销，所以复现用例挂成产出物等人采纳。
        self.artifacts = ArtifactStore(
            config.get("artifacts", {}).get("output_dir", "./artifacts")
        )

        # 构造期**不做 I/O**：仓库前置检查要起 git 子进程，而 web 层是「每个请求一个
        # 新 pipeline」（_fresh_pipeline），放在这里等于每次请求都付一次 git 开销，
        # 而且是在事件循环上付的（界面整体卡顿）。改成首次访问时再算并缓存，
        # 调用点（run/describe/run.py）语义不变。
        self._preflight: Optional[Dict] = None

    @property
    def preflight(self) -> Dict:
        if self._preflight is None:
            self._preflight = self.fixer.preflight() if self.fixer else {
                "problems": ["未配置可用仓库路径，只能产出分析结论，不会生成补丁"],
                "repo": self.repo_path, "is_git": False, "current_branch": "",
                "expected_branch": self.branch, "branch_ok": False, "dirty": False,
            }
        return self._preflight

    def describe(self) -> Dict:
        return {
            "ai_mode": self.ai.mode,
            "ai_model": self.ai.model,
            "repo": self.repo_path,
            "branch": self.branch,
            "preflight": self.preflight,
            "gate_commands": resolve_commands(self.repo_path, self.gate_cfg)
            if self.repo_path else [],
            "verifier_base_url": self.verifier.base_url,
            "agent": self.agent_describe(),
        }

    def agent_describe(self) -> Dict:
        """把 agentic 循环的口径摊给界面：开没开、预算多少、沙箱怎么建、能跑到什么信号。"""
        enabled = bool(self.agent_enabled and self.analyzer is not None
                       and str(self.ai.mode) != "mock")
        probe = (FixAgent(self.repo_path, self.ai, agent_cfg=self.agent_cfg,
                          gate_cfg=self.gate_cfg) if self.repo_path else None)
        return {
            "enabled": enabled,
            "reason": ("" if enabled else
                       ("Mock 模型不支持 agentic 循环" if str(self.ai.mode) == "mock"
                        else ("未配置仓库路径" if not self.repo_path
                              else "配置里已关闭 agent.enabled"))),
            "max_rounds": probe.max_rounds if probe else 0,
            "deadline_seconds": probe.deadline_seconds if probe else 0,
            "max_stall": probe.max_stall if probe else 0,
            "sandbox_mode": probe.sandbox_cfg.get("mode") if probe else "auto",
            "verify_commands": [c["name"] for c in
                                resolve_commands(self.repo_path, self.gate_cfg)]
            if self.repo_path else [],
        }

    # ------------------------------------------------------------- run all
    def run(
        self,
        defect_id: Optional[str] = None,
        mock: bool = False,
        dry_run: bool = False,
        limit: int = 5,
        skip_verify: bool = False,
        mine_only: bool = True,
        defects: Optional[List[Dict]] = None,
        emit=None,
    ) -> List[Dict]:
        _emit(emit, {"type": "stage", "stage": "fetch", "status": "start",
                     "detail": "正在拉取 ONES 工单…"})
        if defects:
            # 显式传入的工单（测试注入/手工指定）优先于任何拉取路径
            defects, source = list(defects), "injected"
        else:
            defects, source = self._fetch(defect_id, mock, limit, mine_only)
        if source == "error":
            _emit(emit, {"type": "stage", "stage": "fetch", "status": "fail",
                         "detail": "ONES 拉取失败（详见结果与日志），不回退假数据"})
        else:
            _emit(emit, {"type": "stage", "stage": "fetch", "status": "done",
                         "detail": f"拉取到 {len(defects)} 条工单（来源 {source}）"})

        results: List[Dict] = []
        total = len(defects)
        for i, d in enumerate(defects):
            did = str(d.get("id") or "local")
            _emit(emit, {"type": "defect", "index": i + 1, "total": total,
                         "id": did, "title": str(d.get("title", ""))[:80]})
            print(f"\n=== Processing {d.get('id')}: {str(d.get('title', ''))[:60]} ===")
            # 用量归属：这一条工单的所有模型调用（含二次定位、返工）都记到它名下
            with usage.task("defect", run_id=did,
                            title=str(d.get("title", ""))[:80]):
                try:
                    r = self._process_one(d, skip_verify=skip_verify, emit=emit)
                except Exception as e:
                    print(f"  [error] {d.get('id')} 处理失败: {e}")
                    _emit(emit, {"type": "stage", "stage": "proposal", "status": "fail",
                                 "defect": did, "detail": f"处理失败: {e}"})
                    r = self._failure_result(d, str(e))
            r["source"] = source
            results.append(r)
        if dry_run:
            print("[note] dry_run 现在只是少截一张图；流水线任何模式下都不会写目标仓库。")
        return results

    # --------------------------------------------------------------- probe
    def probe(
        self,
        defect_id: Optional[str] = None,
        mock: bool = False,
        limit: int = 5,
        locate: bool = True,
        days: int = 0,
        mine_only: bool = True,
        emit=None,
    ) -> Dict:
        """Trial run: fetch defects (and optionally AI-locate them) WITHOUT
        creating proposals, patches, gate runs or any writes. Used to verify
        ONES connectivity and the locate stage independently of the approval
        flow. Fetch errors are surfaced instead of silently falling back to
        mock samples."""
        fetch_error = ""
        notes: List[str] = []
        hints: List[str] = []
        _emit(emit, {"type": "stage", "stage": "fetch", "status": "start",
                     "detail": "正在拉取 ONES 工单…"})
        if mock:
            defects = [
                d for d in SAMPLE_DEFECTS
                if not defect_id or d["id"] == defect_id
            ][:limit]
            source = "mock"
        else:
            source = "ones"
            # 先校验登录态（配置了账密且 token 失效时会自动重登）
            try:
                me = self.ones.me()
                who = (me or {}).get("name") or (me or {}).get("email") or ""
                if who:
                    notes.append(f"ONES 登录态有效：{who}")
            except Exception as e:
                defects, source, fetch_error = [], "error", str(e)
            if not fetch_error:
                try:
                    if defect_id:
                        # 详情接口在此部署不可用（task(key) 返回 AccessDenied），
                        # 统一走列表 + 编号/UUID 匹配。
                        defects = self.ones.fetch_defects(
                            limit=10, mine_only=False, defect_id=str(defect_id))
                        if not defects:
                            fetch_error = f"列表里没有找到工单 {defect_id}"
                    else:
                        defects = self.ones.fetch_defects(
                            limit=limit, days=days, mine_only=mine_only)
                except Exception as e:
                    defects, source = [], "error"
                    fetch_error = str(e)

            lf = getattr(self.ones, "last_filter", {}) or {}
            if lf:
                parts = [f"团队共 {lf.get('total', 0)} 条"]
                if lf.get("dropped_project"):
                    parts.append(f"非目标项目 {lf['dropped_project']} 条")
                if lf.get("mine_only"):
                    parts.append(f"非我负责 {lf.get('dropped', 0)} 条")
                if lf.get("dropped_time"):
                    parts.append(f"超出时间范围 {lf['dropped_time']} 条")
                notes.append("拉取明细：" + "、".join(parts)
                             + f"，最终返回 {len(defects)} 条")
            if lf.get("mine_only") and lf.get("my_uuid"):
                notes.append(
                    f"已按「负责人是我」过滤（我的用户 UUID：{lf['my_uuid']}）")

            if not defects and not fetch_error:
                hints = [
                    "当前拉取条件："
                    + ("负责人是我、" if mine_only else "全部负责人、")
                    + ("不限时间" if not days else f"最近 {days} 天内创建")
                    + f"、projectUUID={self.ones.project_uuid or '(空)'}",
                    "列表为空通常是因为：① 当前项目里你名下没有工单"
                    "（可以关掉「只看我负责的」开关看全项目工单，或检查 Project "
                    "UUID 是否填对）；② 工单在别的项目里（Project UUID 应为"
                    "地址栏 /project/XXX 里那一段）",
                    "如果你填了工单编号却搜不到：确认填的是工单详情页 URL 或"
                    "列表里的数字编号（如 100003）或工单 UUID",
                ]
                notes.append("ONES 接口调用成功，但按当前条件没有匹配到任何工单")

        if fetch_error:
            _emit(emit, {"type": "stage", "stage": "fetch", "status": "fail",
                         "detail": f"ONES 拉取失败：{fetch_error[:160]}"})
        else:
            _emit(emit, {"type": "stage", "stage": "fetch", "status": "done",
                         "detail": f"拉取到 {len(defects)} 条工单（来源 {source}）"})

        results: List[Dict] = []
        total = len(defects)
        for i, d in enumerate(defects):
            item: Dict = {"defect": d}
            did = str(d.get("id") or "local")
            _emit(emit, {"type": "defect", "index": i + 1, "total": total,
                         "id": did, "title": str(d.get("title", ""))[:80]})
            if locate:
                if self.analyzer is None:
                    item["analysis_error"] = "未配置仓库路径，无法做代码定位"
                else:
                    _emit(emit, {"type": "stage", "stage": "locate", "status": "start",
                                 "defect": did, "detail": "关键词抽取 + git grep 扫描…"})
                    try:
                        # 试运行同样会产生真实的模型调用，用量照记（kind=probe 以便区分）
                        with usage.task("probe", run_id=did,
                                        title=str(d.get("title", ""))[:80]):
                            a = self.analyzer.analyze(d, on_delta=_ai_on_delta(emit, did))
                        item["analysis"] = {
                            k: a.get(k) for k in (
                                "category", "root_cause", "explanation",
                                "suspect_files", "keywords", "ai_mode", "ai_error",
                                "locate_empty",
                            )
                        }
                        if a.get("ai_error"):
                            _emit(emit, {"type": "stage", "stage": "locate",
                                         "status": "warn", "defect": did,
                                         "detail": f"AI 分析异常：{a['ai_error'][:160]}"})
                        elif a.get("locate_empty"):
                            _emit(emit, {"type": "stage", "stage": "locate",
                                         "status": "warn", "defect": did,
                                         "detail": "未定位到嫌疑文件（护栏触发）"})
                        else:
                            _emit(emit, {"type": "stage", "stage": "locate",
                                         "status": "done", "defect": did,
                                         "detail": f"定位到 {len(a.get('suspect_files') or [])} 个嫌疑文件"})
                    except Exception as e:
                        item["analysis_error"] = str(e)
                        _emit(emit, {"type": "stage", "stage": "locate",
                                     "status": "fail", "defect": did,
                                     "detail": f"定位失败：{e}"})
            results.append(item)
        return {
            "count": len(results),
            "source": source,
            "fetch_error": fetch_error,
            "notes": notes,
            "hints": hints,
            "results": results,
        }

    def _fetch(self, defect_id, mock, limit, mine_only: bool = True):
        # mock 路径仅保留兼容：示例工单已移除，空列表会走到上层的「未拉取到工单」分支。
        # 关键点：拉取失败时**绝不**回退到假数据，否则会写出无法与真实工单区分的提案。
        if mock:
            defects = [
                d for d in SAMPLE_DEFECTS
                if not defect_id or d["id"] == defect_id
            ]
            return defects[:limit], "mock"
        try:
            if defect_id:
                detail = self.ones.fetch_defect_detail(defect_id)
                return [detail], "ones"
            return self.ones.fetch_defects(limit=limit, days=0,
                                           mine_only=mine_only), "ones"
        except Exception as e:
            print(f"[ONES] !! fetch failed: {e} — 不回退到示例数据，本次返回 0 条。")
            return [], "error"

    # --------------------------------------------------------- single defect
    def _process_one(self, defect: Dict, skip_verify: bool = False,
                     emit=None) -> Dict:
        did = str(defect.get("id") or "local")
        on_delta = _ai_on_delta(emit, did)

        if self.analyzer is None:
            analysis = self._analysis_without_repo(defect)
            patch = _empty_patch("未配置仓库路径，未生成补丁")
            gate = {"level": "none", "ok": True, "checks": [],
                    "notes": ["无仓库，跳过验收"], "failed_checks": []}
            verify = {"status": "skipped", "reason": "未配置仓库路径"}
            agent: Dict = {"enabled": False, "conclusion": "agent_disabled",
                           "needs_human": True, "notes": ["未配置仓库路径，未进入修复循环"]}
        else:
            _emit(emit, {"type": "stage", "stage": "locate", "status": "start",
                         "defect": did, "detail": "关键词抽取 + git grep 扫描…"})
            analysis = self.analyzer.analyze(defect, on_delta=on_delta)
            print(f"  [analyze] category={analysis['category']} mode={analysis['ai_mode']} "
                  f"blocks={analysis['block_count']}")
            if analysis.get("ai_error"):
                _emit(emit, {"type": "stage", "stage": "locate", "status": "warn",
                             "defect": did,
                             "detail": f"AI 分析异常：{analysis['ai_error'][:160]}"})
            elif analysis.get("locate_empty"):
                _emit(emit, {"type": "stage", "stage": "locate", "status": "warn",
                             "defect": did,
                             "detail": "未定位到嫌疑文件（护栏触发，不让模型盲猜）"})
            else:
                files = "、".join(analysis.get("suspect_files") or []) or "-"
                _emit(emit, {"type": "stage", "stage": "locate", "status": "done",
                             "defect": did,
                             "detail": f"定位到 {len(analysis.get('suspect_files') or [])} 个嫌疑文件：{files}"})

            # 修复前的页面现场**必须在动补丁之前抓**：它既进提案给人看，也是修复循环
            # 唯一能拿到的运行时证据（循环本身只跑静态命令，不起 dev server）。
            verify, page = self._page_evidence(did, defect, skip_verify, on_delta, emit)

            agent = self._run_agent(defect, analysis, page, did, emit, verify)
            art_id = self._repro_artifact(defect, agent)
            if art_id:
                agent.setdefault("repro", {})["artifact_id"] = art_id
                _emit(emit, {"type": "stage", "stage": "repro", "status": "done",
                             "defect": did,
                             "detail": f"复现用例已挂成产出物 {art_id}（采纳才会进仓库）"})
            patch_text = agent.get("patch_text") or analysis.get("patch_text", "")
            patch = self.fixer.preview(patch_text) if self.fixer else _empty_patch("无 fixer")
            patch["patch_text"] = patch_text
            if patch["ok"]:
                _emit(emit, {"type": "stage", "stage": "patch", "status": "done",
                             "defect": did,
                             "detail": f"补丁构建成功：{len(patch.get('blocks') or [])} 个块 → "
                                       f"{', '.join(patch.get('files') or [])}"})
                gate = self._gate_of(agent, patch)
            else:
                reason = "；".join((patch.get("errors") or [])[:2]) or "补丁未构建成功"
                _emit(emit, {"type": "stage", "stage": "patch", "status": "warn",
                             "defect": did, "detail": f"没有可应用补丁：{reason}"})
                gate = {"level": "none", "ok": False, "checks": [],
                        "notes": ["补丁未构建成功，未执行验收"], "failed_checks": []}
        _emit(emit, {"type": "stage", "stage": "gate", "status": "done" if gate.get("ok") else "warn",
                     "defect": did,
                     "detail": f"验收闸门 level={gate.get('level')} ok={gate.get('ok')}"})

        proposal = self.proposals.create(
            defect=defect, analysis=analysis, patch=patch, gate=gate,
            verify=verify, agent=agent,
        )
        proposal["preflight"] = self.preflight
        kb_path = self.kb.record(defect, analysis, proposal)
        proposal["kb_path"] = kb_path
        self.proposals.update(proposal)

        _emit(emit, {"type": "stage", "stage": "proposal",
                     "status": "done" if proposal["status"] in ("pending", "gate_failed") else "warn",
                     "defect": did,
                     "detail": f"提案 {proposal['id']} 已生成（状态 {proposal['status']}），等待人工审批"})
        _emit(emit, {"type": "stage", "stage": "kb",
                     "status": "done" if kb_path else "warn",
                     "defect": did,
                     "detail": f"知识卡片已沉淀：{kb_path}" if kb_path else "知识卡片未沉淀"})

        print(f"  [patch] ok={patch['ok']} files={patch['files']} gate={gate['level']}")
        print(f"  [agent] conclusion={agent.get('conclusion')} "
              f"rounds={agent.get('rounds')} attempts={len(agent.get('attempts') or [])}")
        for err in (patch["errors"] or [])[:3]:
            print(f"    ! {err}")
        print(f"  [proposal] {proposal['id']} status={proposal['status']}")
        print(f"  [kb] {kb_path}")

        return {
            "defect": defect,
            "analysis": analysis,
            "patch": patch,
            "gate": gate,
            "verify": verify,
            "agent": agent,
            "proposal": proposal,
            "proposal_id": proposal["id"],
            "fix": {"status": proposal["status"], "conclusion": agent.get("conclusion")},
            "kb_path": kb_path,
        }

    # ------------------------------------------------- 页面现场（修复前证据）
    def _page_evidence(self, did: str, defect: Dict, skip_verify: bool,
                       on_delta, emit) -> Tuple[Dict, Dict]:
        """截修复前的图，并折算成两份东西：提案里的 `verify` + 修复循环用的 `page` 种子。

        截图判读（视觉信号）失败或没配模型时**如实留空**，不要让循环以为「页面没问题」——
        没看过和看过没发现，是两回事。
        """
        if skip_verify or not self.repo_path:
            return ({"status": "skipped", "reason": "按请求跳过页面验证"}, {})
        _emit(emit, {"type": "stage", "stage": "verify", "status": "start",
                     "defect": did, "detail": "打开页面、复刻操作并截图…"})
        verify = self.verifier.capture(did, "before", defect, on_delta=on_delta)
        print(f"  [verify:before] {verify['status']} route={verify.get('route')}")
        _emit(emit, {"type": "stage", "stage": "verify",
                     "status": "done" if verify.get("status") in ("ok", "error_visible") else "warn",
                     "defect": did,
                     "detail": f"页面验证：{verify.get('status')}（route={verify.get('route')}）"})
        if str(self.agent_cfg.get("page_read", True)).strip().lower() in ("false", "0", "no"):
            return verify, {}
        verdict = ""
        if verify.get("screenshot"):
            _emit(emit, {"type": "stage", "stage": "verify", "status": "start",
                         "defect": did, "detail": "让模型看这张修复前截图，判读现象是否出现…"})
            verdict = self.verifier.describe(verify, defect, on_delta=on_delta)
            if verdict:
                _emit(emit, {"type": "stage", "stage": "verify", "status": "done",
                             "defect": did, "detail": "截图判读完成：" + verdict[:120]})
        verify["ai_verdict"] = verdict
        page = {
            "route": verify.get("route") or "",
            "status": verify.get("status") or "",
            "console_errors": (verify.get("console_errors") or [])
            + (verify.get("page_errors") or []),
            "ai_verdict": verdict,
        }
        if not page["console_errors"] and page["status"] in ("unreachable", "error", "blank"):
            page["ai_verdict"] = (page["ai_verdict"]
                                  + f"\n（注意：页面验证状态为 {page['status']}，"
                                    "没有可用的运行时证据）").strip()
        return verify, page

    # ------------------------------------------------------- agentic 修复循环
    def _run_agent(self, defect: Dict, analysis: Dict, page: Dict, did: str,
                   emit, verify: Optional[Dict] = None) -> Dict:
        """跑一次修复循环。任何意外都退回到「一次成型」的结果，而不是让整条流水线红。"""
        if not self.agent_enabled or str(self.ai.mode) == "mock":
            return {"enabled": False, "conclusion": "agent_disabled",
                    "needs_human": False, "notes": [], "attempts": []}

        def _emit_agent(evt: Dict) -> None:
            _emit(emit, {**evt, "defect": did})

        # 先例：把「同类问题当时怎么改的、后来人工认没认」接回回路。
        # 不给的话，同一个模块反复出缺陷，模型每次都是从零猜一遍，被否决过的改法还会重演。
        precedents = ""
        prec_items: List[Dict] = []
        try:
            from .precedents import load_precedents, render_precedents

            limit = _as_int(self.agent_cfg.get("precedents"), 3)
            prec_items = load_precedents(str(self.proposals.base), defect, limit=limit)
            precedents = render_precedents(
                prec_items, _as_int(self.agent_cfg.get("precedents_chars"), 6000))
            if prec_items:
                _emit(emit, {"type": "stage", "stage": "repro", "status": "done",
                             "defect": did,
                             "detail": f"取到 {len(prec_items)} 条同类先例"
                                       f"（其中人工拒绝 {sum(1 for i in prec_items if i['outcome'] == 'rejected')} 条）"})
        except Exception as e:
            print(f"  [precedents] 先例检索失败（不影响本次修复）: {e}")

        _emit(emit, {"type": "stage", "stage": "agent", "status": "start",
                     "defect": did,
                     "detail": "进入 agentic 修复循环：自己查代码、改、在沙箱里真跑验收…"})
        agent = FixAgent(self.repo_path, self.ai, agent_cfg=self.agent_cfg,
                         gate_cfg=self.gate_cfg, emit=_emit_agent,
                         precedents=precedents,
                         page_after_hook=self._page_after_hook(
                             defect, did, verify or page))
        try:
            with usage.step("fix_agent"):
                result = agent.run(defect, seed={"analysis": analysis, "page": page,
                                                 "description": defect.get("description")
                                                 or defect.get("desc") or ""})
        except Exception as e:
            print(f"  [agent] 异常，退回一次成型结果: {e}")
            _emit(emit, {"type": "stage", "stage": "agent", "status": "fail",
                         "defect": did, "detail": f"修复循环异常：{e}"})
            return {"enabled": True, "conclusion": "error", "needs_human": True,
                    "notes": [f"修复循环异常：{e}"], "attempts": [], "trace": []}
        _emit(emit, {"type": "stage", "stage": "agent", "status": "done", "defect": did,
                     "detail": f"修复循环结束：{result.get('conclusion')}"
                               f"（{result.get('rounds')} 轮 / "
                               f"{len(result.get('attempts') or [])} 次补丁尝试 / "
                               f"{result.get('elapsed_seconds')}s）"})
        # 透明化：注入了哪几条先例、当时结局如何，得能在提案里看到——
        # 否则模型抄了个被否决过的改法，人却查不到它抄了什么
        result["precedents_used"] = [
            {"proposal_id": i.get("proposal_id"), "defect_id": i.get("defect_id"),
             "title": i.get("title"), "outcome": i.get("outcome"),
             "conclusion": i.get("conclusion"), "verified_via": i.get("verified_via"),
             "same_defect": bool(i.get("same_defect")), "score": i.get("score")}
            for i in prec_items]
        return result

    # ------------------------------------------------------- 复现用例 → 产出物
    def _repro_artifact(self, defect: Dict, agent: Dict) -> str:
        """把沙箱里那份复现用例挂成产出物，让人工单独决定是否收进仓库。

        两个刻意的取舍：① 走产出物而不是提案补丁——补丁引擎不支持新建文件，而
        `apply_ops` 早就支持「新建 + 回滚时删掉自己建的文件」，没必要为此动唯一写盘路径；
        ② 同一条工单重跑不会堆出十份一样的用例（按路径 + 正文去重），这是知识库
        膨胀那次的教训反过来用。
        """
        repro = agent.get("repro") or {}
        path = str(repro.get("path") or "").strip()
        content = str(repro.get("content") or "").strip()
        if repro.get("status") not in ("red", "green") or not path or not content:
            return ""
        for s in self.artifacts.list(type_filter="repro")[:200]:
            # list 已按新→旧排；只回看最近 200 份，避免一条工单为了去重读遍全量文件
            same = self.artifacts.get(s.get("id") or "") or {}
            for f in (same.get("files") or []):
                if f.get("path") == path and (f.get("content") or "").strip() == content:
                    return str(same.get("id") or "")   # 已有同内容，不重复挂
        did = str(defect.get("id") or "local")
        title = f"复现用例 · {did} · {str(defect.get('title') or '')[:40]}"
        strength = ((repro.get("harness") or {}).get("strength_label")
                    or (repro.get("harness") or {}).get("name") or "")
        payload = {
            "ai_mode": str(agent.get("conclusion") or ""),
            "summary": (f"这条缺陷的复现用例（{strength}）。它在未修复代码上跑红、"
                        "打上补丁后转绿，是「缺陷确实被修好」的判据。"
                        "采纳前请确认它能被你们现有的测试跑器收集到。"),
            "checklist": [
                f"用例路径 {path} 是仓库里的**新文件**，需要你们决定放不放进去、放哪合适",
                "跑这个用例的命令：" + str(repro.get("cmd") or ""),
                "它必须先在未修复代码上失败一次才算数；请在本分支重跑一遍确认",
            ],
            "files": [{"path": path, "action": "create",
                       "description": "缺陷复现用例（agentic 修复循环在沙箱里跑红→绿）",
                       "content": content}],
            "cases": [{"name": path, "expect": "修复前红、修复后绿"}],
        }
        created = self.artifacts.create(
            "repro", title, payload,
            ctx={"notes": f"defect={did}", "file_path": path})
        return str(created.get("id") or "")

    # -------------------------------------------- 沙箱页面复验（修复后截图判读）
    def _page_after_hook(self, defect: Dict, did: str, before: Dict):
        """交给 FixAgent 的钩子：在**打了补丁的沙箱**里起 dev server，拍一张修复后的图。

        这一步的结论只有一种用法：命令全绿而现象仍在，就不算修好。反过来，页面复验做不
        成（起不来服务、白屏、缺后端接口）时**绝不降级成「通过」**，而是原样标成
        unusable / skipped —— 看不等于没看。
        """
        mode = str(self.agent_cfg.get("sandbox_server") or "auto").strip().lower()
        if mode not in ("auto", "on"):
            return None

        def hook(sandbox, budget_left: int = 0) -> Dict:
            from .sandbox import default_server_command

            command = str(self.agent_cfg.get("sandbox_server_command") or "").strip()
            if not command:
                command, _cwd = default_server_command(Path(self.repo_path))
            if not command:
                return {"status": "skipped",
                        "reason": "没探出可用的 dev server 启动命令（package.json 里没有 "
                                  "dev/start/serve，或技术栈不认识）。"
                                  "可在配置里填 agent.sandbox_server_command"}
            ready_timeout = _as_int(self.agent_cfg.get("sandbox_server_ready_timeout"), 90)
            if int(budget_left or 0) < ready_timeout + 45:
                return {"status": "skipped",
                        "reason": f"剩余时间预算不足（{budget_left}s），不起 dev server 复验"}
            handle = sandbox.spawn_server(
                command, ready_timeout=ready_timeout,
                cwd_rel=str(self.agent_cfg.get("sandbox_server_cwd") or ""))
            if not handle.ready:
                return {"status": "unusable", "reason": handle.note or "服务未就绪",
                        "command": handle.command, "url": handle.url,
                        "log_tail": handle.log_tail[-1200:]}
            shot: Dict = {"status": "error", "command": handle.command,
                          "url": handle.url, "server_seconds": handle.seconds}
            try:
                ver = ScreenshotVerifier(
                    base_url=handle.url,
                    headless=bool(self.config.get("playwright", {}).get("headless", True)),
                    screenshot_dir=self.verifier.screenshot_dir,
                    ai=self.ai,
                    auth=self.config.get("playwright", {}).get("auth") or {})
                route = (before or {}).get("route") or "/"
                after = ver.capture(did, "sandbox-after", defect, route=route,
                                    actions=(before or {}).get("replay_actions") or None)
                shot.update({
                    "screenshot": after.get("screenshot") or "",
                    "route": after.get("route") or route,
                    "page_status": after.get("status"),
                    "console_errors": (after.get("console_errors") or [])
                    + (after.get("page_errors") or []),
                })
                if after.get("status") in ("unreachable", "error", "blank"):
                    # 白屏/连不上不是「没有报错所以修好了」
                    shot["status"] = "unusable"
                    shot["reason"] = (f"沙箱页面没能正常渲染（{after.get('status')}）："
                                      + str(after.get("error") or "无更多细节")[:200])
                    return shot
                before_errs = set((before or {}).get("console_errors") or [])
                after_errs = set(shot["console_errors"])
                shot["new_errors"] = sorted(after_errs - before_errs)[:10]
                shot["cleared_errors"] = sorted(before_errs - after_errs)[:10]
                judged = ver.judge_fix(before or {}, after, defect)
                shot["verdict"] = judged.get("verdict") or "unclear"
                shot["evidence"] = judged.get("evidence") or ""
                shot["status"] = "ok"
                if shot["verdict"] not in ("gone", "same", "unclear"):
                    shot["verdict"] = "unclear"
                return shot
            except Exception as e:
                shot["status"] = "failed"
                shot["reason"] = f"{type(e).__name__}: {e}"
                return shot
            finally:
                handle.stop()

        return hook

    # ------------------------------------------------------------ 闸门折算
    def _gate_of(self, agent: Dict, patch: Dict) -> Dict:
        """提案的闸门结论：**优先用沙箱里真跑过的结果**。

        旧行为有个说不出口的坑：layer-1 的 `gate.commands` 是拿 `cwd=目标仓库` 跑的，
        而补丁此时只在内存里——所以「闸门通过」其实只证明了「仓库现状通过」，与这份
        补丁改了什么无关。修复循环已经把同一批命令打在补丁上跑过了，没有理由继续用那个
        与补丁无关的结论。拿不到沙箱结果时（循环关闭 / 没跑成）才退回旧路径。
        """
        best = None
        for a in (agent.get("attempts") or []):
            if not a.get("built"):
                continue
            if best is None or int(a.get("rank", 0)) > int(best.get("rank", 0)):
                best = a
        if best and int(best.get("rank", 0)) >= 3 and (best.get("checks") or {}):
            checks = [{"name": n, "cmd": c.get("cmd", ""), "kind": "command",
                       "returncode": c.get("returncode", -1), "ok": bool(c.get("ok")),
                       "skipped": False, "seconds": c.get("seconds", 0),
                       "output_tail": (c.get("output") or "")[-3000:]}
                      for n, c in best["checks"].items()]
            failed = [c["name"] for c in checks if not c["ok"]]
            notes = [f"验收命令在**沙箱里打在补丁上**真跑过（第 {best.get('round')} 轮）；"
                     f"循环结论 {agent.get('conclusion')}"]
            notes.extend(agent.get("notes") or [])
            if not failed:
                notes.append("沙箱未复现工作区改动：补丁只落在临时副本里，你的仓库未被写入。")
            return {"level": "sandbox", "ok": not failed, "checks": checks,
                    "notes": notes, "failed_checks": failed,
                    "verification": best.get("verification") or {}}
        # 循环没跑出可用结论 → 沿用旧闸门（含降级语法检查），至少不比原来差
        return run_gate(self.repo_path, self.gate_cfg, patch["files"],
                        patched_contents=patch.get("patched_contents")).to_dict()

    def _analysis_without_repo(self, defect: Dict) -> Dict:
        return {
            "defect_id": defect.get("id"),
            "title": defect.get("title"),
            "keywords": [],
            "suspect_files": [],
            "read_files": [],
            "truncation": [],
            "root_cause": "(无仓库路径，未做代码扫描；仅基于描述给出方向性判断)",
            "category": "其他",
            "prevention": "",
            "explanation": "",
            "patch_text": "",
            "patch_blocks": [],
            "patch_canonical": "",
            "parse_errors": [],
            "block_count": 0,
            "ai_mode": self.ai.mode,
            "ai_error": "",
        }

    def _failure_result(self, defect: Dict, error: str) -> Dict:
        analysis = {
            "category": "其他",
            "root_cause": f"(处理异常: {error})",
            "ai_mode": self.ai.mode,
            "keywords": [], "suspect_files": [], "patch_text": "",
            "patch_canonical": "(处理异常)", "explanation": "",
        }
        patch = _empty_patch(f"处理异常: {error}")
        gate = {"level": "none", "ok": False, "checks": [],
                "notes": [f"异常: {error}"], "failed_checks": []}
        agent = {"enabled": bool(self.agent_enabled), "conclusion": "error",
                 "needs_human": True, "notes": [f"处理异常: {error}"], "attempts": []}
        proposal = self.proposals.create(defect, analysis, patch, gate,
                                         {"status": "skipped", "error": error},
                                         agent=agent)
        return {
            "defect": defect,
            "analysis": analysis,
            "patch": patch,
            "gate": gate,
            "agent": agent,
            "verify": {"status": "skipped", "error": error},
            "proposal": proposal,
            "proposal_id": proposal["id"],
            "fix": {"status": "failed", "error": error, "conclusion": "error"},
            "kb_path": "",
        }

    # ------------------------------------------------------------ approval
    def approve(self, proposal_id: str, force_gate: bool = False,
                note: str = "") -> Dict:
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"ok": False, "error": f"提案不存在: {proposal_id}"}
        if proposal["status"] == STATUS_APPLIED:
            return {"ok": False, "error": "该提案已采纳，避免重复提交"}
        if self.fixer is None:
            return {"ok": False, "error": "未配置可用仓库路径，无法应用任何提案"}

        # Surface the most fundamental blocker first: a wrong branch or a dirty tree
        # makes the proposal unsafe to apply regardless of what the gate thought.
        pre = self.fixer.preflight()
        self.proposals.update({**proposal, "preflight": pre})
        if pre["problems"]:
            return {"ok": False, "retryable": True, "preflight": pre,
                    "error": "; ".join(pre["problems"])}
        if proposal["status"] == "gate_failed" and not force_gate:
            failed = (proposal.get("gate") or {}).get("failed_checks") or []
            return {"ok": False, "retryable": True,
                    "error": f"验收闸门未通过（{', '.join(failed)}），"
                             "需人工确认风险后勾选“强制采纳”才会执行"}

        self.proposals.update({**proposal, "decision": {
            "approved": True, "forced": bool(force_gate), "note": note,
            "decided_at": _now(),
        }})

        result = self.fixer.apply(proposal, force_gate=force_gate)
        print(f"  [apply] {result['status']} files={len(result.get('files', []))} {str(result.get('error', ''))[:120]}")

        after: Dict = {"status": "skipped"}
        diff = {}
        if result.get("status") == "ok":
            # 重放 before 的同一路由+操作序列：前后截图必须"同页面同操作"，
            # 像素 diff 才有意义；重放也避免了第二次 AI 编排的不确定性。
            before_verify = proposal.get("verify") or {}
            replay = before_verify.get("replay_actions") or None
            after = self.verifier.capture(
                str(proposal["defect"].get("id")), "after", proposal["defect"],
                route=before_verify.get("route") or None,
                actions=replay,
            )
            diff = self.verifier.compare(proposal.get("verify") or {}, after)
            print(f"  [verify:after] {after['status']} diff={diff.get('status')}")
            result["verify"] = {"after": after, "compare": diff}

        proposal = self.proposals.get(proposal_id) or proposal
        proposal["apply"] = result
        proposal["verify_after"] = after
        proposal["verify_diff"] = diff

        # A preflight/drift failure means nothing was written — keep the proposal
        # reviewable so the user can fix the branch/stash and retry.
        if result.get("status") == "failed":
            self.proposals.update(proposal)
            return {"ok": False, "status": proposal["status"], "retryable": True,
                    "error": result.get("error", ""), "detail": result.get("detail", []),
                    "result": result, "proposal_id": proposal_id}

        status = STATUS_APPLIED if result.get("status") == "ok" else STATUS_APPLY_FAILED
        result["kb_path"] = self.kb.record_from_proposal(
            {**proposal, "status": status}
        )
        self.proposals.update({**proposal, "status": status})
        return {"ok": status == STATUS_APPLIED, "status": status, "result": result,
                "proposal_id": proposal_id}

    def reject(self, proposal_id: str, note: str = "") -> Dict:
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"ok": False, "error": f"提案不存在: {proposal_id}"}
        if proposal["status"] == STATUS_APPLIED:
            return {"ok": False, "error": "已采纳的提案不能用“拒绝”撤销，请先点「撤销采纳」恢复文件"}
        proposal["decision"] = {"approved": False, "note": note, "decided_at": _now()}
        self.proposals.update({**proposal, "status": STATUS_REJECTED})
        self.kb.record_from_proposal({**proposal, "status": STATUS_REJECTED})
        return {"ok": True, "status": STATUS_REJECTED, "proposal_id": proposal_id}

    def undo(self, proposal_id: str) -> Dict:
        """Undo an approved proposal: restore the working-tree files from the
        backups taken at apply time (no commits are involved anymore)."""
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"ok": False, "error": f"提案不存在: {proposal_id}"}
        apply_info = proposal.get("apply") or {}
        backups: Dict[str, str] = apply_info.get("backups") or {}
        if not backups:
            sha = (apply_info.get("sha") or "")[:40]
            if sha:
                return {"ok": False,
                        "error": "该提案来自旧版本（以 commit 方式采纳），请用 git revert 手工处理"}
            return {"ok": False, "error": "该提案没有可恢复的文件备份"}
        if self.fixer is None:
            return {"ok": False, "error": "未配置可用仓库路径，无法恢复文件"}

        # 撤销同样是往工作区写文件，所以和采纳走同一道前置检查：仓库不在了、或者
        # 已经切到别的分支，就不该再把属于另一分支状态的备份内容写回去。
        # （原先 undo 完全跳过 preflight，approve 却查——两条写盘路径不对称。）
        pre = self.fixer.preflight()
        if pre["problems"]:
            return {"ok": False, "preflight": pre, "error": "; ".join(pre["problems"])}

        patched = (proposal.get("patch") or {}).get("patched_contents") or {}
        try:
            targets = {rel: resolve_in_repo(self.fixer.repo_path, rel)
                       for rel in backups}
        except UnsafePath as e:
            return {"ok": False, "error": f"提案里的文件路径不安全，拒绝恢复：{e}"}

        touched = []
        try:
            for rel, original in backups.items():
                target = targets[rel]
                current = target.read_text(encoding="utf-8") if target.exists() else ""
                expected = patched.get(rel, "")
                if expected and current != expected:
                    return {"ok": False,
                            "error": f"{rel} 在采纳后被改过，自动恢复可能覆盖你的改动，请手工处理"}
            for rel, original in backups.items():
                targets[rel].write_text(original, encoding="utf-8")
                touched.append(rel)
        except Exception as e:
            # 半途失败时已经恢复的文件要说清楚，否则用户不知道工作区现在是什么状态
            done = ", ".join(touched) if touched else "无"
            return {"ok": False,
                    "error": f"恢复文件失败: {e}；已恢复的文件：{done}"}
        self.proposals.update({**proposal, "status": STATUS_REJECTED,
                               "apply": {**apply_info, "undone_at": _now(),
                                         "restored_files": touched}})
        return {"ok": True, "restored": touched}


def _empty_patch(reason: str) -> Dict:
    return {
        "ok": False, "blocks": [], "changes": [], "errors": [reason],
        "rejected": [], "warnings": [], "combined_diff": "", "files": [],
        "patched_contents": {}, "patch_text": "", "reason": reason,
    }


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
