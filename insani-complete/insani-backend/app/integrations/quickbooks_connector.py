"""
QuickBooks Connector — Fetches financial data via QuickBooks Online API.

OAuth scopes:
- com.intuit.quickbooks.accounting — full accounting data access

Data extracted:
- Invoices (vendor, amount, status, due date)
- Expenses / purchases
- Vendors (subcontractor info)
- Accounts (budget line items)
- Profit & Loss reports

The connector normalizes financial data into SyncedItems with:
- item_type: "invoice", "expense", "vendor", "budget_line"
- title: invoice number or vendor name
- summary: human-readable description for AI consumption
- metadata: {amount, vendor, status, due_date, account, paid}
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

# Intuit OAuth endpoints
INTUIT_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
INTUIT_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_API_BASE = "https://quickbooks.api.intuit.com/v3/company"

# Sandbox endpoints (for development)
INTUIT_SANDBOX_API = "https://sandbox-quickbooks.api.intuit.com/v3/company"

# Load from environment
QBO_CLIENT_ID = os.getenv("QUICKBOOKS_CLIENT_ID", "")
QBO_CLIENT_SECRET = os.getenv("QUICKBOOKS_CLIENT_SECRET", "")
QBO_REDIRECT_URI = os.getenv("QUICKBOOKS_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/quickbooks")
QBO_USE_SANDBOX = os.getenv("QUICKBOOKS_SANDBOX", "true").lower() == "true"


class QuickBooksConnector(BaseConnector):
    PROVIDER = "quickbooks"
    DISPLAY_NAME = "QuickBooks Online"
    DESCRIPTION = "Sync invoices, expenses, vendors, and budget data"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=QBO_CLIENT_ID,
            client_secret=QBO_CLIENT_SECRET,
            auth_url=INTUIT_AUTH_URL,
            token_url=INTUIT_TOKEN_URL,
            scopes=["com.intuit.quickbooks.accounting"],
            redirect_uri=QBO_REDIRECT_URI,
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
        return f"{INTUIT_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        config = self.get_oauth_config()
        import base64
        auth_header = base64.b64encode(f"{config.client_id}:{config.client_secret}".encode()).decode()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                INTUIT_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config.redirect_uri,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            # QBO returns realm_id (company ID) — store it in config
            data["realm_id"] = data.get("realmId", "")
            return data

    async def refresh_tokens(self, refresh_token: str) -> dict:
        config = self.get_oauth_config()
        import base64
        auth_header = base64.b64encode(f"{config.client_id}:{config.client_secret}".encode()).decode()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                INTUIT_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self, access_token: str) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._api_base()}/companyinfo/0",
                    headers=self._headers(access_token),
                    params={"minorversion": "65"},
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        # Need realm_id to make API calls — stored in connection config
        return {"name": "QuickBooks Company", "email": ""}

    async def fetch_data(
        self,
        access_token: str,
        since: datetime = None,
        cursor: str = "",
    ) -> tuple[list[NormalizedItem], str]:
        """
        Fetch invoices, expenses, and vendors from QuickBooks.
        Uses QBO query API with SQL-like syntax.
        """
        headers = self._headers(access_token)
        items = []
        api_base = self._api_base()

        # We need the realm_id — for now parse from cursor or use default
        realm_id = cursor.split("|")[0] if cursor and "|" in cursor else "0"
        start_position = int(cursor.split("|")[1]) if cursor and "|" in cursor else 1

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Fetch invoices
                invoice_items = await self._fetch_invoices(client, api_base, realm_id, headers, since)
                items.extend(invoice_items)

                # Fetch vendors (subcontractors)
                vendor_items = await self._fetch_vendors(client, api_base, realm_id, headers)
                items.extend(vendor_items)

                # Fetch expenses / purchases
                expense_items = await self._fetch_expenses(client, api_base, realm_id, headers, since)
                items.extend(expense_items)

        except Exception as e:
            logger.error("quickbooks_fetch_error", error=str(e))
            raise

        logger.info("quickbooks_fetched", count=len(items))
        return items, f"{realm_id}|{start_position + len(items)}"

    async def _fetch_invoices(self, client, api_base, realm_id, headers, since=None) -> list[NormalizedItem]:
        """Fetch invoices via QBO query API."""
        query = "SELECT * FROM Invoice ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        if since:
            date_str = since.strftime("%Y-%m-%d")
            query = f"SELECT * FROM Invoice WHERE MetaData.LastUpdatedTime > '{date_str}' ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"

        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": query, "minorversion": "65"},
            )
            resp.raise_for_status()
            data = resp.json()

            for inv in data.get("QueryResponse", {}).get("Invoice", []):
                items.append(self._normalize_invoice(inv))
        except Exception as e:
            logger.warning("quickbooks_invoice_fetch_error", error=str(e))

        return items

    async def _fetch_vendors(self, client, api_base, realm_id, headers) -> list[NormalizedItem]:
        """Fetch vendors (subcontractors, suppliers)."""
        query = "SELECT * FROM Vendor WHERE Active = true MAXRESULTS 100"
        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": query, "minorversion": "65"},
            )
            resp.raise_for_status()
            data = resp.json()

            for vendor in data.get("QueryResponse", {}).get("Vendor", []):
                items.append(self._normalize_vendor(vendor))
        except Exception as e:
            logger.warning("quickbooks_vendor_fetch_error", error=str(e))

        return items

    async def _fetch_expenses(self, client, api_base, realm_id, headers, since=None) -> list[NormalizedItem]:
        """Fetch purchases/expenses."""
        query = "SELECT * FROM Purchase ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        if since:
            date_str = since.strftime("%Y-%m-%d")
            query = f"SELECT * FROM Purchase WHERE MetaData.LastUpdatedTime > '{date_str}' ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"

        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": query, "minorversion": "65"},
            )
            resp.raise_for_status()
            data = resp.json()

            for exp in data.get("QueryResponse", {}).get("Purchase", []):
                items.append(self._normalize_expense(exp))
        except Exception as e:
            logger.warning("quickbooks_expense_fetch_error", error=str(e))

        return items

    def _normalize_invoice(self, inv: dict) -> NormalizedItem:
        vendor_name = inv.get("CustomerRef", {}).get("name", "Unknown")
        amount = inv.get("TotalAmt", 0)
        balance = inv.get("Balance", 0)
        due_date = inv.get("DueDate", "")
        doc_number = inv.get("DocNumber", "")
        status = "Paid" if balance == 0 else "Open"

        lines = inv.get("Line", [])
        line_items = []
        for line in lines:
            desc = line.get("Description", "")
            line_amt = line.get("Amount", 0)
            if desc:
                line_items.append(f"{desc}: ${line_amt:,.2f}")

        summary = f"Invoice #{doc_number} | {vendor_name} | ${amount:,.2f} | Status: {status}"
        if due_date:
            summary += f" | Due: {due_date}"
        if line_items:
            summary += "\nLine items: " + "; ".join(line_items[:5])

        return NormalizedItem(
            external_id=f"inv-{inv.get('Id', '')}",
            item_type="invoice",
            title=f"Invoice #{doc_number} - {vendor_name}",
            summary=summary,
            raw_data=inv,
            metadata={
                "doc_number": doc_number,
                "vendor": vendor_name,
                "amount": amount,
                "balance": balance,
                "status": status,
                "due_date": due_date,
                "line_count": len(lines),
            },
            source_url="",
            item_date=self._parse_date(inv.get("TxnDate")),
            project_hint=vendor_name,
        )

    def _normalize_vendor(self, vendor: dict) -> NormalizedItem:
        name = vendor.get("DisplayName", "Unknown")
        balance = vendor.get("Balance", 0)
        email = vendor.get("PrimaryEmailAddr", {}).get("Address", "")
        phone = vendor.get("PrimaryPhone", {}).get("FreeFormNumber", "")

        summary = f"Vendor: {name}"
        if balance:
            summary += f" | Outstanding balance: ${balance:,.2f}"
        if email:
            summary += f" | Email: {email}"
        if phone:
            summary += f" | Phone: {phone}"

        return NormalizedItem(
            external_id=f"vendor-{vendor.get('Id', '')}",
            item_type="vendor",
            title=name,
            summary=summary,
            raw_data=vendor,
            metadata={
                "name": name,
                "balance": balance,
                "email": email,
                "phone": phone,
                "active": vendor.get("Active", True),
            },
            source_url="",
            project_hint=name,
        )

    def _normalize_expense(self, exp: dict) -> NormalizedItem:
        amount = exp.get("TotalAmt", 0)
        vendor_name = exp.get("EntityRef", {}).get("name", "Unknown")
        account = exp.get("AccountRef", {}).get("name", "")
        txn_date = exp.get("TxnDate", "")

        lines = exp.get("Line", [])
        descriptions = [l.get("Description", "") for l in lines if l.get("Description")]

        summary = f"Expense: ${amount:,.2f} | Vendor: {vendor_name}"
        if account:
            summary += f" | Account: {account}"
        if descriptions:
            summary += f" | Items: {'; '.join(descriptions[:3])}"

        return NormalizedItem(
            external_id=f"exp-{exp.get('Id', '')}",
            item_type="expense",
            title=f"${amount:,.2f} - {vendor_name}",
            summary=summary,
            raw_data=exp,
            metadata={
                "amount": amount,
                "vendor": vendor_name,
                "account": account,
                "date": txn_date,
                "payment_type": exp.get("PaymentType", ""),
            },
            source_url="",
            item_date=self._parse_date(txn_date),
            project_hint=vendor_name,
        )

    def _headers(self, access_token: str) -> dict:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _api_base(self) -> str:
        return INTUIT_SANDBOX_API if QBO_USE_SANDBOX else QBO_API_BASE

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None
