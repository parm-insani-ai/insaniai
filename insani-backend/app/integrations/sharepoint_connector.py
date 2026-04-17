"""
SharePoint Connector — Fetches documents and lists via Microsoft Graph API.

Uses the same Microsoft OAuth as Outlook but with SharePoint-specific scopes.

Data extracted:
- Documents from SharePoint document libraries
- List items (tasks, issues, custom lists)
- Site information

Graph API SharePoint docs: https://learn.microsoft.com/en-us/graph/api/resources/sharepoint
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

MS_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

SP_CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID", os.getenv("MICROSOFT_CLIENT_ID", ""))
SP_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", os.getenv("MICROSOFT_CLIENT_SECRET", ""))
SP_REDIRECT_URI = os.getenv("SHAREPOINT_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/sharepoint")


class SharePointConnector(BaseConnector):
    PROVIDER = "sharepoint"
    DISPLAY_NAME = "Microsoft SharePoint"
    DESCRIPTION = "Sync documents, drawings, and project files from SharePoint"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=SP_CLIENT_ID,
            client_secret=SP_CLIENT_SECRET,
            auth_url=MS_AUTH_URL,
            token_url=MS_TOKEN_URL,
            scopes=["Sites.Read.All", "Files.Read.All", "User.Read", "offline_access"],
            redirect_uri=SP_REDIRECT_URI,
        )

    def get_auth_url(self, state: str) -> str:
        config = self.get_oauth_config()
        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(config.scopes),
            "state": state,
            "response_mode": "query",
            "prompt": "login",
        }
        return f"{MS_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(MS_TOKEN_URL, data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": config.redirect_uri,
                "scope": " ".join(config.scopes),
            })
            resp.raise_for_status()
            return resp.json()

    async def refresh_tokens(self, refresh_token: str) -> dict:
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(MS_TOKEN_URL, data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(config.scopes),
            })
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self, access_token: str) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{GRAPH_API_BASE}/me",
                    headers=self._headers(access_token),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                headers=self._headers(access_token),
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "email": data.get("mail", data.get("userPrincipalName", "")),
                "name": data.get("displayName", ""),
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
                # Get sites the user follows or has access to
                sites = await self._get_sites(client, headers)

                for site in sites[:5]:
                    site_id = site.get("id", "")
                    site_name = site.get("displayName", site.get("name", ""))

                    # Get document libraries (drives) in the site
                    drives = await self._get_drives(client, headers, site_id)

                    for drive in drives[:3]:
                        drive_id = drive.get("id", "")
                        drive_name = drive.get("name", "")

                        # Get recent files in the drive
                        files = await self._get_files(client, headers, drive_id, site_name, drive_name)
                        items.extend(files)

        except Exception as e:
            logger.error("sharepoint_fetch_error", error=str(e))
            raise

        logger.info("sharepoint_fetched", count=len(items))
        return items, ""

    async def _get_sites(self, client, headers) -> list:
        sites = []
        try:
            # Get sites the user follows
            resp = await client.get(
                f"{GRAPH_API_BASE}/me/followedSites",
                headers=headers,
            )
            if resp.status_code == 200:
                sites.extend(resp.json().get("value", []))

            # Also search for all sites
            resp2 = await client.get(
                f"{GRAPH_API_BASE}/sites?search=*",
                headers=headers,
                params={"$top": 10},
            )
            if resp2.status_code == 200:
                for s in resp2.json().get("value", []):
                    if s.get("id") not in [x.get("id") for x in sites]:
                        sites.append(s)
        except Exception as e:
            logger.warning("sharepoint_sites_error", error=str(e))
        return sites

    async def _get_drives(self, client, headers, site_id) -> list:
        try:
            resp = await client.get(
                f"{GRAPH_API_BASE}/sites/{site_id}/drives",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json().get("value", [])
        except Exception as e:
            logger.warning("sharepoint_drives_error", site_id=site_id, error=str(e))
            return []

    async def _get_files(self, client, headers, drive_id, site_name, drive_name) -> list:
        items = []
        try:
            resp = await client.get(
                f"{GRAPH_API_BASE}/drives/{drive_id}/root/children",
                headers=headers,
                params={"$top": 50, "$orderby": "lastModifiedDateTime desc"},
            )
            resp.raise_for_status()

            for item in resp.json().get("value", []):
                if "file" not in item:
                    continue

                name = item.get("name", "")
                size = item.get("size", 0)
                modified = item.get("lastModifiedDateTime", "")
                web_url = item.get("webUrl", "")
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

                doc_type = "document"
                if ext in ("pdf", "dwg", "dxf", "rvt"):
                    doc_type = "drawing"
                elif ext in ("jpg", "jpeg", "png", "heic"):
                    doc_type = "photo"
                elif ext in ("xlsx", "xls", "csv"):
                    doc_type = "spreadsheet"

                size_str = f"{size / 1024:.0f} KB" if size < 1048576 else f"{size / 1048576:.1f} MB"

                # Extract text content for readable files
                content_text = ""
                readable_exts = ("txt", "csv", "md", "json", "xml")
                if ext in readable_exts and size < 2 * 1024 * 1024:
                    try:
                        dl_resp = await client.get(
                            f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item['id']}/content",
                            headers=headers,
                            follow_redirects=True,
                        )
                        if dl_resp.status_code == 200:
                            content_text = dl_resp.text[:3000]
                    except Exception:
                        pass

                summary = f"SharePoint: {name} | Site: {site_name} | Library: {drive_name} | Size: {size_str}"
                if content_text:
                    summary += f"\nContent: {content_text[:500]}"

                items.append(NormalizedItem(
                    external_id=f"sp-{item.get('id', '')}",
                    item_type=doc_type,
                    title=name,
                    summary=summary,
                    raw_data={"id": item.get("id"), "name": name, "drive_id": drive_id},
                    metadata={
                        "site": site_name,
                        "library": drive_name,
                        "size": size,
                        "extension": ext,
                        "modified": modified,
                    },
                    source_url=web_url,
                    item_date=self._parse_date(modified),
                    project_hint=f"{site_name} {name}",
                ))
        except Exception as e:
            logger.warning("sharepoint_files_error", drive_id=drive_id, error=str(e))
        return items

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    def _parse_date(self, date_str) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
