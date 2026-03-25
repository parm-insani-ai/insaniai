"""
Gmail Connector — Fetches project-related emails via Google Gmail API.

OAuth scopes:
- gmail.readonly — read email content and metadata
- gmail.metadata — faster metadata-only queries (optional)

Data extracted:
- Email threads (grouped conversations)
- Subject, from, to, date, body text
- Attachment names and types
- Labels (inbox, sent, starred)

The connector normalizes emails into SyncedItems with:
- item_type: "email"
- title: email subject
- summary: first ~500 chars of body text
- metadata: {from, to, date, labels, has_attachments, attachment_names}
"""

import os
import base64
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem
from app.config import settings

logger = structlog.get_logger()

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Load from environment
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/gmail")


class GmailConnector(BaseConnector):
    PROVIDER = "gmail"
    DISPLAY_NAME = "Gmail / Google Workspace"
    DESCRIPTION = "Sync project emails, attachments, and meeting invites"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            auth_url=GOOGLE_AUTH_URL,
            token_url=GOOGLE_TOKEN_URL,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            redirect_uri=GOOGLE_REDIRECT_URI,
        )

    def get_auth_url(self, state: str) -> str:
        config = self.get_oauth_config()
        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(config.scopes),
            "access_type": "offline",      # Gets us a refresh token
            "prompt": "consent",           # Always show consent (ensures refresh token)
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data={
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
            resp = await client.post(GOOGLE_TOKEN_URL, data={
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
                    f"{GMAIL_API_BASE}/users/me/profile",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GMAIL_API_BASE}/users/me/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "email": data.get("emailAddress", ""),
                "name": data.get("emailAddress", "").split("@")[0],
                "total_messages": data.get("messagesTotal", 0),
            }

    async def fetch_data(
        self,
        access_token: str,
        since: datetime = None,
        cursor: str = "",
    ) -> tuple[list[NormalizedItem], str]:
        """
        Fetch emails from Gmail.
        Uses the Gmail search query to filter project-related emails.
        Returns normalized email items + next page token.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        items = []
        new_cursor = ""

        # Build search query
        # In production, this would be configurable per project
        query_parts = []
        if since:
            date_str = since.strftime("%Y/%m/%d")
            query_parts.append(f"after:{date_str}")
        # Filter to likely project-related emails
        query_parts.append("(RFI OR submittal OR change order OR inspection OR schedule OR budget OR invoice)")
        query = " ".join(query_parts)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # List messages matching query
                params = {
                    "q": query,
                    "maxResults": 50,
                }
                if cursor:
                    params["pageToken"] = cursor

                resp = await client.get(
                    f"{GMAIL_API_BASE}/users/me/messages",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                list_data = resp.json()

                messages = list_data.get("messages", [])
                new_cursor = list_data.get("nextPageToken", "")

                # Fetch full content for each message
                for msg_stub in messages[:50]:  # Cap at 50 per sync
                    try:
                        msg_resp = await client.get(
                            f"{GMAIL_API_BASE}/users/me/messages/{msg_stub['id']}",
                            headers=headers,
                            params={"format": "full"},
                        )
                        msg_resp.raise_for_status()
                        msg_data = msg_resp.json()

                        normalized = self._normalize_email(msg_data)
                        if normalized:
                            items.append(normalized)

                    except Exception as e:
                        logger.warning("gmail_msg_fetch_error", msg_id=msg_stub.get("id"), error=str(e))
                        continue

        except Exception as e:
            logger.error("gmail_fetch_error", error=str(e))
            raise

        logger.info("gmail_fetched", count=len(items), has_more=bool(new_cursor))
        return items, new_cursor

    def _normalize_email(self, msg_data: dict) -> NormalizedItem | None:
        """Convert a Gmail API message into a NormalizedItem."""
        try:
            headers = {h["name"].lower(): h["value"] for h in msg_data.get("payload", {}).get("headers", [])}

            subject = headers.get("subject", "(no subject)")
            from_addr = headers.get("from", "")
            to_addr = headers.get("to", "")
            date_str = headers.get("date", "")

            # Parse date
            item_date = None
            if date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    item_date = parsedate_to_datetime(date_str)
                except Exception:
                    pass

            # Extract body text
            body = self._extract_body(msg_data.get("payload", {}))

            # Check for attachments
            attachments = self._extract_attachment_names(msg_data.get("payload", {}))

            # Build summary
            summary_parts = [f"From: {from_addr}", f"To: {to_addr}"]
            if body:
                # First 500 chars of body
                clean_body = body[:500].strip()
                summary_parts.append(clean_body)
            if attachments:
                summary_parts.append(f"Attachments: {', '.join(attachments)}")

            return NormalizedItem(
                external_id=msg_data["id"],
                item_type="email",
                title=subject,
                summary="\n".join(summary_parts),
                raw_data={
                    "id": msg_data["id"],
                    "thread_id": msg_data.get("threadId", ""),
                    "labels": msg_data.get("labelIds", []),
                    "snippet": msg_data.get("snippet", ""),
                },
                metadata={
                    "from": from_addr,
                    "to": to_addr,
                    "date": date_str,
                    "labels": msg_data.get("labelIds", []),
                    "has_attachments": len(attachments) > 0,
                    "attachment_names": attachments,
                    "thread_id": msg_data.get("threadId", ""),
                },
                source_url=f"https://mail.google.com/mail/u/0/#inbox/{msg_data['id']}",
                item_date=item_date,
                project_hint=subject,  # Use subject for project matching
            )

        except Exception as e:
            logger.warning("gmail_normalize_error", error=str(e))
            return None

    def _extract_body(self, payload: dict) -> str:
        """Recursively extract plain text body from Gmail message payload."""
        mime_type = payload.get("mimeType", "")

        # Direct text/plain part
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Multipart — recurse into parts
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Try text/html as fallback (strip tags roughly)
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    import re
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    return re.sub(r'<[^>]+>', ' ', html).strip()

        # Nested multipart
        for part in parts:
            body = self._extract_body(part)
            if body:
                return body

        return ""

    def _extract_attachment_names(self, payload: dict) -> list[str]:
        """Extract attachment filenames from a Gmail message payload."""
        names = []
        parts = payload.get("parts", [])
        for part in parts:
            filename = part.get("filename", "")
            if filename:
                names.append(filename)
            # Recurse into nested parts
            names.extend(self._extract_attachment_names(part))
        return names
