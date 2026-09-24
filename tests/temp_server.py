# -*- coding: utf-8 -*-
"""给界面自检用的「临时服务实例」：所有落盘目录都指到临时目录。

`web/server.py` 的每个数据目录都能被环境变量覆盖（`KB_DIR` / `PROPOSAL_DIR` /
`ARTIFACT_DIR` / `TEAM_DIR` / `CHAT_DIR` / `SCREENSHOT_DIR`，账本在 scripts/usage.py
里读 `USAGE_DIR`），设置文件用 `SETTINGS_FILE`、目标仓库用 `REPO_PATH`。这里把它们
**一次性全指到同一个临时目录**，于是用例既读不到你的真实配置，也写不进你的真实数据，
更不需要你先手工准备一份带数据的知识库。

    with temp_server.serve(kb="fixture") as srv:      # kb="fixture" 用脱敏夹具知识库
        page.goto(srv.base + "/")

`kb` 可选值：None（空知识库）/ "fixture"（tests/kb_fixture.py 造的一套假工单卡）/ 目录路径。
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TempServer:
    def __init__(self, proc, base: str, root: Path, port: int):
        self.proc = proc
        self.base = base
        self.root = root
        self.port = port
        self.kb_dir = root / "knowledge_base"
        self.settings_file = root / "ui_settings.json"

    def api(self, path: str, timeout: int = 20) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))


@contextlib.contextmanager
def serve(*, kb=None, settings: dict | None = None, keep: bool = False,
          extra_env: dict | None = None):
    """起一个临时实例；退出时关掉进程并删掉临时目录（keep=True 则保留供排查）。"""
    root = Path(tempfile.mkdtemp(prefix="wb_ui_tmp_"))
    dirs = {k: root / k for k in
            ("proposals", "artifacts", "team_runs", "chat_sessions",
             "screenshots", "usage", "knowledge_base", "repo")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    if kb == "fixture":
        import kb_fixture
        kb_fixture.build(dirs["knowledge_base"])
    elif kb:
        shutil.copytree(Path(kb), dirs["knowledge_base"], dirs_exist_ok=True)

    cfg = {
        "repo": {"path": str(dirs["repo"]), "branch": "main"},
        "ai": {"mock": True, "api_key": "", "provider": "openai", "model": "mock"},
        "gate": {"commands_text": ""},
    }
    cfg.update(settings or {})
    settings_file = root / "ui_settings.json"
    settings_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    port = free_port()
    env = dict(os.environ)
    env.update({
        "SETTINGS_FILE": str(settings_file),
        "KB_DIR": str(dirs["knowledge_base"]),
        "PROPOSAL_DIR": str(dirs["proposals"]),
        "ARTIFACT_DIR": str(dirs["artifacts"]),
        "TEAM_DIR": str(dirs["team_runs"]),
        "CHAT_DIR": str(dirs["chat_sessions"]),
        "SCREENSHOT_DIR": str(dirs["screenshots"]),
        "USAGE_DIR": str(dirs["usage"]),
        "REPO_PATH": str(dirs["repo"]),
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(extra_env or {})

    code = ("import uvicorn; from web.server import app; "
            f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='error')")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            if proc.poll() is not None:
                out = (proc.stdout.read() or b"").decode("utf-8", "replace")
                raise RuntimeError(f"临时服务启动失败：\n{out[-2000:]}")
            try:
                urllib.request.urlopen(base + "/api/health", timeout=2).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("临时服务 30s 内未就绪")
        yield TempServer(proc, base, root, port)
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=10)
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            print(f"[temp_server] 临时目录保留在 {root}")
