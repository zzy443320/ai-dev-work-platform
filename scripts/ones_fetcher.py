"""Stage 1: pull work items from a privately deployed ONES (v6.x).

Verified against a privately deployed ONES v6.18.2 instance:
- REST OpenAPI paths (`/project/api/v3/issues/search`) do NOT exist there.
- The working channel is the team-scoped GraphQL endpoint:
    POST {base}/project/api/project/team/{teamUUID}/items/graphql
  with query `{tasks{...}}` (returns the whole team's items; filters are
  applied client-side because the server accepts no args on `tasks`).
- Single-item get `task(key:"{team}-{number}")` exists but returns
  AccessDenied even for the user's own items → detail = list filtered.
- Auth: Ones-Auth-Token header (+ Referer). email/password login via
  /project/api/project/auth/login caches user.token+uuid and auto-relogs.

Two auth modes:
1. email + password  -> auto login / auto refresh (preferred).
2. token only        -> pasted from the browser; expiry is reported clearly.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

# 实测可用的字段（2026-09 校准）：name/description/createTime/number/deadline
# 标量；status/assign/owner/project/issueType 需要子选择。
_GQL_TASKS = (
    "query{tasks{uuid number name description createTime deadline "
    "status{name} assign{uuid name} owner{uuid name} "
    "project{uuid name} issueType{uuid name}}}"
)


class OnesAuthError(Exception):
    """Raised when credentials are missing/invalid and auto-login cannot fix it."""


class OnesClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        project_uuid: str = "",
        team_uuid: str = "",
        email: str = "",
        password: str = "",
        timeout: int = 30,
        attachment_apis: Optional[List[str]] = None,
        attachment_downloads: Optional[List[str]] = None,
        testcase_apis: Optional[List[str]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token or ""
        self.project_uuid = project_uuid or ""
        self.team_uuid = team_uuid or ""
        self.email = email or ""
        self.password = password or ""
        self.user_uuid = ""
        self.timeout = timeout
        self.last_filter: Dict = {}
        # 附件接口的候选模板（工单截图入口）。ONES 各部署的附件 API 形状未逐一
        # 证实，这里按已验证通道的路径风格给出候选，允许配置整体替换：
        # ones.attachment_apis / ones.attachment_downloads（分号分隔，可用
        # {base} {team} {uuid} {att} 占位）。全部候选都失败只意味着「没有截图」，
        # 绝不影响工单拉取主流程。
        self.attachment_apis = list(attachment_apis or []) or None
        self.attachment_downloads = list(attachment_downloads or []) or None
        self.last_attachment_note: str = ""
        # 工单「测试情况」tab 关联的测试用例接口（自测模块用）。与附件同一套
        # best-effort 口径：各部署形状未证实，候选逐个试，失败只记 note。
        self.testcase_apis = list(testcase_apis or []) or None
        self.last_testcase_note: str = ""
        self.session = requests.Session()
        self._apply_headers()

    # ------------------------------------------------------------------ auth
    def _apply_headers(self) -> None:
        h = {
            "Content-Type": "application/json",
            "Referer": self.base_url + "/",
            "User-Agent": "ones-defect-auto-fixer/0.2",
        }
        if self.token:
            h["Ones-Auth-Token"] = self.token
        if self.user_uuid:
            h["Ones-User-Id"] = self.user_uuid
        self.session.headers.update(h)

    def _ensure_auth(self) -> None:
        if self.token:
            return
        if self.email and self.password:
            self.login()
        # token-only mode with no token: the request will fail and the server
        # surfaces a clear error.

    def login(self) -> Dict:
        """Email+password login; caches user.uuid / user.token on success."""
        if not (self.email and self.password):
            raise OnesAuthError(
                "ONES 凭据不可用：token 缺失/失效，且未配置登录邮箱和密码。"
                "请在设置里填写 ONES 账号密码，或重新复制有效 token"
            )
        r = self.session.post(
            f"{self.base_url}/project/api/project/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=self.timeout,
        )
        if r.status_code in (401, 403):
            raise OnesAuthError(
                f"ONES 登录失败（HTTP {r.status_code}）：邮箱或密码不正确"
            )
        r.raise_for_status()
        user = (r.json() or {}).get("user") or {}
        self.user_uuid = user.get("uuid", "") or self.user_uuid
        self.token = user.get("token", "") or self.token
        self._apply_headers()
        return user

    def me(self) -> Dict:
        """Validate the current credentials. Auto-relogs when possible."""
        self._ensure_auth()
        r = self.session.get(
            f"{self.base_url}/project/api/project/users/me", timeout=self.timeout
        )
        if r.status_code in (401, 403):
            if self.email and self.password:
                return self.login()
            try:
                err = (r.json() or {}).get("errcode", "")
            except ValueError:
                err = ""
            raise OnesAuthError(
                f"ONES token 无效或已过期（{err or f'HTTP {r.status_code}'}）。"
                "请刷新 ONES 页面后重新复制 token，或在设置里改用账号密码登录"
            )
        r.raise_for_status()
        try:
            data = r.json() or {}
        except ValueError:
            return {}
        user = data.get("user") or data or {}
        if isinstance(user, dict) and (user.get("uuid") or user.get("id")):
            self.user_uuid = user.get("uuid") or user.get("id") or self.user_uuid
            self._apply_headers()
        return user

    def my_uuid(self) -> str:
        """UUID of the logged-in user (cached)."""
        if self.user_uuid:
            return self.user_uuid
        user = self.me() or {}
        uid = (user.get("uuid") or user.get("id") or "") if isinstance(user, dict) else ""
        if not uid:
            raise OnesAuthError(
                "拿不到当前用户的 UUID，无法按「负责人是我」过滤。"
                "请在设置里填写 ONES 登录邮箱和密码（会自动登录并获取用户身份），"
                "或确认 token 是有效的登录态"
            )
        self.user_uuid = uid
        self._apply_headers()
        return uid

    def _team(self) -> str:
        if self.team_uuid:
            return self.team_uuid
        raise OnesAuthError(
            "未配置 Team UUID：浏览器打开 ONES 后地址栏 /team/XXXX/ 里那一段"
        )

    # ----------------------------------------------------------------- fetch
    def _graphql_tasks(self) -> List[Dict]:
        """Call the verified team GraphQL endpoint and return raw task dicts."""
        self._ensure_auth()
        team = self._team()
        url = f"{self.base_url}/project/api/project/team/{team}/items/graphql"
        # 全团队列表实测 ~6-15s / ~15MB，给足超时
        timeout = max(int(self.timeout), 180)

        def _call():
            return self.session.post(url, json={"query": _GQL_TASKS}, timeout=timeout)

        r = _call()
        if r.status_code in (401, 403) and self.email and self.password:
            self.login()  # token 失效 → 自动重登后重试一次
            r = _call()
        if not r.ok:
            raise RuntimeError(
                f"ONES GraphQL 拉取失败（HTTP {r.status_code}）: {r.text[:200]}"
            )
        try:
            data = r.json() or {}
        except ValueError:
            raise RuntimeError(f"ONES GraphQL 返回非 JSON: {r.text[:200]}")
        errs = data.get("errors") or []
        raw = ((data.get("data") or {}).get("tasks")) or []
        if not raw and errs:
            raise RuntimeError(f"ONES GraphQL 错误: {str(errs[0])[:200]}")
        return raw

    @staticmethod
    def _normalize(t: Dict) -> Dict:
        created = t.get("createTime")
        # ONES createTime 是微秒时间戳
        if isinstance(created, (int, float)) and created > 10**14:
            created = created / 10**6
        st = t.get("status") if isinstance(t.get("status"), dict) else {}
        itype = t.get("issueType") if isinstance(t.get("issueType"), dict) else {}
        proj = t.get("project") if isinstance(t.get("project"), dict) else {}
        return {
            "id": t.get("uuid") or "",
            "number": t.get("number"),
            "title": t.get("name") or "",
            "description": t.get("description") or "",
            "priority": "",
            "status": st.get("name", "") if isinstance(st, dict) else "",
            "issue_type": itype.get("name", "") if isinstance(itype, dict) else "",
            "project": {
                "uuid": proj.get("uuid", "") if isinstance(proj, dict) else "",
                "name": proj.get("name", "") if isinstance(proj, dict) else "",
            },
            "assignee": OnesClient._person(t.get("assign")),
            "owner": OnesClient._person(t.get("owner")),
            "created_at": created,
            "raw": t,
        }

    @staticmethod
    def _person(v) -> Dict:
        if isinstance(v, dict):
            return {"uuid": v.get("uuid") or "", "name": v.get("name") or ""}
        if isinstance(v, str) and v:
            return {"uuid": v, "name": ""}
        return {}

    def fetch_defects(
        self,
        status: str = "",
        days: int = 0,
        limit: int = 20,
        priority: Optional[str] = None,
        mine_only: bool = False,
        defect_id: Optional[str] = None,
    ) -> List[Dict]:
        """Fetch work items via the verified GraphQL channel.

        Server-side filtering is not supported on this deployment (the
        `tasks` query takes no args), so filtering happens client-side:
        project → assignee(mine_only) → defect_id → days → sort desc → limit.
        `self.last_filter` records the counts for the UI notes.
        """
        raw = self._graphql_tasks()
        items = [self._normalize(t) for t in raw if isinstance(t, dict)]

        my_uuid = self.my_uuid() if mine_only else ""
        lf = {
            "total": len(items),
            "mine_only": bool(mine_only),
            "my_uuid": my_uuid,
            "dropped_project": 0,
            "dropped": 0,
            "dropped_time": 0,
        }

        if defect_id:
            key = str(defect_id).strip().lower()
            items = [
                it for it in items
                if key in (
                    str(it.get("id") or "").lower(),
                    str(it.get("number") or ""),
                    f"{self.team_uuid}-{it.get('number')}".lower(),
                )
                or key in str(it.get("title") or "").lower()
            ]
            lf["total"] = len(items)

        if self.project_uuid:
            kept = []
            for it in items:
                pu = (it.get("project") or {}).get("uuid") or ""
                if pu and pu != self.project_uuid:
                    lf["dropped_project"] += 1
                    continue
                kept.append(it)
            items = kept

        if mine_only:
            kept = []
            for it in items:
                au = (it.get("assignee") or {}).get("uuid") or ""
                if au != my_uuid:
                    lf["dropped"] += 1
                    continue
                kept.append(it)
            items = kept

        if days and days > 0:
            cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
            kept = []
            for it in items:
                ca = it.get("created_at") or 0
                if ca and ca >= cutoff:
                    kept.append(it)
                else:
                    lf["dropped_time"] += 1
            items = kept

        items.sort(key=lambda it: it.get("created_at") or 0, reverse=True)
        self.last_filter = lf
        return items[: max(int(limit), 1)]

    def fetch_defect_detail(self, issue_uuid: str) -> Dict:
        """Single item = full list filtered (this deployment has no usable
        single-item API: `task(key:...)` answers AccessDenied)."""
        want = str(issue_uuid).strip()
        items = self.fetch_defects(limit=1, defect_id=want)
        if items:
            return items[0]
        raise RuntimeError(
            f"在当前项目/团队的工单列表里没有找到 {want}。"
            "注意：界面上的数字编号要完整（如 100003），工单 UUID 也可以"
        )

    def add_comment(self, issue_uuid: str, content: str) -> Dict:
        self._ensure_auth()
        team = self._team()
        r = self.session.post(
            f"{self.base_url}/project/api/project/team/{team}"
            f"/task/{issue_uuid}/comments",
            json={"content": content}, timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json().get("data") or {}

    # ------------------------------------------------------------- 工单附件
    DEFAULT_ATTACHMENT_APIS = (
        "{base}/project/api/project/team/{team}/task/{uuid}/attachments",
        "{base}/project/api/project/task/{uuid}/attachments",
    )
    DEFAULT_ATTACHMENT_DOWNLOADS = (
        "{base}/project/api/project/attachment/download/{att}",
        "{base}/project/api/project/attachment/{att}/download",
    )

    def fetch_attachments(self, issue_uuid: str) -> List[Dict]:
        """尽力拉一条工单的附件图片（汇报人拍的现场截图）。

        **best-effort，三条红线**：① 候选通道逐个试，任何失败都吞掉并记进
        `last_attachment_note`，绝不抛异常打断修复主流程——附件拉不到只意味着
        「这次没有截图可看」，不是流水线故障；② 只收图片附件，其它类型直接跳过；
        ③ 返回项带原始字节，落盘与转多模态载荷是调用方（pipeline）的事。
        接口形状与部署相关：配置 `ones.attachment_apis` / `ones.attachment_downloads`
        （分号分隔的 URL 模板，占位 {base} {team} {uuid} {att}）可整体替换候选。
        """
        self.last_attachment_note = ""
        try:
            self._ensure_auth()
        except Exception as e:
            self.last_attachment_note = f"登录态不可用，跳过附件：{e}"
            return []
        if not self.base_url:
            return []
        base = self.base_url.rstrip("/")
        team = self.team_uuid or ""

        def fill(tpl: str, uuid: str) -> str:
            return (str(tpl).replace("{base}", base).replace("{team}", team)
                    .replace("{uuid}", str(uuid)).replace("{att}", str(uuid)))

        items: List[Dict] = []
        for tpl in (self.attachment_apis or self.DEFAULT_ATTACHMENT_APIS):
            url = fill(tpl, issue_uuid)
            try:
                r = self.session.get(url, timeout=self.timeout)
            except Exception as e:
                self.last_attachment_note = f"附件列表请求失败：{type(e).__name__}"
                continue
            if not r.ok:
                self.last_attachment_note = f"附件列表 HTTP {r.status_code}"
                continue
            items = self._parse_attachment_list(r)
            if items:
                break
        if not items:
            if not self.last_attachment_note:
                self.last_attachment_note = "各候选接口都没有返回可解析的附件列表"
            return []

        from .chat_attachments import classify

        out: List[Dict] = []
        for it in items[:8]:
            att = str(it.get("uuid") or "")
            name = str(it.get("name") or f"attachment-{att[:8]}")
            if not att:
                continue
            for dtpl in (self.attachment_downloads or self.DEFAULT_ATTACHMENT_DOWNLOADS):
                url = fill(dtpl, att)
                try:
                    r = self.session.get(url, timeout=max(int(self.timeout), 60))
                except Exception:
                    continue
                if not r.ok or not r.content:
                    continue
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if classify(ctype, name) != "image":
                    break        # 是附件但不是图片：不再换下载地址，直接跳过
                out.append({"uuid": att, "name": name, "content_type": ctype,
                            "data": r.content})
                break
        self.last_attachment_note = (f"取到 {len(out)} 张图片附件"
                                     if out else
                                     f"附件列表取到 {len(items)} 项，但没有可下载的图片")
        return out

    # ------------------------------------------------------------- 测试用例
    DEFAULT_TESTCASE_APIS = (
        "{base}/project/api/project/team/{team}/task/{uuid}/testcases",
        "{base}/project/api/project/team/{team}/task/{uuid}/test_case",
        "{base}/project/api/project/team/{team}/task/{uuid}/test-cases",
        "{base}/project/api/project/team/{team}/testcase/task/{uuid}",
        "{base}/project/api/project/task/{uuid}/testcases",
    )

    def fetch_test_cases(self, issue_uuid: str) -> List[Dict]:
        """尽力拉一条工单「测试情况」tab 里关联的测试用例。

        与 ``fetch_attachments`` 同一套红线：候选通道逐个试，任何失败都吞掉并写进
        ``last_testcase_note``，绝不抛异常打断自测主流程 —— 拉不到只意味着
        「这次没有对应用例」，用户可以手贴用例继续。接口形状与 ONES 部署有关：
        配置 ``ones.testcase_apis``（分号分隔的 URL 模板，占位 {base} {team} {uuid}）
        可整体替换候选。
        """
        self.last_testcase_note = ""
        try:
            self._ensure_auth()
        except Exception as e:                               # noqa: BLE001
            self.last_testcase_note = f"登录态不可用，跳过测试用例：{e}"
            return []
        if not self.base_url:
            return []
        base = self.base_url.rstrip("/")
        team = self.team_uuid or ""

        def fill(tpl: str) -> str:
            return (str(tpl).replace("{base}", base).replace("{team}", team)
                    .replace("{uuid}", str(issue_uuid)))

        for tpl in (self.testcase_apis or self.DEFAULT_TESTCASE_APIS):
            url = fill(tpl)
            try:
                r = self.session.get(url, timeout=self.timeout)
            except Exception as e:                           # noqa: BLE001
                self.last_testcase_note = f"测试用例请求失败：{type(e).__name__}"
                continue
            if not r.ok:
                self.last_testcase_note = f"测试用例接口 HTTP {r.status_code}（{url}）"
                continue
            items = self._parse_testcase_list(r)
            if items:
                self.last_testcase_note = f"从测试情况 tab 取到 {len(items)} 条用例"
                return items
            self.last_testcase_note = f"接口有响应但没解析出用例（{url}）"
        if not self.last_testcase_note:
            self.last_testcase_note = "各候选接口都没有返回可解析的测试用例"
        return []

    @staticmethod
    def _parse_testcase_list(resp) -> List[Dict]:
        """把测试用例响应洗成 [{name/steps/expected}]。各部署形状不一，逐层试探。

        递归找「最长的、元素是 dict 且带名称字段的列表」——这比硬编码
        ``data.testcases`` 这种路径稳：实测同一个产品的不同接口层级都不一样。
        """
        try:
            data = resp.json()
        except Exception:                                    # noqa: BLE001
            return []

        def looks_like_case(d) -> bool:
            return isinstance(d, dict) and any(
                str(d.get(k) or "").strip()
                for k in ("name", "title", "case_name", "summary"))

        best: List[Dict] = []

        def walk(node, depth: int = 0) -> None:
            nonlocal best
            if depth > 6:
                return
            if isinstance(node, list):
                cases = [x for x in node if looks_like_case(x)]
                if len(cases) > len(best):
                    best = cases
                for x in node[:40]:
                    walk(x, depth + 1)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v, depth + 1)

        walk(data)
        return best[:40]

    @staticmethod
    def _parse_attachment_list(resp) -> List[Dict]:
        """把附件列表响应洗成 [{uuid,name}]。各部署返回形状不一，逐层试探。"""
        try:
            data = resp.json()
        except Exception:
            return []
        candidates = []
        if isinstance(data, dict):
            inner = data.get("data")
            candidates.append(inner)
            if isinstance(inner, dict):
                candidates.extend([inner.get("attachments"), inner.get("list"),
                                   inner.get("items")])
        elif isinstance(data, list):
            candidates.append(data)
        for cand in candidates:
            if isinstance(cand, list) and cand and isinstance(cand[0], dict):
                out = []
                for it in cand:
                    if not isinstance(it, dict):
                        continue
                    att = str(it.get("uuid") or it.get("id") or "")
                    if not att:
                        continue
                    out.append({"uuid": att,
                                "name": str(it.get("name") or it.get("fileName")
                                            or it.get("title") or "")})
                if out:
                    return out
        return []
