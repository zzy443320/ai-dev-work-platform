# -*- coding: utf-8 -*-
"""写盘护栏回归：撤销采纳也要查前置条件，越界路径一律写不到仓库外面。

盯三件事（都是 2026-10-08 第二梯队补的）：
  1. `pipeline.undo` 原先不跑 preflight（`approve` 却跑）——分支切走了、仓库目录
     已经不存在，照样能把备份内容写下去。两条写盘路径的闸门不对称。
  2. `fixer.apply` / `pipeline.undo` 用裸拼接 `repo_path / rel` 算目标路径，纵深
     防御只有上游 `_norm_path` 一层；现在落盘前统一过 `repo_paths.resolve_in_repo`。
  3. `_restore` 原先把异常 `pass` 掉，于是回滚失败也照旧回报「已回滚（工作区未改动）」
     ——这句是会误导人的谎，现在如实返回失败的文件的清单。

跑法（不依赖 cwd）：
    PYTHONUTF8=1 .venv/Scripts/python.exe tests/check_write_guard.py
"""
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS_DIR))

from test_safety import config_for, git, make_repo, propose  # noqa: E402
from scripts.fixer import CodeFixer  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402
from scripts.repo_paths import UnsafePath, resolve_in_repo  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def _force_rm(path: Path) -> None:
    """删目录，带 Windows 的坑：.git/objects 里的文件是只读的。

    直接 rmtree 会撞 WinError 5，所以逐个把只读位清掉再删。
    """
    def onexc(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onexc=onexc)


def unit_resolve():
    print("\n[1] resolve_in_repo 单元表")
    root = Path(tempfile.mkdtemp(prefix="guard-repo-"))
    (root / "src").mkdir()
    ok_cases = {
        "src/App.tsx": root / "src" / "App.tsx",
        "a/./b.tsx": root / "a" / "b.tsx",
        "src//deep/x.ts": root / "src" / "deep" / "x.ts",
        "src\\Win\\Path.tsx": root / "src" / "Win" / "Path.tsx",  # Windows 反斜杠
    }
    for rel, want in ok_cases.items():
        try:
            got = resolve_in_repo(root, rel)
            check(f"仓内路径放行 {rel}", got == want.resolve(), f"got={got}")
        except UnsafePath as e:
            check(f"仓内路径放行 {rel}", False, f"被误拒: {e}")

    bad = ["../evil.tsx", "src/../../evil.tsx", "/etc/passwd", "C:/Windows/x.ts",
           "D:/repo-name/y.ts", "//server/share/z.ts", "", "   ", "./.."]
    for rel in bad:
        try:
            got = resolve_in_repo(root, rel)
            check(f"越界路径拒绝 {rel!r}", False, f"竟然放行 → {got}")
        except UnsafePath:
            check(f"越界路径拒绝 {rel!r}", True)

    # 符号链接跳出仓库：仓内一个软链指向仓外真实文件
    outside = Path(tempfile.mkdtemp(prefix="guard-outside-")) / "secret.ts"
    outside.write_text("export const secret = 1;\n", encoding="utf-8")
    link = root / "src" / "link.ts"
    try:
        link.symlink_to(outside)
        try:
            got = resolve_in_repo(root, "src/link.ts")
            check("符号链接指向仓外 → 拒", False, f"竟然放行 → {got}")
        except UnsafePath:
            check("符号链接指向仓外 → 拒", True)
    except (OSError, NotImplementedError) as e:
        print(f"  SKIP   符号链接用例（本机无权创建软链：{type(e).__name__}）")
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(outside.parent, ignore_errors=True)


def undo_checks_preflight():
    print("\n[2] undo 必须和 approve 一样查 preflight")
    repo = make_repo()
    pipe = AIDefectFixerPipeline(config_for(repo))
    p = propose(pipe)
    r = pipe.approve(p["id"])
    check("先正常采纳", r["ok"], r.get("error", "")[:120])
    target = repo / "src" / "ProcessList.tsx"
    patched = target.read_text(encoding="utf-8")

    # 切到别的分支后撤销：以前会照写，现在应当拒绝
    git(str(repo), "checkout", "-b", "elsewhere")
    u = pipe.undo(p["id"])
    check("分支不一致时拒绝撤销", not u["ok"], str(u)[:140])
    check("拒绝理由说清了分支", "分支" in u.get("error", ""), u.get("error", "")[:140])
    check("拒绝后文件仍是采纳后的样子",
          target.read_text(encoding="utf-8") == patched)

    # 仓库目录整个不再是 git 仓：也不许写
    git(str(repo), "checkout", "main")
    _force_rm(repo / ".git")
    u2 = pipe.undo(p["id"])
    check("目标已不是 git 仓库时拒绝撤销", not u2["ok"], str(u2)[:140])


def escape_patch_writes_nothing_outside():
    print("\n[3] 补丁目标越界：既被拒，也不会漏写到仓外")
    repo = make_repo()
    outside = repo.parent / "escape.tsx"
    cfg = config_for(repo)
    pipe = AIDefectFixerPipeline(cfg)
    p = propose(pipe)
    # 手工把提案的补丁文本改成仓外目标（模拟上游洗路径被绕过的最坏情况）
    block = ("<<<<<<< SEARCH ../escape.tsx\nwhatever\n=======\nhacked\n>>>>>>> REPLACE")
    p = {**p, "patch": {**(p.get("patch") or {}), "patch_text": block,
                        "changes": [{"file_path": "../escape.tsx", "diff": ""}],
                        "patched_contents": {"../escape.tsx": "hacked"}}}
    fixer = CodeFixer(str(repo), branch="main")
    res = fixer.apply(p)
    check("越界补丁未写入", not res.get("ok") or res.get("status") != "ok", str(res)[:160])
    check("仓外没有被创建文件", not outside.exists(), str(outside))
    check("报错里能看出是路径问题", "路径" in str(res) or "SEARCH" in str(res),
          str(res.get("error", ""))[:140])


def restore_reports_failures():
    print("\n[4] _restore 如实报告回滚失败")
    repo = make_repo()
    fixer = CodeFixer(str(repo), branch="main")
    # 一个必然写不回去的条目：目标是目录，不是文件
    bad = [{"file": "src", "backup": "// original\n"}]
    unrestored = fixer._restore(bad)
    check("回滚失败会被列出来", unrestored == ["src"], str(unrestored))
    ok = [{"file": "src/ProcessList.tsx", "backup": "// restored\n"}]
    check("正常回滚返回空列表", fixer._restore(ok) == [])
    check("内容确实写回去了",
          (repo / "src" / "ProcessList.tsx").read_text(encoding="utf-8") == "// restored\n")


def store_locking():
    print("\n[6] set_status 的「读-改-写」在并发下不丢更新")
    import threading
    import time

    from scripts.proposal import ProposalStore

    base = Path(tempfile.mkdtemp(prefix="guard-store-")) / "proposals"
    st = ProposalStore(str(base))
    p = st.create({"id": "LOCK-1", "title": "锁"}, {"category": "逻辑"},
                  {"ok": True, "changes": []}, {"ok": True})
    pid = p["id"]
    real_get = st.get

    def slow_get(x):
        r = real_get(x)
        time.sleep(0.05)        # 放大竞态窗口：没有锁的话两个线程都会读到同一份旧数据
        return r

    st.get = slow_get
    errs = []

    def run(store, target, extra):
        # 子线程里的异常不会传回主线程，只会打一行 stderr 就被吞掉——上一版就是这么
        # 「两头假通过」的（Thread 的 kwargs 会把参数拆成关键字参数传给目标函数）。
        # 所以显式收集，任何一次抛错都算用例失败。
        try:
            store.set_status(target, **extra)
        except Exception as e:          # noqa: BLE001
            errs.append(f"{type(e).__name__}: {e}")

    def pair(store, target, a, b):
        ts = [threading.Thread(target=run, args=(store, target, x)) for x in (a, b)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

    pair(st, pid,
         {"status": "applied", "apply": {"a": 1}},
         {"status": "rejected", "decision": {"note": "b"}})
    check("两个并发写线程都没抛错", not errs, "; ".join(errs)[:160])
    final = real_get(pid)
    both = (final.get("apply") or {}).get("a") == 1 and \
           (final.get("decision") or {}).get("note") == "b"
    check("加锁后两次写入的字段都在", both,
          str({k: final.get(k) for k in ("apply", "decision")})[:120])

    # 反向对照：同一套并发跑在「没有锁」的实例上必须丢字段，否则上面的断言是空炮。
    class _NoLock:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    st2 = ProposalStore(str(base))
    p2 = st2.create({"id": "LOCK-2", "title": "无锁"}, {"category": "逻辑"},
                    {"ok": True, "changes": []}, {"ok": True})
    st2.get = slow_get
    st2._lock = _NoLock()
    errs2 = []
    def run2(store, target, extra):
        try:
            store.set_status(target, **extra)
        except Exception as e:          # noqa: BLE001
            errs2.append(f"{type(e).__name__}: {e}")
    ts = [threading.Thread(target=run2, args=(st2, p2["id"], x)) for x in (
        {"status": "applied", "apply": {"a": 1}},
        {"status": "rejected", "decision": {"note": "b"}})]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("无锁对照组也没抛错（否则下面那条是假通过）", not errs2, "; ".join(errs2)[:160])
    bad = real_get(p2["id"])
    lost = not ((bad.get("apply") or {}).get("a") == 1 and
                (bad.get("decision") or {}).get("note") == "b")
    check("反向对照：去掉锁确实会丢更新（说明用例咬得住）", lost,
          str({k: bad.get(k) for k in ("apply", "decision")})[:120])


def http_wiring_is_live():
    print("\n[5] 写盘面在 HTTP 上确实可达（server.py 模块化拆分后的装配验证）")
    # 单元测试过 store 层不够：approve/undo 挪进 web/routers/review.py 之后，如果
    # include_router 漏了或路径写错，只有真发请求才会暴露（OpenAPI 表能看出路径，
    # 但看不出中间件/依赖是否照常工作）。
    import json
    import urllib.error
    import urllib.request

    from temp_server import serve
    with serve() as srv:
        def call(path, method="GET", body=None):
            req = urllib.request.Request(srv.base + path, method=method,
                                         data=json.dumps(body or {}).encode() if method == "POST" else None,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")

        code, data = call("/api/proposals")
        check("GET /api/proposals 可达", code == 200 and isinstance(data, list),
              f"code={code}")
        code, data = call("/api/proposals/nope-404/approve", "POST", {})
        check("POST approve 路由在位（不存在的提案给出业务错误而非 404 路由缺失）",
              code in (200, 409) and "error" in data, f"code={code} {str(data)[:80]}")
        code, data = call("/api/proposals/nope-404/undo", "POST")
        check("POST undo 路由在位", code in (200, 409) and "不存在" in str(data),
              f"code={code} {str(data)[:80]}")
        code, data = call("/api/artifacts")
        check("GET /api/artifacts 可达", code == 200 and isinstance(data, list), f"code={code}")
        code, data = call("/api/artifacts/nope/approve", "POST", {})
        check("产出物 approve 路由在位", code in (200, 404, 409), f"code={code}")


if __name__ == "__main__":
    unit_resolve()
    undo_checks_preflight()
    escape_patch_writes_nothing_outside()
    restore_reports_failures()
    store_locking()
    http_wiring_is_live()
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    sys.exit(1 if FAIL else 0)
