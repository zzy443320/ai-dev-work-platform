"""Prove the transport is genuinely config-driven, not Anthropic-with-a-different-name.

A fake relay answers at `data.reply` behind `/gw/chat` with a custom auth header —
nothing matches an official SDK default, so a pass means the config layer works.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_relay  # noqa: E402
import scripts.pipeline as _pl  # noqa: E402
from fixture_defect import TEST_DEFECT  # noqa: E402
from scripts.ai_model import AIModel  # noqa: E402
from scripts.ai_providers import AIConfig, AIError  # noqa: E402
from scripts.pipeline import AIDefectFixerPipeline  # noqa: E402

# 内置示例工单已移除：测试用自己的固定 fixture 注入
_pl.SAMPLE_DEFECTS[:] = [TEST_DEFECT]

FAIL = []
TMP = Path(__file__).resolve().parent.parent / ".relay-test-repo"


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def ai_config(base, **over):
    cfg = {
        "provider": "custom",
        "base_url": base,
        "endpoint": "/gw/chat",
        "models_path": "/gw/models",
        "content_path": "data.reply",
        "auth_style": "header",
        "auth_header": "X-Gw-Token",
        "api_key": "gw-secret-abcdef123456",
        "model": "gw-claude-pro",
        "temperature": 0.2,
        "max_tokens": 1200,
        "timeout": 20,
        "json_mode": False,
        "extra_body": {"tenant": "h3yun"},
        "extra_headers": {"X-Tenant": "h3yun"},
    }
    cfg.update(over)
    return cfg


def make_repo():
    import shutil
    import subprocess

    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    (TMP / "src").mkdir(parents=True)
    (TMP / "src" / "ProcessList.tsx").write_text(
        "// 流程列表\n"
        "export const ProcessList = (props) => {\n"
        "  const name = props.items.template.name;\n"
        "  return <div>{name}</div>;\n"
        "};\n",
        encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_EMAIL": "t@t"}
    import os

    e = {**os.environ, **env}
    for a in (["init", "-b", "main"], ["add", "-A"], ["commit", "-m", "init"]):
        subprocess.run(["git", *a], cwd=str(TMP), capture_output=True, text=True, env=e)
    return TMP


def pipe_config(repo, ai):
    import tempfile

    return {
        "ones": {"base_url": "", "token": "", "project_uuid": ""},
        "ai": ai,
        "repo": {"path": str(repo), "branch": "main"},
        "gate": {"degraded": True},
        "playwright": {"base_url": "http://127.0.0.1:1",
                       "screenshot_dir": "./screenshots"},
        "knowledge_base": {"output_dir": str(Path(tempfile.mkdtemp()) / "kb")},
        "proposals": {"output_dir": str(Path(tempfile.mkdtemp()) / "proposals")},
    }


def main():
    srv, base = fake_relay.start()
    print("fake relay at", base)
    try:
        print("\n[1] 非标准路径 / 自定义鉴权头 / 非标准响应字段")
        m = AIModel(ai_config(base))
        check("mode 反映 provider 而非写死", m.mode == "custom", m.mode)
        r = m.probe()
        check("连通性探测成功", r.get("ok") is True, str(r)[:200])
        check("回显内容正确", r.get("reply") == "收到", r.get("reply"))
        last = fake_relay.STATE["requests"][-1]
        check("打到 /gw/chat", last["path"] == "/gw/chat", last["path"])
        check("用了自定义请求头而不是 Authorization",
              last["gw"] == "gw-secret-abcdef123456" and last["auth"] is None)
        check("extra_body 透传", last["body"].get("tenant") == "h3yun")
        check("extra_headers 透传", last["tenant"] == "h3yun", str(last.get("tenant")))
        check("temperature/max_tokens 生效",
              last["body"]["temperature"] == 0.2 and last["body"]["max_tokens"] == 1200)
        check("json_mode=False 不发 response_format",
              "response_format" not in last["body"])
        check("describe 里密钥已掩码",
              "…" in r["request"]["headers"].get("X-Gw-Token", ""),
              str(r["request"]["headers"]))

        print("\n[2] 模型列表走自定义路径")
        names = m.available_models()
        check("拉到 3 个模型", len(names) == 3, str(names))

        print("\n[3] 整条流水线经中转站产出可应用补丁")
        repo = make_repo()
        pipe = AIDefectFixerPipeline(pipe_config(repo, ai_config(base)))
        res = pipe.run(mock=True, defect_id=TEST_DEFECT["id"], limit=1, skip_verify=True)[0]
        a, p = res["analysis"], res["proposal"]
        check("ai_mode=custom", a["ai_mode"] == "custom", a["ai_mode"])
        check("根因来自中转站", "template" in a["root_cause"], a["root_cause"][:60])
        check("分类来自中转站", a["category"] == "数据", a["category"])
        check("补丁 ok", p["patch"]["ok"], str(p["patch"]["errors"])[:160])
        diff = p["patch"]["combined_diff"]
        check("diff 是单行最小改动", diff.count("+  const name") == 1
              and "?.name" in diff, diff.replace("\n", " | ")[:160])
        check("提案 pending", p["status"] == "pending", p["status"])
        ap = pipe.approve(p["id"])
        check("采纳成功", ap["ok"], str(ap)[:160])
        body = (repo / "src" / "ProcessList.tsx").read_text(encoding="utf-8")
        check("真实文件被正确改写", "props.items.template?.name" in body
              and "return <div>" in body, repr(body))

        print("\n[4] 故障可见性（中转站最常见的坑）")
        for mode, expect in (
            ("err401", "HTTP 401"),
            ("err500", "HTTP 500"),
            ("html", "不是 JSON"),
            ("notjson", "不是 JSON"),
            ("wrongshape", "缺少字段"),
        ):
            fake_relay.STATE["mode"] = mode
            out = AIModel(ai_config(base)).complete("x")
            err = out.get("error", "")
            detail = out.get("error_detail", {})
            check(f"{mode} -> 报出 {expect}", expect in err or expect in str(detail),
                  err[:90])
            check(f"{mode} -> 带 url 与状态码", bool(detail.get("url")),
                  str(detail.get("status")))
            check(f"{mode} -> 不编造补丁", out.get("patch_blocks", "") == "")
        fake_relay.STATE["mode"] = "fenced"
        out = AIModel(ai_config(base)).complete("x")
        check("```json 包裹的回复也能解析", out.get("category") == "数据", str(out)[:80])

        print("\n[5] 配置校验（不发请求就能发现错误）")
        bad = AIConfig.from_dict({"provider": "custom", "api_key": "k", "model": "m"})
        check("custom 缺 base_url 被拦", any("Base URL" in x for x in bad.validate()),
              str(bad.validate()))
        nokey = AIConfig.from_dict({"provider": "openai", "model": "m"})
        check("缺 key 自动落 mock", nokey.mode == "mock")
        check("URL 非法被拦",
              any("URL" in x for x in AIConfig.from_dict(
                  {"provider": "custom", "base_url": "ftp://x", "api_key": "k",
                   "model": "m"}).validate()))
        check("未知 provider 字段被容忍",
              AIConfig.from_dict({"provider": "openai", "api_key": "k", "model": "m",
                                  "brand_new_key": 1}).mode == "openai")
        print("\n[6] 请求体积：未知字段不炸")
        try:
            AIModel({"provider": "openai", "api_key": "k", "model": "m",
                     "future_toggle": True}).cfg
            check("from_dict 忽略未知键", True)
        except Exception as e:
            check("from_dict 忽略未知键", False, str(e))
    finally:
        import shutil

        srv.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)
        print("\nrelay stopped, temp repo removed")
    print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
