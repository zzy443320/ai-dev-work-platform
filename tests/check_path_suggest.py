r"""回归：路径写错时的自救 + 夹带 tool call 的回复解析（工单 215890 复盘）。

2026-10-15 工单 215890（「【迭代106】移动端定时任务编辑时，选不了模型」）跑完
status=invalid、needs_human=true：7 轮预算烧光，一个字都没改。复盘出的两条硬伤：

**一、工具只回「文件不存在」，不给任何方向**
工单描述与先例卡片里的路径都过时了（真实落点带 `runtime/` 前缀）：
  - 第 3 轮 repo_read 猜 `packages/cloudpivot-ai/lui/mobile/pages/schedule/schedule-editor.vue`
    → 只拿到一句「文件不存在」，模型只能继续猜
  - 第 4 轮又对 `packages/cloudpivot-ai/lui/pages/schedule/schedule-editor.vue`
    （同样不存在）交了补丁 → 沙箱以「文件不存在」拒绝，白烧一轮
  - 第 6 轮才靠 repo_grep 撞到真实路径 `packages/cloudpivot-ai/runtime/lui/mobile/...`
    → 第 7 轮预算耗尽，只能 give_up
修法：`RepoReader.suggest_paths` 按文件名（退到目录名）在仓库里给出相近的真实路径，
`repo_read` / `repo_dir` 报错时一并返回；补丁打偏到不存在路径时的回喂里也带上。

**二、回复「正文 + 夹带 action JSON」时两头落空**
第 4 轮原文既有一段**空** SEARCH/REPLACE 块，又跟着一条 repo_grep 调用。旧解析只认
「整条回复以 `{` 开头」，结果 grep 没执行、空块被当补丁送去沙箱。
修法：`parse_embedded_action` 从回复里捞出工具调用；只在补丁块全为空时才用它。

用例自带临时 git 仓库，不依赖用户的目标仓库。
用法：python tests/check_path_suggest.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.chat import RepoReader  # noqa: E402
from scripts.fix_agent import parse_action, parse_embedded_action  # noqa: E402
from scripts.patch_engine import parse_blocks  # noqa: E402

_PASS = 0
_FAIL = 0

# 215890 第 4 轮的原文（截自 proposals/L4Zo7p9zlZYhlDQY-20261015-191441.json）
ROUND4_TEXT = (
    "<<<<<<< SEARCH packages/cloudpivot-ai/lui/pages/schedule/schedule-editor.vue\n"
    "=======\n"
    ">>>>>>> REPLACE\n\n"
    "Wait — 我不能这样空提交。先查仓库。\n\n"
    '{"action":"call","tool":"repo_grep","arguments":'
    '{"pattern":"schedule-editor","glob":"**/*.vue"}}'
)


def ok(cond: bool, label: str, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [ok]   {label}" + (f" — {detail[:90]}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {detail[:200]}" if detail else ""))


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)


def make_repo(root: Path) -> None:
    """最小 monorepo：同名文件散在不同前缀下 + 一个易猜错的旧路径。"""
    ed = root / "packages" / "cloudpivot-ai" / "runtime" / "lui" / "mobile" / "pages" / "schedule"
    ed.mkdir(parents=True)
    (ed / "schedule-editor.vue").write_text(
        "<template>\n  <div class=\"lui-mobile-schedule-editor-popup\">\n"
        "    <label class=\"lui-mobile-schedule-editor__field\">模型</label>\n"
        "  </div>\n</template>", encoding="utf-8")
    pc = root / "packages" / "cloudpivot-ai" / "runtime" / "lui" / "pages" / "schedule"
    pc.mkdir(parents=True)
    (pc / "schedule-editor.vue").write_text("<template><div/></template>", encoding="utf-8")
    (root / "packages" / "cloudpivot-ai" / "runtime" / "lui" / "store").mkdir(parents=True)
    (root / "packages" / "cloudpivot-ai" / "runtime" / "lui" / "store"
     / "workspace-store.ts").write_text("export const s = 1;\n", encoding="utf-8")
    git(root, "init")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")


def main() -> None:
    if not shutil.which("git"):
        print("SKIP：没有 git")
        return
    tmp = Path(tempfile.mkdtemp(prefix="ones-path-suggest-"))
    try:
        repo = tmp / "repo"
        repo.mkdir()
        make_repo(repo)
        r = RepoReader(str(repo))

        print("[1] repo_read 猜错路径时要给真实路径（215890 第 3 轮死点）")
        out = r.read_file(
            "packages/cloudpivot-ai/lui/mobile/pages/schedule/schedule-editor.vue")
        ok(out.startswith("[错误] 文件不存在"),
           "仍然如实说文件不存在", out.splitlines()[0][:80])
        ok("packages/cloudpivot-ai/runtime/lui/mobile/pages/schedule/schedule-editor.vue"
           in out, "给出带 runtime/ 的真实路径")
        ok("不要照抄工单描述或历史卡片里的旧路径" in out,
           "明确禁止照抄工单/卡片里的旧路径")
        ok(out.index("runtime/lui/mobile") < out.index("runtime/lui/pages"),
           "与请求路径重合更多的候选排前面（mobile 在前）")

        print("\n[2] repo_list 目录写错时同样给候选")
        out_d = r.list_dir("packages/cloudpivot-ai/lui")
        ok(out_d.startswith("[错误] 目录不存在"), "如实说目录不存在", out_d.splitlines()[0][:80])
        ok("packages/cloudpivot-ai/runtime/lui" in out_d, "给出真实目录")

        print("\n[3] 确实没有相近路径时不硬编（不能骗模型）")
        out_n = r.read_file("packages/cloudpivot-ai/lui/pages/xx-nonexistent-page.vue")
        ok("相近" not in out_n, "没有候选就只报错，不瞎给路径", out_n[:120])

        print("\n[4] 夹带 tool call 的回复要能被捞出（215890 第 4 轮死点）")
        ok(parse_action(ROUND4_TEXT) is None,
           "旧的严格解析确实解析不出来（复现失败点）")
        emb = parse_embedded_action(ROUND4_TEXT)
        ok(bool(emb) and emb.get("tool") == "repo_grep",
           "新解析捞出 repo_grep 调用", str(emb))
        ok(emb.get("arguments", {}).get("pattern") == "schedule-editor",
           "arguments 一并保留", str(emb.get("arguments")))
        blocks, _errs = parse_blocks(ROUND4_TEXT)
        ok(bool(blocks) and not [b for b in blocks
                                 if (b.search or "").strip() or (b.replace or "").strip()],
           "该轮补丁块确实是空的（所以旧路径会白烧一轮）", str(len(blocks)))

        print("\n[5] 正常补丁回复不许被误判成工具调用")
        patch_text = ("<<<<<<< SEARCH packages/cloudpivot-ai/runtime/lui/store/"
                      "workspace-store.ts\n"
                      "export const s = 1;\n"
                      "=======\n"
                      "export const s = 2;\n"
                      ">>>>>>> REPLACE")
        ok(parse_embedded_action(patch_text) is None,
           "纯 SEARCH/REPLACE 回复里没有夹带调用")
        pblocks = [b for b in parse_blocks(patch_text)[0]
                   if (b.search or "").strip()]
        ok(len(pblocks) == 1, "该回复被正常识别成补丁块")
        # 代码正文里出现 {"action":"call"} 这种字面量时，只有它带已知工具名才算数
        ok(parse_embedded_action('说明文字 {"action":"call","tool":"随便写的"} 结束') is None,
           "未知工具名不认，避免正文误触")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {_PASS} 项，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
