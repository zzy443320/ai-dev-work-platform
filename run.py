"""CLI entry point.

  python run.py --config config.yaml --mock --limit 3     # generate proposals only
  python run.py --config config.yaml --list-proposals     # what is waiting for review
  python run.py --config config.yaml --show ONES-1001-…   # print one diff

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
    p.add_argument("--mock", action="store_true", help="用内置样本代替 ONES")
    p.add_argument("--dry-run", action="store_true",
                   help="已废弃：任何模式下都不会改代码，只跳过页面截图")
    p.add_argument("--skip-verify", action="store_true", help="跳过 Playwright 截图")
    p.add_argument("--defect-id", default=None)
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--list-proposals", action="store_true", help="列出待审批提案")
    p.add_argument("--show", default=None, metavar="PROPOSAL_ID", help="打印某个提案的 diff")
    args = p.parse_args()

    if Path(args.config).exists():
        config = load_config(args.config)
        for k, v in BASE_CONFIG.items():
            config.setdefault(k, v)
    else:
        print(f"[config] {args.config} not found; using mock defaults")
        config = json.loads(json.dumps(BASE_CONFIG))

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

    results = pipeline.run(
        defect_id=args.defect_id,
        mock=args.mock,
        dry_run=args.dry_run,
        limit=args.limit,
        skip_verify=args.skip_verify,
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
    main()
