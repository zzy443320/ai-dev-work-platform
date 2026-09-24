"""页面验证登录（verifier auth）与 Figma 双通道的行为校验。

不联网、不开浏览器：只验证「凭据是否会被认领、选择器是否被正确配对、
设置读写是否正确回显」，这些是最容易静默写错的部分。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verifier import ScreenshotVerifier  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  [ok]   " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def v(auth):
    return ScreenshotVerifier("http://localhost:9100", screenshot_dir="./screenshots", auth=auth)


print("== 1. 工单复现步骤里带登录 → 认领并配对选择器 ==")
auth = {"email": "qa@corp.com", "password": "s3cret"}
actions = [
    ["goto", "/login", None],
    ["fill", "input[name=username]", "whoever"],
    ["fill", "input[type=password]", "whatever"],
    ["click", "button[type=submit]", None],
    ["goto", "/agent-workspace/schedule", None],
]
creds = v(auth)._login_creds(actions)
check("识别出登录动作", creds == ("/login", "input[name=username]", "input[type=password]"),
      str(creds))

print("== 2. 普通表单（填数据后保存）不得被误判成登录 ==")
creds = v(auth)._login_creds([
    ["goto", "/schedule", None],
    ["fill", "input[name=title]", "每周例会"],
    ["fill", "input[name=repeat]", "2"],
    ["click", "button.save", None],
])
check("非登录表单不触发登录", creds is None, str(creds))

print("== 3. 有登录 URL 但只有单个密码框 → 补齐默认选择器 ==")
creds = v(auth)._login_creds([
    ["goto", "/signin", None],
    ["fill", "#pwd", "x"],
    ["click", "button.login-btn", None],
])
check("两段式登录页可兜底", creds == ("/signin", "#username", "#password"), str(creds))

print("== 4. 未配凭据 / 未启用 → 一律不登录 ==")
check("空 auth 不认领", v({})._login_creds(actions) is None)
check("只有账号没有密码不认领",
      v({"email": "a@b.c"})._login_creds(actions) is None)

print("== 5. 选择器别名（中文 / 驼峰）也能配对 ==")
creds = v(auth)._login_creds([
    ["goto", "/login", None],
    ["fill", "input.loginName", "x"],
    ["fill", "input.pwdInput", "y"],
])
check("loginName/pwdInput 配对", creds == ("/login", "input.loginName", "input.pwdInput"),
      str(creds))

print("== 6. 登录 URL 正则不误伤普通路由 ==")
sp = v(auth)
check("普通路由不算登录页", not sp._is_login_url("/agent-workspace/schedule"))
check("带后缀也识别", sp._is_login_url("/login.html") and sp._is_login_url("/auth/callback"))

print("== 7. 相对路由拼接 ==")
check("裸路径补斜杠", sp._abs("schedule/list") == "http://localhost:9100/schedule/list",
      sp._abs("schedule/list"))
check("绝对路径直用", sp._abs("/login") == "http://localhost:9100/login")
check("完整 URL 不改", sp._abs("http://x.com/login") == "http://x.com/login")

print("== 8. web 层：_page_auth 只在启用且填全时才给凭据 ==")
from web import server  # noqa: E402

s = server._deep_defaults()
a = server._page_auth(s)
check("默认不带凭据（只有 storage_state）", "password" not in a and "storage_state" in a, str(a))
s["pagelogin"].update({"enabled": True, "email": "u", "password": "p"})
a = server._page_auth(s)
check("启用后注入 email/password", a.get("email") == "u" and a.get("password") == "p", str(a))
s["pagelogin"]["enabled"] = False
check("关闭开关即撤回凭据", "password" not in server._page_auth(s))
s["pagelogin"]["enabled"] = True
s["pagelogin"]["password"] = ""
check("密码为空不算启用", "password" not in server._page_auth(s))
check("storage_state 落在 screenshots 目录",
      Path(server._page_auth(s)["storage_state"]).parent == server.SCREENSHOT_DIR)

print("== 9. 配置面板读写：密码只进不回 ==")
s = server._deep_defaults()
s["pagelogin"].update({"enabled": True, "email": "u@corp.com", "password": "topsecret"})
pub = server._settings_public(s)
check("公开结构里没有明文密码", "topsecret" not in json.dumps(pub, ensure_ascii=False))
check("给出 has_page_password 标记", pub.get("has_page_password") is True)
check("密码字段被替换为掩码", "password_masked" in pub.get("pagelogin", {}))
check("pipeline_config 带上 auth",
      server._pipeline_config(s)["playwright"]["auth"].get("email") == "u@corp.com")

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + "; ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
