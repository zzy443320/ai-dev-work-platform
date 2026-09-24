"""End-to-end orchestrator.

ONES → locate → SEARCH/REPLACE patch → gate → screenshot → **proposal awaiting
approval**. This module never writes to the target repository. Writing happens only
through `approve()`, which is what the Web UI's 采纳 button calls.
"""
from pathlib import Path
from typing import Dict, List, Optional

from . import usage
from .ai_model import AIModel
from .analyzer import DefectAnalyzer
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

        self.preflight = self.fixer.preflight() if self.fixer else {
            "problems": ["未配置可用仓库路径，只能产出分析结论，不会生成补丁"],
            "repo": self.repo_path, "is_git": False, "current_branch": "",
            "expected_branch": self.branch, "branch_ok": False, "dirty": False,
        }

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
                    "列表里的数字编号（如 205417）或工单 UUID",
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
            patch = self.fixer.preview(analysis["patch_text"]) if self.fixer else _empty_patch("无 fixer")
            patch["patch_text"] = analysis["patch_text"]
            if patch["ok"]:
                _emit(emit, {"type": "stage", "stage": "patch", "status": "done",
                             "defect": did,
                             "detail": f"补丁构建成功：{len(patch.get('blocks') or [])} 个块 → "
                                       f"{', '.join(patch.get('files') or [])}"})
                gate = run_gate(
                    self.repo_path, self.gate_cfg, patch["files"],
                    patched_contents=patch.get("patched_contents"),
                ).to_dict()
            else:
                reason = "；".join((patch.get("errors") or [])[:2]) or "补丁未构建成功"
                _emit(emit, {"type": "stage", "stage": "patch", "status": "warn",
                             "defect": did, "detail": f"没有可应用补丁：{reason}"})
                gate = {"level": "none", "ok": False, "checks": [],
                        "notes": ["补丁未构建成功，未执行验收"], "failed_checks": []}
        _emit(emit, {"type": "stage", "stage": "gate", "status": "done" if gate.get("ok") else "warn",
                     "defect": did,
                     "detail": f"验收闸门 level={gate.get('level')} ok={gate.get('ok')}"})

        if skip_verify or not self.repo_path:
            verify = {"status": "skipped", "reason": "按请求跳过页面验证"}
        else:
            _emit(emit, {"type": "stage", "stage": "verify", "status": "start",
                         "defect": did, "detail": "打开页面、复刻操作并截图…"})
            verify = self.verifier.capture(did, "before", defect, on_delta=on_delta)
            print(f"  [verify:before] {verify['status']} route={verify.get('route')}")
            _emit(emit, {"type": "stage", "stage": "verify",
                         "status": "done" if verify.get("status") in ("ok", "error_visible") else "warn",
                         "defect": did,
                         "detail": f"页面验证：{verify.get('status')}（route={verify.get('route')}）"})

        proposal = self.proposals.create(
            defect=defect, analysis=analysis, patch=patch, gate=gate, verify=verify
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
            "proposal": proposal,
            "proposal_id": proposal["id"],
            "fix": {"status": proposal["status"]},
            "kb_path": kb_path,
        }

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
        proposal = self.proposals.create(defect, analysis, patch, gate,
                                         {"status": "skipped", "error": error})
        return {
            "defect": defect,
            "analysis": analysis,
            "patch": patch,
            "gate": gate,
            "verify": {"status": "skipped", "error": error},
            "proposal": proposal,
            "proposal_id": proposal["id"],
            "fix": {"status": "failed", "error": error},
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

        patched = (proposal.get("patch") or {}).get("patched_contents") or {}
        touched = []
        try:
            for rel, original in backups.items():
                target = self.fixer.repo_path / rel
                current = target.read_text(encoding="utf-8") if target.exists() else ""
                expected = patched.get(rel, "")
                if expected and current != expected:
                    return {"ok": False,
                            "error": f"{rel} 在采纳后被改过，自动恢复可能覆盖你的改动，请手工处理"}
            for rel, original in backups.items():
                (self.fixer.repo_path / rel).write_text(original, encoding="utf-8")
                touched.append(rel)
        except Exception as e:
            return {"ok": False, "error": f"恢复文件失败: {e}"}
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


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
