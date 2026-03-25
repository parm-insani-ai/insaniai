"""
Autodesk Connector -- Fetches BIM data via Autodesk Platform Services (APS).

Formerly known as Forge. Covers:
- Autodesk Construction Cloud (ACC)
- BIM 360
- Autodesk Docs

Data extracted:
- Projects and folders
- Issues (field issues, quality, safety)
- Model metadata (not 3D geometry -- just names, versions, status)
- Clash reports / coordination issues
- Documents (specs, drawings)

APS docs: https://aps.autodesk.com/developer/overview
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

APS_AUTH_URL = "https://developer.api.autodesk.com/authentication/v2/authorize"
APS_TOKEN_URL = "https://developer.api.autodesk.com/authentication/v2/token"
APS_API_BASE = "https://developer.api.autodesk.com"

AUTODESK_CLIENT_ID = os.getenv("AUTODESK_CLIENT_ID", "")
AUTODESK_CLIENT_SECRET = os.getenv("AUTODESK_CLIENT_SECRET", "")
AUTODESK_REDIRECT_URI = os.getenv("AUTODESK_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/autodesk")


class AutodeskConnector(BaseConnector):
    PROVIDER = "autodesk"
    DISPLAY_NAME = "Autodesk BIM 360 / ACC"
    DESCRIPTION = "Sync models, clash reports, issues, and construction documents"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=AUTODESK_CLIENT_ID,
            client_secret=AUTODESK_CLIENT_SECRET,
            auth_url=APS_AUTH_URL,
            token_url=APS_TOKEN_URL,
            scopes=["data:read", "data:write", "account:read"],
            redirect_uri=AUTODESK_REDIRECT_URI,
        )

    def get_auth_url(self, state: str) -> str:
        config = self.get_oauth_config()
        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(config.scopes),
            "state": state,
        }
        return f"{APS_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(APS_TOKEN_URL, data={
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
            resp = await client.post(APS_TOKEN_URL, data={
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
                    f"{APS_API_BASE}/userprofile/v1/users/@me",
                    headers=self._headers(access_token),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{APS_API_BASE}/userprofile/v1/users/@me",
                headers=self._headers(access_token),
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "email": data.get("emailId", ""),
                "name": f"{data.get('firstName', '')} {data.get('lastName', '')}".strip(),
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
                # Get ACC/BIM360 hubs (accounts)
                hubs = await self._get_hubs(client, headers)

                for hub in hubs[:3]:
                    hub_id = hub["id"]

                    # Get projects in this hub
                    projects = await self._get_projects(client, headers, hub_id)

                    for proj in projects[:10]:
                        project_id = proj["id"]
                        project_name = proj.get("attributes", {}).get("name", "")

                        # Fetch issues
                        issues = await self._fetch_issues(client, headers, project_id, project_name, since)
                        items.extend(issues)

                        # Fetch documents/items from top folders
                        docs = await self._fetch_documents(client, headers, project_id, project_name)
                        items.extend(docs)

        except Exception as e:
            logger.error("autodesk_fetch_error", error=str(e))
            raise

        logger.info("autodesk_fetched", count=len(items))
        return items, ""

    async def _get_hubs(self, client, headers) -> list:
        resp = await client.get(
            f"{APS_API_BASE}/project/v1/hubs",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def _get_projects(self, client, headers, hub_id) -> list:
        resp = await client.get(
            f"{APS_API_BASE}/project/v1/hubs/{hub_id}/projects",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def _fetch_issues(self, client, headers, project_id, project_name, since) -> list:
        items = []
        try:
            # ACC Issues API (v2)
            # Extract the project UUID from the URN format
            proj_uuid = project_id.split(".")[-1] if "." in project_id else project_id

            params = {"limit": 50}
            if since:
                params["filter[updatedAt]"] = f"{since.strftime('%Y-%m-%dT%H:%M:%SZ')}..{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}"

            resp = await client.get(
                f"{APS_API_BASE}/construction/issues/v1/projects/{proj_uuid}/issues",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

            for issue in resp.json().get("results", []):
                status = issue.get("status", "")
                issue_type = issue.get("issueType", "")
                assignee = issue.get("assignedTo", "")

                items.append(NormalizedItem(
                    external_id=f"issue-{issue['id']}",
                    item_type="issue",
                    title=f"Issue: {issue.get('title', '')}",
                    summary=f"Issue: {issue.get('title', '')} | Type: {issue_type} | Status: {status} | Assigned: {assignee}\n{issue.get('description', '')[:300]}",
                    raw_data=issue,
                    metadata={
                        "status": status,
                        "type": issue_type,
                        "assignee": assignee,
                        "priority": issue.get("priority", ""),
                        "location": issue.get("locationDescription", ""),
                        "project": project_name,
                    },
                    source_url=f"https://acc.autodesk.com/build/issues/projects/{proj_uuid}/issues/{issue['id']}",
                    item_date=self._parse_date(issue.get("createdAt")),
                    project_hint=project_name,
                ))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.info("autodesk_issues_no_access", project_id=project_id)
            else:
                logger.warning("autodesk_issues_error", project_id=project_id, error=str(e))
        except Exception as e:
            logger.warning("autodesk_issues_error", project_id=project_id, error=str(e))
        return items

    async def _fetch_documents(self, client, headers, project_id, project_name) -> list:
        items = []
        try:
            # Get top-level folders
            resp = await client.get(
                f"{APS_API_BASE}/project/v1/hubs/b.{project_id.split('.')[-1] if '.' in project_id else project_id}/projects/{project_id}/topFolders",
                headers=headers,
            )
            if resp.status_code != 200:
                return items

            folders = resp.json().get("data", [])

            for folder in folders[:5]:
                folder_id = folder["id"]
                folder_name = folder.get("attributes", {}).get("name", "")

                # Get items in folder
                items_resp = await client.get(
                    f"{APS_API_BASE}/data/v1/projects/{project_id}/folders/{folder_id}/contents",
                    headers=headers,
                    params={"page[limit]": 20},
                )
                if items_resp.status_code != 200:
                    continue

                for doc in items_resp.json().get("data", []):
                    attrs = doc.get("attributes", {})
                    doc_name = attrs.get("displayName", attrs.get("name", ""))
                    doc_type = attrs.get("extension", {}).get("type", "")

                    items.append(NormalizedItem(
                        external_id=f"doc-{doc['id']}",
                        item_type="drawing",
                        title=doc_name,
                        summary=f"Document: {doc_name} | Folder: {folder_name} | Type: {doc_type}",
                        raw_data={"id": doc["id"], "name": doc_name, "folder": folder_name, "type": doc_type},
                        metadata={
                            "folder": folder_name,
                            "type": doc_type,
                            "version": attrs.get("versionNumber", 1),
                            "project": project_name,
                        },
                        source_url="",
                        item_date=self._parse_date(attrs.get("createTime")),
                        project_hint=project_name,
                    ))
        except Exception as e:
            logger.warning("autodesk_docs_error", project_id=project_id, error=str(e))
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
