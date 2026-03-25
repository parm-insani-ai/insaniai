"""
Dropbox Connector — Fetches files AND their contents via Dropbox API.

Downloads readable files (PDF, txt, docx, csv) and extracts text
so the AI can answer questions about what's inside the documents,
not just what files exist.

Supported file types for content extraction:
- PDF — parsed with pypdf
- TXT, CSV, MD, JSON — read as plain text
- DOCX — basic text extraction
- Others — metadata only (name, path, size)

Files larger than 5MB are skipped for content extraction.
"""

import os
import io
import json
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

DROPBOX_AUTH_URL = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
DROPBOX_API_BASE = "https://api.dropboxapi.com/2"
DROPBOX_CONTENT_URL = "https://content.dropboxapi.com/2"

DROPBOX_CLIENT_ID = os.getenv("DROPBOX_CLIENT_ID", "")
DROPBOX_CLIENT_SECRET = os.getenv("DROPBOX_CLIENT_SECRET", "")
DROPBOX_REDIRECT_URI = os.getenv("DROPBOX_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/dropbox")

# Max file size to download for content extraction (5MB)
MAX_CONTENT_SIZE = 5 * 1024 * 1024

# File extensions we can extract text from
READABLE_EXTENSIONS = {"pdf", "txt", "csv", "md", "json", "log", "xml", "html", "htm"}


class DropboxConnector(BaseConnector):
    PROVIDER = "dropbox"
    DISPLAY_NAME = "Dropbox"
    DESCRIPTION = "Sync construction documents, drawings, photos, and shared files"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=DROPBOX_CLIENT_ID,
            client_secret=DROPBOX_CLIENT_SECRET,
            auth_url=DROPBOX_AUTH_URL,
            token_url=DROPBOX_TOKEN_URL,
            scopes=["files.metadata.read", "files.content.read", "sharing.read"],
            redirect_uri=DROPBOX_REDIRECT_URI,
        )

    def get_auth_url(self, state: str) -> str:
        config = self.get_oauth_config()
        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "token_access_type": "offline",
            "state": state,
        }
        return f"{DROPBOX_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        config = self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            resp = await client.post(DROPBOX_TOKEN_URL, data={
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
            resp = await client.post(DROPBOX_TOKEN_URL, data={
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
                resp = await client.post(
                    f"{DROPBOX_API_BASE}/users/get_current_account",
                    headers=self._headers(access_token),
                    content="null",
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DROPBOX_API_BASE}/users/get_current_account",
                headers=self._headers(access_token),
                content="null",
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "email": data.get("email", ""),
                "name": data.get("name", {}).get("display_name", ""),
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
            async with httpx.AsyncClient(timeout=60.0) as client:
                # List all files
                file_entries = await self._list_files(client, headers, cursor)

                # For each readable file, download and extract content
                for entry in file_entries:
                    name = entry.get("name", "")
                    path = entry.get("path_display", "")
                    size = entry.get("size", 0)
                    modified = entry.get("server_modified", "")
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

                    # Categorize by extension
                    doc_type = "document"
                    if ext in ("pdf", "dwg", "dxf", "rvt"):
                        doc_type = "drawing"
                    elif ext in ("jpg", "jpeg", "png", "heic"):
                        doc_type = "photo"
                    elif ext in ("xlsx", "xls", "csv"):
                        doc_type = "spreadsheet"

                    size_str = f"{size / 1024:.0f} KB" if size < 1048576 else f"{size / 1048576:.1f} MB"

                    # Try to extract content for readable files
                    content_text = ""
                    if ext in READABLE_EXTENSIONS and size <= MAX_CONTENT_SIZE:
                        try:
                            content_text = await self._download_and_extract(client, access_token, path, ext)
                            logger.info("dropbox_content_extracted", file=name, chars=len(content_text))
                        except Exception as e:
                            logger.warning("dropbox_content_error", file=name, error=str(e))

                    # Build summary with content
                    summary = f"File: {name} | Path: {path} | Size: {size_str}"
                    if content_text:
                        # Truncate content to 2000 chars for AI context
                        truncated = content_text[:2000]
                        if len(content_text) > 2000:
                            truncated += f"... [{len(content_text)} total chars]"
                        summary += f"\n\nCONTENT:\n{truncated}"
                    elif ext in ("jpg", "jpeg", "png", "heic"):
                        summary += " | (image file - no text content)"
                    elif size > MAX_CONTENT_SIZE:
                        summary += f" | (file too large for content extraction - {size_str})"

                    items.append(NormalizedItem(
                        external_id=f"dbx-{entry.get('id', '')}",
                        item_type=doc_type,
                        title=name,
                        summary=summary,
                        raw_data={"id": entry.get("id"), "name": name, "path": path, "size": size},
                        metadata={
                            "path": path,
                            "size": size,
                            "extension": ext,
                            "modified": modified,
                            "has_content": bool(content_text),
                            "content_chars": len(content_text),
                        },
                        source_url=f"https://www.dropbox.com/home{path}",
                        item_date=self._parse_date(modified),
                        project_hint=path,
                    ))

        except Exception as e:
            logger.error("dropbox_fetch_error", error=str(e))
            raise

        logger.info("dropbox_fetched", count=len(items), with_content=sum(1 for i in items if i.metadata.get("has_content")))
        return items, ""

    async def _list_files(self, client, headers, cursor="") -> list:
        entries = []
        try:
            if cursor:
                resp = await client.post(
                    f"{DROPBOX_API_BASE}/files/list_folder/continue",
                    headers=headers,
                    json={"cursor": cursor},
                )
            else:
                resp = await client.post(
                    f"{DROPBOX_API_BASE}/files/list_folder",
                    headers=headers,
                    json={
                        "path": "",
                        "recursive": True,
                        "include_non_downloadable_files": False,
                        "include_deleted": False,
                    },
                )

            if resp.status_code != 200:
                logger.warning("dropbox_list_response", status=resp.status_code, body=resp.text[:200])
                return entries

            data = resp.json()

            for entry in data.get("entries", []):
                if entry.get(".tag") == "file":
                    entries.append(entry)

        except Exception as e:
            logger.warning("dropbox_list_error", error=str(e))
        return entries

    async def _download_and_extract(self, client, access_token: str, path: str, ext: str) -> str:
        """Download a file from Dropbox and extract its text content."""
        import json as json_mod

        # Dropbox content download uses a different endpoint and header format
        download_headers = {
            "Authorization": f"Bearer {access_token}",
            "Dropbox-API-Arg": json_mod.dumps({"path": path}),
        }

        resp = await client.post(
            f"{DROPBOX_CONTENT_URL}/files/download",
            headers=download_headers,
        )
        resp.raise_for_status()
        file_bytes = resp.content

        # Extract text based on file type
        if ext == "pdf":
            return self._extract_pdf_text(file_bytes)
        elif ext in ("txt", "md", "log", "csv"):
            return file_bytes.decode("utf-8", errors="replace")
        elif ext == "json":
            try:
                data = json_mod.loads(file_bytes)
                return json_mod.dumps(data, indent=2)[:3000]
            except Exception:
                return file_bytes.decode("utf-8", errors="replace")[:3000]
        elif ext in ("xml", "html", "htm"):
            import re
            text = file_bytes.decode("utf-8", errors="replace")
            # Strip HTML/XML tags for cleaner text
            return re.sub(r'<[^>]+>', ' ', text).strip()
        else:
            return ""

    def _extract_pdf_text(self, file_bytes: bytes) -> str:
        """Extract text from a PDF file using pypdf."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            text_parts = []
            for i, page in enumerate(reader.pages[:20]):  # Cap at 20 pages
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(f"[Page {i+1}] {page_text.strip()}")
            return "\n\n".join(text_parts)
        except Exception as e:
            logger.warning("dropbox_pdf_parse_error", error=str(e))
            return ""

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    def _parse_date(self, date_str) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
