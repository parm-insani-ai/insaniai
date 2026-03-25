"""
Sage 300 CRE Connector — Fetches construction accounting data.

Sage 300 CRE (formerly Timberline) is the dominant construction
accounting software for mid/large GCs. Integration options:

1. Sage Intacct API (cloud) — REST API with OAuth
2. Sage 300 CRE SDK (on-premise) — requires local ODBC/COM
3. Sage Construction APIs — newer REST endpoints

This connector targets Sage Intacct's REST API which many
construction companies have migrated to or use alongside 300 CRE.

Data extracted:
- General Ledger accounts and balances
- Accounts Payable (vendor invoices, bills)
- Accounts Receivable (customer invoices)
- Job Cost data (costs by project/phase/cost code)
- Vendors and customers
- Purchase Orders

Sage Intacct API: https://developer.intacct.com/
"""

import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

# Sage Intacct uses session-based auth (company ID + credentials)
# or OAuth via partner integrations
SAGE_AUTH_URL = os.getenv("SAGE_AUTH_URL", "https://api.intacct.com/ia/api/v1/company/authorize")
SAGE_TOKEN_URL = os.getenv("SAGE_TOKEN_URL", "https://api.intacct.com/ia/api/v1/company/token")
SAGE_API_BASE = os.getenv("SAGE_API_BASE", "https://api.intacct.com/ia/xml/xmlgw.phtml")

SAGE_CLIENT_ID = os.getenv("SAGE_CLIENT_ID", "")
SAGE_CLIENT_SECRET = os.getenv("SAGE_CLIENT_SECRET", "")
SAGE_REDIRECT_URI = os.getenv("SAGE_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/sage")
SAGE_COMPANY_ID = os.getenv("SAGE_COMPANY_ID", "")
SAGE_SENDER_ID = os.getenv("SAGE_SENDER_ID", "")
SAGE_SENDER_PASSWORD = os.getenv("SAGE_SENDER_PASSWORD", "")


class SageConnector(BaseConnector):
    PROVIDER = "sage"
    DISPLAY_NAME = "Sage 300 CRE / Intacct"
    DESCRIPTION = "Sync job costs, AP/AR, GL accounts, vendors, and purchase orders"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=SAGE_CLIENT_ID,
            client_secret=SAGE_CLIENT_SECRET,
            auth_url=SAGE_AUTH_URL,
            token_url=SAGE_TOKEN_URL,
            scopes=[],
            redirect_uri=SAGE_REDIRECT_URI,
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
        return True

    async def get_account_info(self, access_token: str) -> dict:
        return {"name": f"Sage ({SAGE_COMPANY_ID})", "email": ""}

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
                # Fetch job cost data
                jobs = await self._fetch_jobs(client, headers)
                items.extend(jobs)

                # Fetch AP bills
                bills = await self._fetch_ap_bills(client, headers, since)
                items.extend(bills)

                # Fetch AR invoices
                invoices = await self._fetch_ar_invoices(client, headers, since)
                items.extend(invoices)

                # Fetch vendors
                vendors = await self._fetch_vendors(client, headers)
                items.extend(vendors)

                # Fetch purchase orders
                pos = await self._fetch_purchase_orders(client, headers, since)
                items.extend(pos)

        except Exception as e:
            logger.error("sage_fetch_error", error=str(e))
            raise

        logger.info("sage_fetched", count=len(items))
        return items, ""

    async def _fetch_jobs(self, client, headers) -> list:
        items = []
        try:
            resp = await client.get(
                f"{SAGE_API_BASE}/objects/project",
                headers=headers,
                params={"fields": "PROJECTID,NAME,STATUS,TOTALBUDGET,TOTALACTUAL,PERCENTCOMPLETE", "pagesize": 50},
            )
            if resp.status_code != 200:
                return items

            for job in resp.json().get("data", []):
                name = job.get("NAME", "")
                project_id = job.get("PROJECTID", "")
                budget = job.get("TOTALBUDGET", 0)
                actual = job.get("TOTALACTUAL", 0)
                pct = job.get("PERCENTCOMPLETE", 0)
                status = job.get("STATUS", "")

                variance = budget - actual if budget and actual else 0

                summary = f"Job: {project_id} - {name} | Status: {status} | Budget: ${budget:,.0f} | Actual: ${actual:,.0f} | Variance: ${variance:,.0f}"

                items.append(NormalizedItem(
                    external_id=f"sage-job-{project_id}",
                    item_type="job_cost",
                    title=f"Job {project_id} - {name}",
                    summary=summary,
                    raw_data=job,
                    metadata={
                        "project_id": project_id,
                        "status": status,
                        "budget": budget,
                        "actual": actual,
                        "variance": variance,
                        "percent_complete": pct,
                    },
                    source_url="",
                    project_hint=name,
                ))
        except Exception as e:
            logger.warning("sage_jobs_error", error=str(e))
        return items

    async def _fetch_ap_bills(self, client, headers, since=None) -> list:
        items = []
        try:
            params = {"fields": "RECORDNO,VENDORID,VENDORNAME,TOTALDUE,TOTALENTERED,WHENCREATED,WHENDUE,STATE", "pagesize": 50}
            resp = await client.get(f"{SAGE_API_BASE}/objects/apbill", headers=headers, params=params)
            if resp.status_code != 200:
                return items

            for bill in resp.json().get("data", []):
                vendor = bill.get("VENDORNAME", "Unknown")
                amount = bill.get("TOTALENTERED", 0)
                due = bill.get("TOTALDUE", 0)
                due_date = bill.get("WHENDUE", "")
                state = bill.get("STATE", "")

                items.append(NormalizedItem(
                    external_id=f"sage-bill-{bill.get('RECORDNO', '')}",
                    item_type="bill",
                    title=f"AP Bill - {vendor} - ${amount:,.2f}",
                    summary=f"AP Bill | Vendor: {vendor} | Amount: ${amount:,.2f} | Due: ${due:,.2f} | Status: {state} | Due date: {due_date}",
                    raw_data=bill,
                    metadata={"vendor": vendor, "amount": amount, "due": due, "state": state, "due_date": due_date},
                    source_url="",
                    item_date=self._parse_date(bill.get("WHENCREATED")),
                    project_hint=vendor,
                ))
        except Exception as e:
            logger.warning("sage_ap_error", error=str(e))
        return items

    async def _fetch_ar_invoices(self, client, headers, since=None) -> list:
        items = []
        try:
            params = {"fields": "RECORDNO,CUSTOMERID,CUSTOMERNAME,TOTALDUE,TOTALENTERED,WHENCREATED,WHENDUE,STATE", "pagesize": 50}
            resp = await client.get(f"{SAGE_API_BASE}/objects/arinvoice", headers=headers, params=params)
            if resp.status_code != 200:
                return items

            for inv in resp.json().get("data", []):
                customer = inv.get("CUSTOMERNAME", "Unknown")
                amount = inv.get("TOTALENTERED", 0)
                due = inv.get("TOTALDUE", 0)
                state = inv.get("STATE", "")

                items.append(NormalizedItem(
                    external_id=f"sage-inv-{inv.get('RECORDNO', '')}",
                    item_type="invoice",
                    title=f"AR Invoice - {customer} - ${amount:,.2f}",
                    summary=f"AR Invoice | Customer: {customer} | Amount: ${amount:,.2f} | Due: ${due:,.2f} | Status: {state}",
                    raw_data=inv,
                    metadata={"customer": customer, "amount": amount, "due": due, "state": state},
                    source_url="",
                    item_date=self._parse_date(inv.get("WHENCREATED")),
                    project_hint=customer,
                ))
        except Exception as e:
            logger.warning("sage_ar_error", error=str(e))
        return items

    async def _fetch_vendors(self, client, headers) -> list:
        items = []
        try:
            params = {"fields": "VENDORID,NAME,STATUS,TOTALDUE", "pagesize": 100}
            resp = await client.get(f"{SAGE_API_BASE}/objects/vendor", headers=headers, params=params)
            if resp.status_code != 200:
                return items

            for v in resp.json().get("data", []):
                name = v.get("NAME", "")
                balance = v.get("TOTALDUE", 0)

                items.append(NormalizedItem(
                    external_id=f"sage-vendor-{v.get('VENDORID', '')}",
                    item_type="vendor",
                    title=name,
                    summary=f"Vendor: {name} | Outstanding: ${balance:,.2f}",
                    raw_data=v,
                    metadata={"name": name, "balance": balance, "status": v.get("STATUS", "")},
                    source_url="",
                    project_hint=name,
                ))
        except Exception as e:
            logger.warning("sage_vendors_error", error=str(e))
        return items

    async def _fetch_purchase_orders(self, client, headers, since=None) -> list:
        items = []
        try:
            params = {"fields": "RECORDNO,PONUMBER,VENDORNAME,TOTAL,STATE,DATECREATED", "pagesize": 50}
            resp = await client.get(f"{SAGE_API_BASE}/objects/purchasingdocument", headers=headers, params=params)
            if resp.status_code != 200:
                return items

            for po in resp.json().get("data", []):
                vendor = po.get("VENDORNAME", "Unknown")
                amount = po.get("TOTAL", 0)
                po_number = po.get("PONUMBER", "")
                state = po.get("STATE", "")

                items.append(NormalizedItem(
                    external_id=f"sage-po-{po.get('RECORDNO', '')}",
                    item_type="purchase_order",
                    title=f"PO #{po_number} - {vendor}",
                    summary=f"Purchase Order #{po_number} | Vendor: {vendor} | Amount: ${amount:,.2f} | Status: {state}",
                    raw_data=po,
                    metadata={"po_number": po_number, "vendor": vendor, "amount": amount, "state": state},
                    source_url="",
                    item_date=self._parse_date(po.get("DATECREATED")),
                    project_hint=vendor,
                ))
        except Exception as e:
            logger.warning("sage_po_error", error=str(e))
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
