"""CLI entry point.

  python run.py --config config.yaml --demo --limit 1       # 不接 ONES 先看效果
  python run.py --config config.yaml --limit 3             # 从 ONES 拉真实工单
  python run.py --config config.yaml --list-proposals      # what is waiting for review
  python run.py --config config.yaml --show ONES-1001-…    # print one diff

The pipeline never modifies the target repository. Approve proposals in the Web UI
(python -m web.server → http://127.0.0.1:8765), which is the only path that commits.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scripts.mock_data import DEMO_DEFECT  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402


def load_config(path: str) -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        raw = f.read()

    def expand(m):
        var = m.group(1)
        return os.environ.get(var, m.group(0))

    raw = re.sub(r"\$\{([A-Z_][A-Z0-9_]*)}", expand, raw)
    return yaml.safe_load(raw)


BASE_CONFIG = {
    "ones": {"base_url": "", "token": "", "project_uuid": ""},
    "ai": {"model": "claude-3-5-sonnet-latest"},
    "repo": {"path": "", "branch": ""},
    "gate": {},
    "playwright": {
        "base_url": "http://localhost:3000", "headless": True,
        "screenshot_dir": "./screenshots",
    },
    "knowledge_base": {"output_dir": "./knowledge_base"},
    "proposals": {"output_dir": "./proposals"},
}


def main():
    p = argparse.ArgumentParser(description="ONES defect → proposal pipeline")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--mock", action="store_true",
                   help="不调 ONES。示例工单已移除，单独用它不会产出任何工单，配合 --demo 使用")
    p.add_argument("--demo", action="store_true",
                   help="注入一条显式演示工单（DEMO-1）跑完整流程，无需 ONES 配置")
    p.add_argument("--dry-run", action="store_true",
                   help="已废弃：任何模式下都不会改代码，只跳过页面截图")
    p.add_argument("--skip-verify", action="store_true", help="跳过 Playwright 截图")
    p.add_argument("--defect-id", default=None)
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--list-proposals", action="store_true", help="列出待审批提案")
    p.add_argument("--show", default=None, metavar="PROPOSAL_ID", help="打印某个提案的 diff")
    # 知识库保底三条（knowledge_base/ 不入库，是流水线唯一会增值的资产，必须有备份
    # 与可提交的脱敏副本）。三条都在建流水线之前处理，不需要 ONES / 模型配置。
    p.add_argument("--backup-kb", action="store_true",
                   help="把知识库整份打包成带时间戳的 zip（放 kb_backups/，不入库）")
    p.add_argument("--export-kb", metavar="OUT", nargs="?",
                   const="knowledge_base_masked", default=None,
                   help="导出脱敏副本（默认 knowledge_base_masked/）；会先从凭据行自动"
                        "补全账号名词表，确认零残留后再提交")
    p.add_argument("--audit-kb", metavar="DIR", nargs="?",
                   const="knowledge_base_masked", default=None,
                   help="比对原件，检查脱敏副本里是否还残留敏感信息（有残留则退出码 1）")
    args = p.parse_args()

    if Path(args.config).exists():
        config = load_config(args.config)
        for k, v in BASE_CONFIG.items():
            config.setdefault(k, v)
    else:
        print(f"[config] {args.config} not found; using mock defaults")
        config = json.loads(json.dumps(BASE_CONFIG))

    kb_dir = Path((config.get("knowledge_base") or {}).get("output_dir")
                  or "./knowledge_base")

    if args.backup_kb:
        from scripts.kb_backup import backup_zip
        zp = backup_zip(kb_dir, Path("kb_backups"))
        print(f"[kb] 备份完成 → {zp}（{zp.stat().st_size // 1024} KB，"
              f"含整份原始知识库；该目录不入库）")
        return 0

    if args.export_kb:
        from scripts.kb_backup import (export_masked, seed_terms_from_credentials,
                                       summarize, write_keep_template,
                                       write_term_template)
        write_term_template(kb_dir)
        write_keep_template(kb_dir)
        seeded = seed_terms_from_credentials(kb_dir)
        if seeded:
            print(f"[kb] 从凭据行自动补入 {len(seeded)} 个测试账号名到 "
                  f"{kb_dir / 'mask_terms.txt'}（该文件不入库）")
        print(summarize(export_masked(kb_dir, Path(args.export_kb))))
        return 0

    if args.audit_kb:
        from scripts.kb_backup import audit_masked, redact
        rep = audit_masked(kb_dir, Path(args.audit_kb))
        print(f"[kb] 自检：原件抽出 {rep['checked']} 个敏感原子"
              f"（内网主机名 / 账号口令 / 工单记录号 / 绝对路径），"
              f"白名单 {rep['kept']} 个")
        if rep["leaked"]:
            print(f"[kb] ⚠️ 副本里仍有 {len(rep['leaked'])} 个残留，不能提交：")
            for item in rep["leaked"][:20]:
                src = "、".join(item["where"][:3])
                print(f"     {redact(item['token'])}  ← {src}")
            if len(rep["leaked"]) > 20:
                print(f"     …另有 {len(rep['leaked']) - 20} 个")
            print(f"[kb] 处理：把这些串（或其共同前缀）加进 {kb_dir / 'mask_terms.txt'} "
                  f"后重新 --export-kb")
            return 1
        print("[kb] 零残留，副本可以提交")
        return 0

    pipeline = AIDefectFixerPipeline(config)

    if args.show:
        _show(pipeline, args.show)
        return
    if args.list_proposals:
        _list(pipeline)
        return

    if args.dry_run:
        print("[warn] --dry-run 已无实际作用：流水线本来就只生成提案、不写代码。")

    pre = pipeline.preflight
    print(f"[init] AI mode: {pipeline.ai.mode} ({pipeline.ai.model})")
    print(
        "[init] repo: {}  branch={} expected={}".format(
            pipeline.repo_path or "(未配置)",
            pre.get("current_branch") or "?",
            pre.get("expected_branch") or "?",
        )
    )
    for problem in pre.get("problems", []):
        print(f"[init][warn] {problem}")
    cmds = pipeline.describe()["gate_commands"]
    print(f"[init] gate: " + (", ".join(f"{c['name']}={c['cmd']}" for c in cmds)
                              if cmds else "无可用命令，降级为语法检查"))

    injected = None
    if args.demo:
        injected = [DEMO_DEFECT]
        print(f"[demo] 注入演示工单 {DEMO_DEFECT['id']}：{DEMO_DEFECT['title']}")
        print("       演示数据固定带 DEMO- 前缀，与真实工单一眼可辨（不参与任何自动兜底）。")
    else:
        ones = config.get("ones") or {}
        if not (ones.get("base_url") and ones.get("token")):
            print("[提示] 未配置 ONES（ones.base_url / ones.token），直接跑会拉不到工单。"
                  "想先看完整效果，加 --demo。")

    results = pipeline.run(
        defect_id=args.defect_id,
        mock=args.mock,
        dry_run=args.dry_run,
        limit=args.limit,
        skip_verify=args.skip_verify,
        defects=injected,
    )

    ready = [r for r in results if r["proposal"]["status"] in ("pending", "gate_failed")]
    print(f"\nProcessed {len(results)} defects → {len(ready)} 个提案可审批")
    for r in results:
        prop = r["proposal"]
        print(f"  - {prop['id']}: {prop['status']} "
              f"{len(prop['patch'].get('files', []))} 文件 → {r['kb_path']}")
    if ready:
        print("\n下一步：python -m web.server 打开 http://127.0.0.1:8765 审阅 diff 并采纳。")


def _list(pipeline):
    rows = pipeline.proposals.list()
    if not rows:
        print("暂无提案。先运行一次流水线：python run.py --config config.yaml --mock")
        return
    print(f"{'提案 ID':40} {'状态':12} {'闸门':10} 文件  +  -")
    for r in rows:
        print(f"{r['id']:40} {r['status']:12} {str(r['gate_level']):10} "
              f"{len(r['files']):3} {r['added']:2} {r['removed']:2}  {r['title'][:30]}")


def _show(pipeline, pid):
    p = pipeline.proposals.get(pid)
    if not p:
        print(f"未找到提案 {pid}")
        return
    print(json.dumps({k: p[k] for k in ("id", "status", "created")}, ensure_ascii=False))
    print(f"\n根因: {p['analysis'].get('root_cause', '')}\n")
    print(p["patch"].get("combined_diff") or "(无 diff)")
    print("\n错误:", p["patch"].get("errors") or "无")
    print("告警:", p["patch"].get("warnings") or "无")
    print("闸门:", json.dumps(p.get("gate", {}), ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    sys.exit(int(main() or 0))
