"""
QuickBooks Connector -- Fetches financial data via QuickBooks Online API.

The realm_id (company ID) is stored in the connection's config_json
during the OAuth callback and used for all subsequent API calls.
"""

import os
import base64
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

INTUIT_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
INTUIT_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_API_BASE = "https://quickbooks.api.intuit.com/v3/company"
INTUIT_SANDBOX_API = "https://sandbox-quickbooks.api.intuit.com/v3/company"

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
            return resp.json()

    async def refresh_tokens(self, refresh_token: str) -> dict:
        config = self.get_oauth_config()
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
        return True

    async def get_account_info(self, access_token: str) -> dict:
        return {"name": "QuickBooks Company", "email": ""}

    async def fetch_data(
        self,
        access_token: str,
        since: datetime = None,
        cursor: str = "",
        connection_config: dict = None,
    ) -> tuple[list[NormalizedItem], str]:
        """
        Fetch invoices, expenses, and vendors from QuickBooks.
        realm_id comes from connection_config (stored during OAuth).
        """
        headers = self._headers(access_token)
        items = []
        api_base = self._api_base()

        # Get realm_id from connection config
        realm_id = ""
        if connection_config:
            realm_id = connection_config.get("realm_id", "")
        if not realm_id and cursor:
            realm_id = cursor.split("|")[0] if "|" in cursor else cursor

        if not realm_id:
            logger.error("quickbooks_no_realm_id")
            raise RuntimeError("QuickBooks realm_id not found. Please reconnect QuickBooks.")

        logger.info("quickbooks_fetching", realm_id=realm_id, api_base=api_base)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                invoices = await self._fetch_invoices(client, api_base, realm_id, headers, since)
                items.extend(invoices)

                vendors = await self._fetch_vendors(client, api_base, realm_id, headers)
                items.extend(vendors)

                expenses = await self._fetch_expenses(client, api_base, realm_id, headers, since)
                items.extend(expenses)

                customers = await self._fetch_customers(client, api_base, realm_id, headers)
                items.extend(customers)

                bills = await self._fetch_bills(client, api_base, realm_id, headers, since)
                items.extend(bills)

                payments = await self._fetch_payments(client, api_base, realm_id, headers, since)
                items.extend(payments)

                estimates = await self._fetch_estimates(client, api_base, realm_id, headers, since)
                items.extend(estimates)

                accounts = await self._fetch_accounts(client, api_base, realm_id, headers)
                items.extend(accounts)

        except RuntimeError:
            raise
        except Exception as e:
            logger.error("quickbooks_fetch_error", error=str(e))
            raise

        logger.info("quickbooks_fetched", count=len(items))
        return items, realm_id

    async def _fetch_invoices(self, client, api_base, realm_id, headers, since=None) -> list:
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
            logger.warning("quickbooks_invoice_error", error=str(e))
        return items

    async def _fetch_vendors(self, client, api_base, realm_id, headers) -> list:
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
            logger.warning("quickbooks_vendor_error", error=str(e))
        return items

    async def _fetch_expenses(self, client, api_base, realm_id, headers, since=None) -> list:
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
            logger.warning("quickbooks_expense_error", error=str(e))
        return items

    def _normalize_invoice(self, inv: dict) -> NormalizedItem:
        vendor_name = inv.get("CustomerRef", {}).get("name", "Unknown")
        amount = inv.get("TotalAmt", 0)
        balance = inv.get("Balance", 0)
        due_date = inv.get("DueDate", "")
        doc_number = inv.get("DocNumber", "")
        status = "Paid" if balance == 0 else "Open"

        lines = inv.get("Line", [])
        line_items = [f"{l.get('Description', '')}: ${l.get('Amount', 0):,.2f}" for l in lines if l.get("Description")]

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
            metadata={"doc_number": doc_number, "vendor": vendor_name, "amount": amount, "balance": balance, "status": status, "due_date": due_date},
            source_url=self._qbo_url("invoice", inv.get("Id", "")),
            project_hint=vendor_name,
        )

    def _normalize_vendor(self, vendor: dict) -> NormalizedItem:
        name = vendor.get("DisplayName", "Unknown")
        balance = vendor.get("Balance", 0)
        email = vendor.get("PrimaryEmailAddr", {}).get("Address", "") if vendor.get("PrimaryEmailAddr") else ""
        phone = vendor.get("PrimaryPhone", {}).get("FreeFormNumber", "") if vendor.get("PrimaryPhone") else ""

        summary = f"Vendor: {name}"
        if balance:
            summary += f" | Outstanding: ${balance:,.2f}"
        if email:
            summary += f" | {email}"

        return NormalizedItem(
            external_id=f"vendor-{vendor.get('Id', '')}",
            item_type="vendor",
            title=name,
            summary=summary,
            raw_data=vendor,
            metadata={"name": name, "balance": balance, "email": email, "phone": phone},
            source_url=self._qbo_url("vendor", vendor.get("Id", "")),
            project_hint=name,
        )

    def _normalize_expense(self, exp: dict) -> NormalizedItem:
        amount = exp.get("TotalAmt", 0)
        vendor_name = exp.get("EntityRef", {}).get("name", "Unknown") if exp.get("EntityRef") else "Unknown"
        account = exp.get("AccountRef", {}).get("name", "") if exp.get("AccountRef") else ""

        summary = f"Expense: ${amount:,.2f} | Vendor: {vendor_name}"
        if account:
            summary += f" | Account: {account}"

        return NormalizedItem(
            external_id=f"exp-{exp.get('Id', '')}",
            item_type="expense",
            title=f"${amount:,.2f} - {vendor_name}",
            summary=summary,
            raw_data=exp,
            metadata={"amount": amount, "vendor": vendor_name, "account": account},
            source_url=self._qbo_url("expense", exp.get("Id", "")),
            item_date=self._parse_date(exp.get("TxnDate")),
            project_hint=vendor_name,
        )

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"}

    def _api_base(self) -> str:
        return INTUIT_SANDBOX_API if QBO_USE_SANDBOX else QBO_API_BASE

    def _qbo_url(self, item_type: str, item_id: str, realm_id: str = "") -> str:
        """Build a deep link URL into QuickBooks Online."""
        base = "https://app.sandbox.qbo.intuit.com" if QBO_USE_SANDBOX else "https://app.qbo.intuit.com"
        type_map = {
            "invoice": "invoice",
            "bill": "bill",
            "expense": "expense",
            "estimate": "estimate",
            "payment": "recvpayment",
            "vendor": "vendordetail",
            "customer": "customerdetail",
        }
        qbo_type = type_map.get(item_type, item_type)
        return f"{base}/app/{qbo_type}?txnId={item_id}"

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            return None

    # ── Additional data type fetchers ──

    async def _fetch_customers(self, client, api_base, realm_id, headers) -> list:
        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": "SELECT * FROM Customer WHERE Active = true MAXRESULTS 100", "minorversion": "65"},
            )
            resp.raise_for_status()
            for cust in resp.json().get("QueryResponse", {}).get("Customer", []):
                name = cust.get("DisplayName", "Unknown")
                balance = cust.get("Balance", 0)
                email = cust.get("PrimaryEmailAddr", {}).get("Address", "") if cust.get("PrimaryEmailAddr") else ""
                phone = cust.get("PrimaryPhone", {}).get("FreeFormNumber", "") if cust.get("PrimaryPhone") else ""

                summary = f"Customer: {name}"
                if balance:
                    summary += f" | Balance: ${balance:,.2f}"
                if email:
                    summary += f" | {email}"

                items.append(NormalizedItem(
                    external_id=f"cust-{cust.get('Id', '')}",
                    item_type="customer",
                    title=name,
                    summary=summary,
                    raw_data=cust,
                    metadata={"name": name, "balance": balance, "email": email, "phone": phone},
                    source_url=self._qbo_url("customer", cust.get("Id", "")),
                    project_hint=name,
                ))
        except Exception as e:
            logger.warning("quickbooks_customer_error", error=str(e))
        return items

    async def _fetch_bills(self, client, api_base, realm_id, headers, since=None) -> list:
        query = "SELECT * FROM Bill ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        if since:
            query = f"SELECT * FROM Bill WHERE MetaData.LastUpdatedTime > '{since.strftime('%Y-%m-%d')}' ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": query, "minorversion": "65"},
            )
            resp.raise_for_status()
            for bill in resp.json().get("QueryResponse", {}).get("Bill", []):
                vendor = bill.get("VendorRef", {}).get("name", "Unknown") if bill.get("VendorRef") else "Unknown"
                amount = bill.get("TotalAmt", 0)
                balance = bill.get("Balance", 0)
                due_date = bill.get("DueDate", "")
                status = "Paid" if balance == 0 else "Open"

                lines = bill.get("Line", [])
                descriptions = [l.get("Description", "") for l in lines if l.get("Description")]

                summary = f"Bill | Vendor: {vendor} | ${amount:,.2f} | Status: {status} | Due: {due_date}"
                if descriptions:
                    summary += f"\nItems: {'; '.join(descriptions[:5])}"

                items.append(NormalizedItem(
                    external_id=f"bill-{bill.get('Id', '')}",
                    item_type="bill",
                    title=f"Bill - {vendor} - ${amount:,.2f}",
                    summary=summary,
                    raw_data=bill,
                    metadata={"vendor": vendor, "amount": amount, "balance": balance, "status": status, "due_date": due_date},
                    source_url=self._qbo_url("bill", bill.get("Id", "")),
                    item_date=self._parse_date(bill.get("TxnDate")),
                    project_hint=vendor,
                ))
        except Exception as e:
            logger.warning("quickbooks_bill_error", error=str(e))
        return items

    async def _fetch_payments(self, client, api_base, realm_id, headers, since=None) -> list:
        query = "SELECT * FROM Payment ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        if since:
            query = f"SELECT * FROM Payment WHERE MetaData.LastUpdatedTime > '{since.strftime('%Y-%m-%d')}' ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": query, "minorversion": "65"},
            )
            resp.raise_for_status()
            for pmt in resp.json().get("QueryResponse", {}).get("Payment", []):
                customer = pmt.get("CustomerRef", {}).get("name", "Unknown") if pmt.get("CustomerRef") else "Unknown"
                amount = pmt.get("TotalAmt", 0)

                items.append(NormalizedItem(
                    external_id=f"pmt-{pmt.get('Id', '')}",
                    item_type="payment",
                    title=f"Payment - {customer} - ${amount:,.2f}",
                    summary=f"Payment received | Customer: {customer} | Amount: ${amount:,.2f}",
                    raw_data=pmt,
                    metadata={"customer": customer, "amount": amount},
                    source_url=self._qbo_url("payment", pmt.get("Id", "")),
                    item_date=self._parse_date(pmt.get("TxnDate")),
                    project_hint=customer,
                ))
        except Exception as e:
            logger.warning("quickbooks_payment_error", error=str(e))
        return items

    async def _fetch_estimates(self, client, api_base, realm_id, headers, since=None) -> list:
        query = "SELECT * FROM Estimate ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        if since:
            query = f"SELECT * FROM Estimate WHERE MetaData.LastUpdatedTime > '{since.strftime('%Y-%m-%d')}' ORDERBY MetaData.CreateTime DESC MAXRESULTS 50"
        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": query, "minorversion": "65"},
            )
            resp.raise_for_status()
            for est in resp.json().get("QueryResponse", {}).get("Estimate", []):
                customer = est.get("CustomerRef", {}).get("name", "Unknown") if est.get("CustomerRef") else "Unknown"
                amount = est.get("TotalAmt", 0)
                status = est.get("TxnStatus", "")
                doc_number = est.get("DocNumber", "")

                items.append(NormalizedItem(
                    external_id=f"est-{est.get('Id', '')}",
                    item_type="estimate",
                    title=f"Estimate #{doc_number} - {customer}",
                    summary=f"Estimate #{doc_number} | Customer: {customer} | Amount: ${amount:,.2f} | Status: {status}",
                    raw_data=est,
                    metadata={"doc_number": doc_number, "customer": customer, "amount": amount, "status": status},
                    source_url=self._qbo_url("estimate", est.get("Id", "")),
                    item_date=self._parse_date(est.get("TxnDate")),
                    project_hint=customer,
                ))
        except Exception as e:
            logger.warning("quickbooks_estimate_error", error=str(e))
        return items

    async def _fetch_accounts(self, client, api_base, realm_id, headers) -> list:
        items = []
        try:
            resp = await client.get(
                f"{api_base}/{realm_id}/query",
                headers=headers,
                params={"query": "SELECT * FROM Account WHERE Active = true MAXRESULTS 100", "minorversion": "65"},
            )
            resp.raise_for_status()
            for acct in resp.json().get("QueryResponse", {}).get("Account", []):
                name = acct.get("Name", "Unknown")
                acct_type = acct.get("AccountType", "")
                balance = acct.get("CurrentBalance", 0)

                items.append(NormalizedItem(
                    external_id=f"acct-{acct.get('Id', '')}",
                    item_type="account",
                    title=name,
                    summary=f"Account: {name} | Type: {acct_type} | Balance: ${balance:,.2f}",
                    raw_data=acct,
                    metadata={"name": name, "type": acct_type, "balance": balance},
                    source_url="",
                    project_hint=name,
                ))
        except Exception as e:
            logger.warning("quickbooks_account_error", error=str(e))
        return items
