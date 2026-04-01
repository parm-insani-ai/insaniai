"""
Oracle Primavera P6 Connector — Fetches schedule data via P6 REST API.

Primavera P6 EPPM exposes a REST API for accessing project schedules,
activities, resources, and relationships.

Data extracted:
- Projects (name, dates, status, completion)
- Activities (tasks with dates, durations, status, constraints)

Authentication: Supports OAuth (Oracle Identity Cloud) or Basic Auth
for on-premise P6 installations.
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

P6_BASE_URL = os.getenv("PRIMAVERA_BASE_URL", "")
P6_CLIENT_ID = os.getenv("PRIMAVERA_CLIENT_ID", "")
P6_CLIENT_SECRET = os.getenv("PRIMAVERA_CLIENT_SECRET", "")
P6_REDIRECT_URI = os.getenv("PRIMAVERA_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/primavera")
ORACLE_AUTH_URL = os.getenv("PRIMAVERA_AUTH_URL", "")
ORACLE_TOKEN_URL = os.getenv("PRIMAVERA_TOKEN_URL", "")
P6_USERNAME = os.getenv("PRIMAVERA_USERNAME", "")
P6_PASSWORD = os.getenv("PRIMAVERA_PASSWORD", "")


class PrimaveraConnector(BaseConnector):
    PROVIDER = "primavera"
    DISPLAY_NAME = "Oracle Primavera P6"
    DESCRIPTION = "Sync project schedules, activities, and milestones"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=P6_CLIENT_ID,
            client_secret=P6_CLIENT_SECRET,
            auth_url=ORACLE_AUTH_URL,
            token_url=ORACLE_TOKEN_URL,
            scopes=["openid"],
            redirect_uri=P6_REDIRECT_URI,
        )

    def get_auth_url(self, state: str) -> str:
        config = self.get_oauth_config()
        if not config.client_id or not config.auth_url:
            return ""
        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(config.scopes),
            "state": state,
        }
        return f"{config.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(config.token_url, data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": config.redirect_uri,
            })
            resp.raise_for_status()
            return resp.json()

    async def refresh_tokens(self, refresh_token: str) -> dict:
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(config.token_url, data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            })
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self, access_token: str) -> bool:
        if not P6_BASE_URL:
            logger.warning("primavera_no_base_url")
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{P6_BASE_URL}/project",
                    headers=self._headers(access_token),
                    params={"Fields": "ObjectId,Name", "PageSize": 1},
                )
                return resp.status_code == 200
        except Exception as e:
            logger.warning("primavera_test_failed", error=str(e))
            return False

    async def get_account_info(self, access_token: str) -> dict:
        return {"email": P6_USERNAME or "p6-user", "name": "Primavera P6"}

    async def fetch_data(
        self,
        access_token: str,
        since: datetime = None,
        cursor: str = "",
        connection_config: dict = None,
    ) -> tuple[list[NormalizedItem], str]:
        if not P6_BASE_URL:
            logger.error("primavera_no_base_url_configured")
            return [], ""

        headers = self._headers(access_token)
        items = []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                projects = await self._fetch_projects(client, headers)
                logger.info("primavera_projects", count=len(projects))

                for proj in projects[:20]:
                    project_id = proj.get("ObjectId", "")
                    project_name = proj.get("Name", "")

                    # Add project as an item
                    pct = proj.get("PercentComplete", 0) or 0
                    items.append(NormalizedItem(
                        external_id=f"p6-proj-{project_id}",
                        item_type="schedule_project",
                        title=project_name,
                        summary=f"Project: {project_name} | Status: {proj.get('Status', '')} | {pct}% complete | Start: {str(proj.get('StartDate', ''))[:10]} | Finish: {str(proj.get('FinishDate', ''))[:10]}",
                        raw_data=proj,
                        metadata={
                            "status": proj.get("Status", ""),
                            "percent_complete": pct,
                            "start": str(proj.get("StartDate", ""))[:10],
                            "finish": str(proj.get("FinishDate", ""))[:10],
                        },
                        source_url="",
                        item_date=self._parse_date(proj.get("StartDate")),
                        project_hint=project_name,
                    ))

                    activities = await self._fetch_activities(client, headers, project_id, project_name)
                    items.extend(activities)

        except Exception as e:
            logger.error("primavera_fetch_error", error=str(e))
            raise

        logger.info("primavera_fetched", count=len(items))
        return items, ""

    async def _fetch_projects(self, client, headers) -> list:
        try:
            resp = await client.get(
                f"{P6_BASE_URL}/project",
                headers=headers,
                params={
                    "Fields": "ObjectId,Name,Status,StartDate,FinishDate,DataDate,PercentComplete",
                    "PageSize": 100,
                },
            )
            if resp.status_code != 200:
                logger.warning("primavera_projects_status", status=resp.status_code)
                return []
            return resp.json()
        except Exception as e:
            logger.warning("primavera_projects_error", error=str(e))
            return []

    async def _fetch_activities(self, client, headers, project_id, project_name) -> list:
        items = []
        try:
            resp = await client.get(
                f"{P6_BASE_URL}/activity",
                headers=headers,
                params={
                    "Fields": "ObjectId,Name,ActivityId,Status,StartDate,FinishDate,ActualStartDate,ActualFinishDate,RemainingDuration,PercentComplete,ActivityType,PrimaryConstraintType",
                    "Filter": f"ProjectObjectId = {project_id}",
                    "PageSize": 200,
                },
            )
            if resp.status_code != 200:
                return items

            for act in resp.json():
                activity_id = act.get("ActivityId", "")
                name = act.get("Name", "")
                status = act.get("Status", "")
                pct = act.get("PercentComplete", 0) or 0
                start = act.get("StartDate", "")
                finish = act.get("FinishDate", "")
                remaining = act.get("RemainingDuration", "")

                summary = f"Activity {activity_id}: {name} | Status: {status} | {pct}% complete"
                if start:
                    summary += f" | Start: {str(start)[:10]}"
                if finish:
                    summary += f" | Finish: {str(finish)[:10]}"
                if remaining:
                    summary += f" | Remaining: {remaining}d"

                items.append(NormalizedItem(
                    external_id=f"p6-act-{act.get('ObjectId', '')}",
                    item_type="schedule_activity",
                    title=f"{activity_id} - {name}",
                    summary=summary,
                    raw_data=act,
                    metadata={
                        "activity_id": activity_id,
                        "status": status,
                        "percent_complete": pct,
                        "start": str(start)[:10] if start else "",
                        "finish": str(finish)[:10] if finish else "",
                        "remaining_duration": remaining,
                        "project": project_name,
                    },
                    source_url="",
                    item_date=self._parse_date(start),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("primavera_activities_error", project_id=project_id, error=str(e))
        return items

    def _headers(self, access_token: str) -> dict:
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        elif P6_USERNAME and P6_PASSWORD:
            import base64
            creds = base64.b64encode(f"{P6_USERNAME}:{P6_PASSWORD}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        return headers

    def _parse_date(self, date_str) -> datetime | None:
        if not date_str:
            return None
        try:
            if "T" in str(date_str):
                return datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        except Exception:
            return None
