"""
Procore Connector — Fetches construction project data via Procore REST API v1.1.

OAuth: Procore uses standard OAuth 2.0 with no additional scopes required.
The user's permissions determine what data is accessible.

Data extracted:
- RFIs (request for information)
- Submittals
- Change Orders
- Daily Logs

Procore API docs: https://developers.procore.com/reference/rest/v1
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

# Production URLs
PROCORE_AUTH_URL = "https://login.procore.com/oauth/authorize"
PROCORE_TOKEN_URL = "https://login.procore.com/oauth/token"
PROCORE_API_BASE = "https://api.procore.com/rest/v1.1"

# Sandbox URLs
PROCORE_SANDBOX_AUTH_URL = "https://login-sandbox.procore.com/oauth/authorize"
PROCORE_SANDBOX_TOKEN_URL = "https://login-sandbox.procore.com/oauth/token"
PROCORE_SANDBOX_API_BASE = "https://sandbox.procore.com/rest/v1.1"

PROCORE_CLIENT_ID = os.getenv("PROCORE_CLIENT_ID", "")
PROCORE_CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET", "")
PROCORE_REDIRECT_URI = os.getenv("PROCORE_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/procore")
PROCORE_USE_SANDBOX = os.getenv("PROCORE_SANDBOX", "true").lower() == "true"


class ProcoreConnector(BaseConnector):
    PROVIDER = "procore"
    DISPLAY_NAME = "Procore"
    DESCRIPTION = "Sync RFIs, submittals, change orders, and daily logs"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=PROCORE_CLIENT_ID,
            client_secret=PROCORE_CLIENT_SECRET,
            auth_url=PROCORE_SANDBOX_AUTH_URL if PROCORE_USE_SANDBOX else PROCORE_AUTH_URL,
            token_url=PROCORE_SANDBOX_TOKEN_URL if PROCORE_USE_SANDBOX else PROCORE_TOKEN_URL,
            scopes=[],  # Procore doesn't use scopes — permissions are user-level
            redirect_uri=PROCORE_REDIRECT_URI,
        )

    def _api_base(self) -> str:
        return PROCORE_SANDBOX_API_BASE if PROCORE_USE_SANDBOX else PROCORE_API_BASE

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
        """Exchange auth code for tokens. Procore requires form-encoded POST."""
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
                    f"{self._api_base()}/me",
                    headers=self._headers(access_token),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._api_base()}/me",
                headers=self._headers(access_token),
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "email": data.get("login", ""),
                "name": data.get("name", ""),
            }

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
                companies = await self._get_companies(client, headers)
                logger.info("procore_companies", count=len(companies))

                for company in companies[:3]:
                    company_id = company["id"]
                    co_headers = {**headers, "Procore-Company-Id": str(company_id)}

                    try:
                        projects = await self._get_projects(client, co_headers, company_id)
                        logger.info("procore_projects", company_id=company_id, count=len(projects))
                    except Exception as e:
                        logger.warning("procore_projects_error", company_id=company_id, error=str(e))
                        continue

                    for project in projects[:10]:
                        project_id = project["id"]
                        project_name = project.get("name", "")

                        rfis = await self._fetch_rfis(client, co_headers, project_id, project_name, since)
                        items.extend(rfis)

                        submittals = await self._fetch_submittals(client, co_headers, project_id, project_name, since)
                        items.extend(submittals)

                        cos = await self._fetch_change_orders(client, co_headers, project_id, project_name, since)
                        items.extend(cos)

                        logs = await self._fetch_daily_logs(client, co_headers, project_id, project_name, since)
                        items.extend(logs)

        except Exception as e:
            logger.error("procore_fetch_error", error=str(e))
            raise

        logger.info("procore_fetched", count=len(items))
        return items, ""

    async def _get_companies(self, client, headers) -> list:
        resp = await client.get(f"{self._api_base()}/companies", headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _get_projects(self, client, headers, company_id) -> list:
        resp = await client.get(
            f"{self._api_base()}/projects",
            headers=headers,
            params={"company_id": company_id, "per_page": 100},
        )
        resp.raise_for_status()
        return resp.json()

    async def _fetch_rfis(self, client, headers, project_id, project_name, since) -> list:
        items = []
        try:
            params = {"per_page": 100, "page": 1}
            if since:
                params["filters[updated_at]"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/rfis",
                headers=headers,
                params=params,
            )
            if resp.status_code != 200:
                logger.warning("procore_rfi_status", status=resp.status_code, project_id=project_id)
                return items

            for rfi in resp.json():
                status = rfi.get("status", "")
                assignee = ""
                if rfi.get("assignee") and isinstance(rfi["assignee"], dict):
                    assignee = rfi["assignee"].get("name", "")
                due_date = rfi.get("due_date", "")
                question = ""
                if rfi.get("question") and isinstance(rfi["question"], dict):
                    question = rfi["question"].get("plain_text_body", "")

                items.append(NormalizedItem(
                    external_id=f"rfi-{rfi['id']}",
                    item_type="rfi",
                    title=f"RFI #{rfi.get('number', '')} - {rfi.get('subject', '')}",
                    summary=f"RFI #{rfi.get('number', '')} | Status: {status} | Assigned to: {assignee} | Due: {due_date}\n{question[:500]}",
                    raw_data=rfi,
                    metadata={
                        "number": rfi.get("number"),
                        "status": status,
                        "assignee": assignee,
                        "due_date": due_date,
                        "priority": rfi.get("priority", ""),
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/{project_id}/project/rfis/{rfi['id']}",
                    item_date=self._parse_date(rfi.get("created_at")),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("procore_rfi_error", project_id=project_id, error=str(e))
        return items

    async def _fetch_submittals(self, client, headers, project_id, project_name, since) -> list:
        items = []
        try:
            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/submittals",
                headers=headers,
                params={"per_page": 100, "page": 1},
            )
            if resp.status_code != 200:
                return items

            for sub in resp.json():
                status = sub.get("status", {})
                if isinstance(status, dict):
                    status = status.get("name", "")

                spec = sub.get("specification_section", {})
                spec_label = spec.get("label", "") if isinstance(spec, dict) else ""

                items.append(NormalizedItem(
                    external_id=f"sub-{sub['id']}",
                    item_type="submittal",
                    title=f"Submittal #{sub.get('number', '')} - {sub.get('title', '')}",
                    summary=f"Submittal #{sub.get('number', '')} | Status: {status} | Spec: {spec_label}",
                    raw_data=sub,
                    metadata={
                        "number": sub.get("number"),
                        "status": status,
                        "spec_section": spec_label,
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/{project_id}/project/submittals/{sub['id']}",
                    item_date=self._parse_date(sub.get("created_at")),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("procore_submittal_error", project_id=project_id, error=str(e))
        return items

    async def _fetch_change_orders(self, client, headers, project_id, project_name, since) -> list:
        items = []
        try:
            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/change_order_packages",
                headers=headers,
                params={"per_page": 100, "page": 1},
            )
            if resp.status_code != 200:
                return items

            for co in resp.json():
                amount = co.get("grand_total", 0) or 0
                items.append(NormalizedItem(
                    external_id=f"co-{co['id']}",
                    item_type="change_order",
                    title=f"CO #{co.get('number', '')} - {co.get('title', '')}",
                    summary=f"Change Order #{co.get('number', '')} | Status: {co.get('status', '')} | Amount: ${amount:,.2f}",
                    raw_data=co,
                    metadata={
                        "number": co.get("number"),
                        "status": co.get("status", ""),
                        "amount": amount,
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/{project_id}/project/change_order_packages/{co['id']}",
                    item_date=self._parse_date(co.get("created_at")),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("procore_co_error", project_id=project_id, error=str(e))
        return items

    async def _fetch_daily_logs(self, client, headers, project_id, project_name, since) -> list:
        items = []
        try:
            params = {"per_page": 30, "page": 1}
            if since:
                params["log_date"] = f">{since.strftime('%Y-%m-%d')}"

            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/daily_logs",
                headers=headers,
                params=params,
            )
            if resp.status_code != 200:
                return items

            for log in resp.json():
                notes = log.get("notes", "")
                if isinstance(notes, dict):
                    notes = notes.get("plain_text_body", "")

                items.append(NormalizedItem(
                    external_id=f"log-{log['id']}",
                    item_type="daily_log",
                    title=f"Daily Log - {log.get('log_date', '')}",
                    summary=f"Daily Log for {log.get('log_date', '')} | Weather: {log.get('weather', '')} | Notes: {str(notes)[:300]}",
                    raw_data=log,
                    metadata={
                        "log_date": log.get("log_date", ""),
                        "weather": log.get("weather", ""),
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/{project_id}/project/daily_log",
                    item_date=self._parse_date(log.get("log_date")),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("procore_log_error", project_id=project_id, error=str(e))
        return items

    def _headers(self, access_token: str) -> dict:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

    def _parse_date(self, date_str) -> datetime | None:
        if not date_str:
            return None
        try:
            if "T" in str(date_str):
                return datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.strptime(str(date_str), "%Y-%m-%d")
        except Exception:
            return None
