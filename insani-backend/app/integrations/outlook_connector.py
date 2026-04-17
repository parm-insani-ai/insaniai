"""
Microsoft Outlook / Office 365 Connector — Fetches emails and calendar events
via Microsoft Graph API.

OAuth scopes:
- Mail.Read — read email messages
- Calendars.Read — read calendar events
- User.Read — get user profile

Data extracted:
- Emails (subject, from, to, body text, attachments)
- Calendar events (meetings, inspections, site visits)

Graph API docs: https://learn.microsoft.com/en-us/graph/overview
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

MS_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")
MS_REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/outlook")


class OutlookConnector(BaseConnector):
    PROVIDER = "outlook"
    DISPLAY_NAME = "Microsoft Outlook / Office 365"
    DESCRIPTION = "Sync emails, calendar events, and attachments from Outlook"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=MS_CLIENT_ID,
            client_secret=MS_CLIENT_SECRET,
            auth_url=MS_AUTH_URL,
            token_url=MS_TOKEN_URL,
            scopes=["Mail.Read", "Calendars.Read", "User.Read", "offline_access"],
            redirect_uri=MS_REDIRECT_URI,
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
                emails = await self._fetch_emails(client, headers, since)
                items.extend(emails)

                events = await self._fetch_calendar_events(client, headers, since)
                items.extend(events)

        except Exception as e:
            logger.error("outlook_fetch_error", error=str(e))
            raise

        logger.info("outlook_fetched", count=len(items))
        return items, ""

    async def _fetch_emails(self, client, headers, since=None) -> list:
        items = []
        try:
            params = {
                "$top": 50,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,hasAttachments,webLink",
            }
            if since:
                params["$filter"] = f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"

            resp = await client.get(
                f"{GRAPH_API_BASE}/me/messages",
                headers=headers,
                params=params,
            )
            
            if resp.status_code != 200:
                www_auth = resp.headers.get("www-authenticate", "")
                logger.error("outlook_email_debug", status=resp.status_code, body=resp.text[:500], www_auth=www_auth[:500])
                return items

            for msg in resp.json().get("value", []):
                from_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "")
                from_name = msg.get("from", {}).get("emailAddress", {}).get("name", "")
                to_list = [r.get("emailAddress", {}).get("address", "") for r in msg.get("toRecipients", [])]
                subject = msg.get("subject", "(no subject)")
                body_preview = msg.get("bodyPreview", "")
                web_link = msg.get("webLink", "")

                summary = f"From: {from_name} <{from_addr}>\nTo: {', '.join(to_list)}\n{body_preview}"

                items.append(NormalizedItem(
                    external_id=f"outlook-{msg['id'][:50]}",
                    item_type="email",
                    title=subject,
                    summary=summary,
                    raw_data={"id": msg["id"], "subject": subject, "from": from_addr},
                    metadata={
                        "from": from_addr,
                        "from_name": from_name,
                        "to": to_list,
                        "has_attachments": msg.get("hasAttachments", False),
                    },
                    source_url=web_link,
                    item_date=self._parse_date(msg.get("receivedDateTime")),
                    project_hint=subject,
                ))
        except Exception as e:
            logger.warning("outlook_email_error", error=str(e))
        return items

    async def _fetch_calendar_events(self, client, headers, since=None) -> list:
        items = []
        try:
            now = datetime.utcnow()
            params = {
                "$top": 30,
                "$orderby": "start/dateTime desc",
                "$select": "id,subject,organizer,start,end,location,bodyPreview,webLink,attendees",
            }

            resp = await client.get(
                f"{GRAPH_API_BASE}/me/events",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()

            for event in resp.json().get("value", []):
                subject = event.get("subject", "")
                organizer = event.get("organizer", {}).get("emailAddress", {}).get("name", "")
                location = event.get("location", {}).get("displayName", "")
                start = event.get("start", {}).get("dateTime", "")
                end = event.get("end", {}).get("dateTime", "")
                attendees = [a.get("emailAddress", {}).get("name", "") for a in event.get("attendees", [])]

                summary = f"Event: {subject} | Organizer: {organizer}"
                if location:
                    summary += f" | Location: {location}"
                if start:
                    summary += f" | Start: {start[:16]}"
                if attendees:
                    summary += f" | Attendees: {', '.join(attendees[:5])}"

                items.append(NormalizedItem(
                    external_id=f"event-{event['id'][:50]}",
                    item_type="calendar_event",
                    title=subject,
                    summary=summary,
                    raw_data={"id": event["id"], "subject": subject},
                    metadata={
                        "organizer": organizer,
                        "location": location,
                        "start": start,
                        "end": end,
                        "attendees": attendees,
                    },
                    source_url=event.get("webLink", ""),
                    item_date=self._parse_date(start),
                    project_hint=subject,
                ))
        except Exception as e:
            logger.warning("outlook_calendar_error", error=str(e))
        return items

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    def _parse_date(self, date_str) -> datetime | None:
        if not date_str:
            return None
        try:
            if "T" in str(date_str):
                return datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.strptime(str(date_str), "%Y-%m-%d")
        except Exception:
            return None
