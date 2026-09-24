# -*- coding: utf-8 -*-
"""离线重放：用当前 analyzer 在历史工单上跑定位（不调 AI），打印排名与读取窗口。"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyzer import DefectAnalyzer, load_precedent_files, strip_html  # noqa: E402


def replay(proposal_path: str):
    prop = json.loads(Path(proposal_path).read_text(encoding="utf-8"))
    defect = prop.get("defect") or prop
    settings = json.loads((PROJECT_ROOT / "ui_settings.json").read_text(encoding="utf-8"))
    repo = settings.get("repo", {}).get("path", "")
    a = DefectAnalyzer(repo, None, precedent_dir=str(PROJECT_ROOT / "proposals"))
    text = f"{defect.get('title', '')}\n{strip_html(defect.get('description', ''))}"
    print(f"===== {Path(proposal_path).name} =====")
    print("TITLE:", defect.get("title", ""))
    kws, ptoks = a._extract_keywords(text)
    print("path_tokens:", ptoks)
    kws, dropped = a._drop_high_freq_keywords(kws)
    print("keywords:", kws)
    print("freq_dropped:", dropped)
    a._wants_validation = any(t in text.lower() for t in ("必填", "校验", "required", "validate", "validator"))
    a._precedent_files = load_precedent_files(str(PROJECT_ROOT / "proposals"))
    files = a._scan_repo(kws, ptoks)
    print("suspect top12:")
    for f in files[:12]:
        print("   ", f)
    snips, meta = a._read_contexts(files, kws)
    print("read:")
    for f in snips:
        print("   ", f, f"({len(snips[f])} chars)")
    return a, kws, ptoks, files, snips


if __name__ == "__main__":
    for p in sys.argv[1:]:
        replay(p)
