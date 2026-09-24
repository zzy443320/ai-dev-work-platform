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
from datetime import datetime, timedelta, timezone
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
