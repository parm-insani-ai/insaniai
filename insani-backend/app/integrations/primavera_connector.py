"""
Primavera P6 Connector — Fetches project schedules via Oracle Primavera P6 API.

Primavera P6 can be deployed as:
- P6 EPPM (cloud/on-premise with REST API)
- P6 Professional (desktop, no REST API)

This connector targets P6 EPPM's REST API.
The base URL varies per installation — configured via env var.

Data extracted:
- Projects (name, dates, status, budget)
- Activities (tasks with dates, durations, progress)
- Resources (labor, equipment assignments)
- WBS (work breakdown structure)

P6 API docs: https://docs.oracle.com/cd/F25600_01/English/Integration_API/
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

# P6 EPPM uses basic auth or OAuth depending on setup
# Most on-premise installations use basic auth with a REST API
P6_BASE_URL = os.getenv("PRIMAVERA_BASE_URL", "https://p6.example.com/p6ws/restapi")
P6_USERNAME = os.getenv("PRIMAVERA_USERNAME", "")
P6_PASSWORD = os.getenv("PRIMAVERA_PASSWORD", "")

# For cloud-hosted Oracle P6, OAuth is used
P6_AUTH_URL = os.getenv("PRIMAVERA_AUTH_URL", "https://login.oracle.com/oauth2/v1/authorize")
P6_TOKEN_URL = os.getenv("PRIMAVERA_TOKEN_URL", "https://login.oracle.com/oauth2/v1/token")
P6_CLIENT_ID = os.getenv("PRIMAVERA_CLIENT_ID", "")
P6_CLIENT_SECRET = os.getenv("PRIMAVERA_CLIENT_SECRET", "")
P6_REDIRECT_URI = os.getenv("PRIMAVERA_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/primavera")


class PrimaveraConnector(BaseConnector):
    PROVIDER = "primavera"
    DISPLAY_NAME = "Oracle Primavera P6"
    DESCRIPTION = "Sync project schedules, activities, resources, and milestones"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=P6_CLIENT_ID,
            client_secret=P6_CLIENT_SECRET,
            auth_url=P6_AUTH_URL,
            token_url=P6_TOKEN_URL,
            scopes=[],
            redirect_uri=P6_REDIRECT_URI,
        )

    def get_auth_url(self, state: str) -> str:
        config = self.get_oauth_config()
        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
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
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{P6_BASE_URL}/project",
                    headers=self._headers(access_token),
                    params={"Fields": "ObjectId,Name", "$top": 1},
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        return {"name": "Primavera P6", "email": ""}

    async def fetch_data(
        self,
        access_token: str,
        since: datetime = None,
        cursor: str = "",
        connection_config: dict = None,
    ) -> tuple[list[NormalizedItem], str]:
        headers = self._headers(access_token)
        items = []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                projects = await self._fetch_projects(client, headers)
                items.extend(projects)

                for proj_item in projects[:10]:
                    proj_id = proj_item.raw_data.get("ObjectId", "")
                    proj_name = proj_item.title

                    activities = await self._fetch_activities(client, headers, proj_id, proj_name)
                    items.extend(activities)

        except Exception as e:
            logger.error("primavera_fetch_error", error=str(e))
            raise

        logger.info("primavera_fetched", count=len(items))
        return items, ""

    async def _fetch_projects(self, client, headers) -> list:
        items = []
        try:
            resp = await client.get(
                f"{P6_BASE_URL}/project",
                headers=headers,
                params={
                    "Fields": "ObjectId,Name,Status,StartDate,FinishDate,PlannedBudget,ActualCost,PercentComplete",
                    "$top": 20,
                },
            )
            resp.raise_for_status()

            for proj in resp.json():
                name = proj.get("Name", "")
                status = proj.get("Status", "")
                start = proj.get("StartDate", "")
                finish = proj.get("FinishDate", "")
                budget = proj.get("PlannedBudget", 0)
                actual = proj.get("ActualCost", 0)
                pct = proj.get("PercentComplete", 0)

                summary = f"Project: {name} | Status: {status} | {pct}% complete"
                if budget:
                    summary += f" | Budget: ${budget:,.0f} | Actual: ${actual:,.0f}"
                if start and finish:
                    summary += f" | {start[:10]} to {finish[:10]}"

                items.append(NormalizedItem(
                    external_id=f"p6proj-{proj.get('ObjectId', '')}",
                    item_type="schedule_project",
                    title=name,
                    summary=summary,
                    raw_data=proj,
                    metadata={
                        "status": status,
                        "start": start,
                        "finish": finish,
                        "budget": budget,
                        "actual_cost": actual,
                        "percent_complete": pct,
                    },
                    source_url="",
                    item_date=self._parse_date(start),
                    project_hint=name,
                ))
        except Exception as e:
            logger.warning("primavera_projects_error", error=str(e))
        return items

    async def _fetch_activities(self, client, headers, project_id, project_name) -> list:
        items = []
        try:
            resp = await client.get(
                f"{P6_BASE_URL}/activity",
                headers=headers,
                params={
                    "Fields": "ObjectId,Name,Status,StartDate,FinishDate,RemainingDuration,ActualDuration,PercentComplete,ActivityType",
                    "Filter": f"ProjectObjectId = {project_id}",
                    "$top": 50,
                },
            )
            resp.raise_for_status()

            for act in resp.json():
                name = act.get("Name", "")
                status = act.get("Status", "")
                start = act.get("StartDate", "")
                finish = act.get("FinishDate", "")
                remaining = act.get("RemainingDuration", 0)
                pct = act.get("PercentComplete", 0)
                act_type = act.get("ActivityType", "")

                summary = f"Activity: {name} | Status: {status} | {pct}% complete"
                if remaining:
                    summary += f" | {remaining} days remaining"
                if start and finish:
                    summary += f" | {start[:10]} to {finish[:10]}"

                items.append(NormalizedItem(
                    external_id=f"p6act-{act.get('ObjectId', '')}",
                    item_type="schedule_activity",
                    title=f"{project_name}: {name}",
                    summary=summary,
                    raw_data=act,
                    metadata={
                        "project": project_name,
                        "status": status,
                        "start": start,
                        "finish": finish,
                        "remaining_days": remaining,
                        "percent_complete": pct,
                        "type": act_type,
                    },
                    source_url="",
                    item_date=self._parse_date(start),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("primavera_activities_error", project_id=project_id, error=str(e))
        return items

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    def _parse_date(self, date_str) -> datetime | None:
        if not date_str:
            return None
        try:
            if "T" in str(date_str):
                return datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        except Exception:
            return None
