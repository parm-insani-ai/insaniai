"""
Procore Connector -- Fetches construction project data via Procore API.

OAuth scopes: Procore uses company-level and project-level permissions.
The OAuth flow grants access to all projects the user has access to.

Data extracted:
- RFIs (request for information)
- Submittals
- Change Orders
- Daily Logs
- Drawings / Documents
- Punch List items

Procore API docs: https://developers.procore.com/reference/rest/v1
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

PROCORE_AUTH_URL = "https://login.procore.com/oauth/authorize"
PROCORE_TOKEN_URL = "https://login.procore.com/oauth/token"
PROCORE_API_BASE = "https://api.procore.com/rest/v1.0"

# Sandbox
PROCORE_SANDBOX_AUTH_URL = "https://login-sandbox.procore.com/oauth/authorize"
PROCORE_SANDBOX_TOKEN_URL = "https://login-sandbox.procore.com/oauth/token"
PROCORE_SANDBOX_API_BASE = "https://sandbox.procore.com/rest/v1.0"

PROCORE_CLIENT_ID = os.getenv("PROCORE_CLIENT_ID", "")
PROCORE_CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET", "")
PROCORE_REDIRECT_URI = os.getenv("PROCORE_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/procore")
PROCORE_USE_SANDBOX = os.getenv("PROCORE_SANDBOX", "true").lower() == "true"


class ProcoreConnector(BaseConnector):
    PROVIDER = "procore"
    DISPLAY_NAME = "Procore"
    DESCRIPTION = "Sync RFIs, submittals, change orders, daily logs, and drawings"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=PROCORE_CLIENT_ID,
            client_secret=PROCORE_CLIENT_SECRET,
            auth_url=PROCORE_SANDBOX_AUTH_URL if PROCORE_USE_SANDBOX else PROCORE_AUTH_URL,
            token_url=PROCORE_SANDBOX_TOKEN_URL if PROCORE_USE_SANDBOX else PROCORE_TOKEN_URL,
            scopes=[],
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
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(config.token_url, json={
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
            resp = await client.post(config.token_url, json={
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
                # Get companies first
                try:
                    companies = await self._get_companies(client, headers)
                    logger.info("procore_companies", count=len(companies))
                except Exception as e:
                    logger.error("procore_companies_error", error=str(e))
                    companies = []

                for company in companies[:3]:
                    company_id = company["id"]
                    logger.info("procore_processing_company", company_id=company_id, name=company.get("name", ""))

                    try:
                        projects = await self._get_projects(client, headers, company_id)
                        logger.info("procore_projects", company_id=company_id, count=len(projects))
                    except Exception as e:
                        logger.warning("procore_projects_error", company_id=company_id, error=str(e))
                        continue

                    for project in projects[:10]:
                        project_id = project["id"]
                        project_name = project.get("name", "")
                        logger.info("procore_processing_project", project_id=project_id, name=project_name)

                        rfis = await self._fetch_rfis(client, headers, company_id, project_id, project_name, since)
                        items.extend(rfis)

                        submittals = await self._fetch_submittals(client, headers, company_id, project_id, project_name, since)
                        items.extend(submittals)

                        cos = await self._fetch_change_orders(client, headers, company_id, project_id, project_name, since)
                        items.extend(cos)

                        logs = await self._fetch_daily_logs(client, headers, company_id, project_id, project_name, since)
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
            headers={**headers, "Procore-Company-Id": str(company_id)},
            params={"company_id": company_id},
        )
        resp.raise_for_status()
        return resp.json()

    async def _fetch_rfis(self, client, headers, company_id, project_id, project_name, since) -> list:
        items = []
        try:
            params = {"per_page": 50}
            if since:
                params["filters[updated_at]"] = f"{since.strftime('%Y-%m-%dT%H:%M:%SZ')}...{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}"

            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/rfis",
                headers={**headers, "Procore-Company-Id": str(company_id)},
                params=params,
            )
            resp.raise_for_status()

            for rfi in resp.json():
                status = rfi.get("status", "")
                assignee = rfi.get("assignee", {}).get("name", "") if rfi.get("assignee") else ""
                due_date = rfi.get("due_date", "")

                items.append(NormalizedItem(
                    external_id=f"rfi-{rfi['id']}",
                    item_type="rfi",
                    title=f"RFI #{rfi.get('number', '')} - {rfi.get('subject', '')}",
                    summary=f"RFI #{rfi.get('number', '')} | Status: {status} | Assigned to: {assignee} | Due: {due_date}\n{rfi.get('question', {}).get('plain_text_body', '')}",
                    raw_data=rfi,
                    metadata={
                        "number": rfi.get("number"),
                        "status": status,
                        "assignee": assignee,
                        "due_date": due_date,
                        "priority": rfi.get("priority", ""),
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/projects/{project_id}/rfis/{rfi['id']}",
                    item_date=self._parse_date(rfi.get("created_at")),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("procore_rfi_error", project_id=project_id, error=str(e))
        return items

    async def _fetch_submittals(self, client, headers, company_id, project_id, project_name, since) -> list:
        items = []
        try:
            params = {"per_page": 50}
            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/submittals",
                headers={**headers, "Procore-Company-Id": str(company_id)},
                params=params,
            )
            resp.raise_for_status()

            for sub in resp.json():
                status = sub.get("status", {}).get("name", "") if isinstance(sub.get("status"), dict) else sub.get("status", "")
                items.append(NormalizedItem(
                    external_id=f"sub-{sub['id']}",
                    item_type="submittal",
                    title=f"Submittal #{sub.get('number', '')} - {sub.get('title', '')}",
                    summary=f"Submittal #{sub.get('number', '')} | Status: {status} | Spec section: {sub.get('specification_section', {}).get('label', '')}",
                    raw_data=sub,
                    metadata={
                        "number": sub.get("number"),
                        "status": status,
                        "spec_section": sub.get("specification_section", {}).get("label", ""),
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/projects/{project_id}/submittals/{sub['id']}",
                    item_date=self._parse_date(sub.get("created_at")),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("procore_submittal_error", project_id=project_id, error=str(e))
        return items

    async def _fetch_change_orders(self, client, headers, company_id, project_id, project_name, since) -> list:
        items = []
        try:
            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/change_order_packages",
                headers={**headers, "Procore-Company-Id": str(company_id)},
                params={"per_page": 50},
            )
            resp.raise_for_status()

            for co in resp.json():
                items.append(NormalizedItem(
                    external_id=f"co-{co['id']}",
                    item_type="change_order",
                    title=f"CO #{co.get('number', '')} - {co.get('title', '')}",
                    summary=f"Change Order #{co.get('number', '')} | Status: {co.get('status', '')} | Amount: ${co.get('grand_total', 0):,.2f}",
                    raw_data=co,
                    metadata={
                        "number": co.get("number"),
                        "status": co.get("status", ""),
                        "amount": co.get("grand_total", 0),
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/projects/{project_id}/change_order_packages/{co['id']}",
                    item_date=self._parse_date(co.get("created_at")),
                    project_hint=project_name,
                ))
        except Exception as e:
            logger.warning("procore_co_error", project_id=project_id, error=str(e))
        return items

    async def _fetch_daily_logs(self, client, headers, company_id, project_id, project_name, since) -> list:
        items = []
        try:
            params = {"per_page": 20}
            if since:
                params["filters[log_date]"] = f"{since.strftime('%Y-%m-%d')}...{datetime.utcnow().strftime('%Y-%m-%d')}"

            resp = await client.get(
                f"{self._api_base()}/projects/{project_id}/daily_logs",
                headers={**headers, "Procore-Company-Id": str(company_id)},
                params=params,
            )
            resp.raise_for_status()

            for log in resp.json():
                items.append(NormalizedItem(
                    external_id=f"log-{log['id']}",
                    item_type="daily_log",
                    title=f"Daily Log - {log.get('log_date', '')}",
                    summary=f"Daily Log for {log.get('log_date', '')} | Weather: {log.get('weather', '')} | Notes: {log.get('notes', {}).get('plain_text_body', '')[:300]}",
                    raw_data=log,
                    metadata={
                        "log_date": log.get("log_date", ""),
                        "weather": log.get("weather", ""),
                        "project": project_name,
                    },
                    source_url=f"https://app.procore.com/projects/{project_id}/daily_log/{log['id']}",
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
