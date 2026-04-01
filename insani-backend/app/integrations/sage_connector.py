"""
Sage Intacct Connector — Fetches construction accounting data via Sage Intacct API.

Sage Intacct uses XML-based Web Services API with session-based authentication.
Authentication requires sender credentials (company-level) and user credentials.

Data extracted:
- Projects (job cost data)
- AP Bills (accounts payable)
- AR Invoices (accounts receivable)
- Vendors
- Purchase Orders

Sage Intacct API docs: https://developer.intacct.com/web-services/
"""

import os
from datetime import datetime, timezone

import httpx
import structlog

from app.integrations.base import BaseConnector, OAuthConfig, NormalizedItem

logger = structlog.get_logger()

SAGE_API_ENDPOINT = "https://api.intacct.com/ia/xml/xmlgw.phtml"

SAGE_SENDER_ID = os.getenv("SAGE_SENDER_ID", "")
SAGE_SENDER_PASSWORD = os.getenv("SAGE_SENDER_PASSWORD", "")
SAGE_COMPANY_ID = os.getenv("SAGE_COMPANY_ID", "")
SAGE_USER_ID = os.getenv("SAGE_USER_ID", "")
SAGE_USER_PASSWORD = os.getenv("SAGE_USER_PASSWORD", "")

# OAuth is not standard for Sage Intacct — it uses session-based XML auth
SAGE_CLIENT_ID = os.getenv("SAGE_CLIENT_ID", "")
SAGE_CLIENT_SECRET = os.getenv("SAGE_CLIENT_SECRET", "")
SAGE_REDIRECT_URI = os.getenv("SAGE_REDIRECT_URI", "http://localhost:8000/v1/integrations/callback/sage")


def _build_xml_request(function_xml: str, session_id: str = "") -> str:
    """Build a Sage Intacct XML API request."""
    if session_id:
        auth_xml = f"<sessionid>{session_id}</sessionid>"
    else:
        auth_xml = f"""<login>
            <userid>{SAGE_USER_ID}</userid>
            <companyid>{SAGE_COMPANY_ID}</companyid>
            <password>{SAGE_USER_PASSWORD}</password>
        </login>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<request>
    <control>
        <senderid>{SAGE_SENDER_ID}</senderid>
        <password>{SAGE_SENDER_PASSWORD}</password>
        <controlid>insani-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}</controlid>
        <uniqueid>false</uniqueid>
        <dtdversion>3.0</dtdversion>
        <includewhitespace>false</includewhitespace>
    </control>
    <operation>
        <authentication>
            {auth_xml}
        </authentication>
        <content>
            {function_xml}
        </content>
    </operation>
</request>"""


def _parse_xml_value(xml_text: str, tag: str) -> str:
    """Simple XML tag value extractor — no external XML library needed."""
    import re
    match = re.search(f"<{tag}>(.*?)</{tag}>", xml_text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_xml_records(xml_text: str, record_tag: str) -> list[dict]:
    """Extract a list of records from XML response."""
    import re
    records = []
    pattern = f"<{record_tag}>(.*?)</{record_tag}>"
    for match in re.finditer(pattern, xml_text, re.DOTALL):
        record_xml = match.group(1)
        record = {}
        for field_match in re.finditer(r"<(\w+)>(.*?)</\1>", record_xml, re.DOTALL):
            record[field_match.group(1)] = field_match.group(2).strip()
        if record:
            records.append(record)
    return records


class SageConnector(BaseConnector):
    PROVIDER = "sage"
    DISPLAY_NAME = "Sage Intacct / 300 CRE"
    DESCRIPTION = "Sync job costs, invoices, bills, vendors, and purchase orders"

    def get_oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            client_id=SAGE_CLIENT_ID,
            client_secret=SAGE_CLIENT_SECRET,
            auth_url="",
            token_url="",
            scopes=[],
            redirect_uri=SAGE_REDIRECT_URI,
        )

    def get_auth_url(self, state: str) -> str:
        # Sage Intacct doesn't use standard OAuth — uses XML session auth
        return ""

    async def exchange_code(self, code: str) -> dict:
        # Not applicable — use session-based auth instead
        session_id = await self._get_session()
        if session_id:
            return {
                "access_token": session_id,
                "token_type": "session",
                "expires_in": 3600,
            }
        raise RuntimeError("Failed to create Sage Intacct session")

    async def refresh_tokens(self, refresh_token: str) -> dict:
        session_id = await self._get_session()
        if session_id:
            return {"access_token": session_id, "token_type": "session", "expires_in": 3600}
        raise RuntimeError("Failed to refresh Sage Intacct session")

    async def _get_session(self) -> str | None:
        """Get a session ID from Sage Intacct using login credentials."""
        xml = _build_xml_request("""
            <function controlid="getSession">
                <getAPISession />
            </function>
        """)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    SAGE_API_ENDPOINT,
                    content=xml,
                    headers={"Content-Type": "application/xml"},
                )
                if resp.status_code == 200:
                    session_id = _parse_xml_value(resp.text, "sessionid")
                    if session_id:
                        return session_id
                logger.warning("sage_session_failed", status=resp.status_code, body=resp.text[:300])
        except Exception as e:
            logger.error("sage_session_error", error=str(e))
        return None

    async def test_connection(self, access_token: str) -> bool:
        if not SAGE_SENDER_ID or not SAGE_COMPANY_ID:
            logger.warning("sage_missing_credentials")
            return False

        xml = _build_xml_request("""
            <function controlid="testConn">
                <readByQuery>
                    <object>COMPANY</object>
                    <fields>COMPANYID,NAME</fields>
                    <query></query>
                    <pagesize>1</pagesize>
                </readByQuery>
            </function>
        """, session_id=access_token)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    SAGE_API_ENDPOINT,
                    content=xml,
                    headers={"Content-Type": "application/xml"},
                )
                return resp.status_code == 200 and "<status>success</status>" in resp.text.lower()
        except Exception:
            return False

    async def get_account_info(self, access_token: str) -> dict:
        return {
            "email": SAGE_USER_ID or "",
            "name": f"Sage Intacct ({SAGE_COMPANY_ID})",
        }

    async def fetch_data(
        self,
        access_token: str,
        since: datetime = None,
        cursor: str = "",
        connection_config: dict = None,
    ) -> tuple[list[NormalizedItem], str]:
        items = []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Fetch projects/jobs
                projects = await self._query(client, access_token, "PROJECT",
                    "PROJECTID,NAME,STATUS,TOTALBUDGET,TOTALACTUAL,PROJECTCATEGORY,BEGINDATE,ENDDATE")
                for proj in projects:
                    budget = proj.get("TOTALBUDGET", "0")
                    actual = proj.get("TOTALACTUAL", "0")
                    items.append(NormalizedItem(
                        external_id=f"sage-proj-{proj.get('PROJECTID', '')}",
                        item_type="job_cost",
                        title=f"Project: {proj.get('NAME', '')}",
                        summary=f"Project {proj.get('PROJECTID', '')} | {proj.get('NAME', '')} | Status: {proj.get('STATUS', '')} | Budget: ${budget} | Actual: ${actual}",
                        raw_data=proj,
                        metadata={
                            "project_id": proj.get("PROJECTID", ""),
                            "status": proj.get("STATUS", ""),
                            "budget": budget,
                            "actual": actual,
                            "category": proj.get("PROJECTCATEGORY", ""),
                        },
                        source_url="",
                        item_date=self._parse_date(proj.get("BEGINDATE")),
                        project_hint=proj.get("NAME", ""),
                    ))

                # Fetch AP bills
                bills = await self._query(client, access_token, "APBILL",
                    "RECORDNO,VENDORID,VENDORNAME,TOTALDUE,TOTALENTERED,STATE,WHENCREATED,WHENDUE,DESCRIPTION")
                for bill in bills:
                    items.append(NormalizedItem(
                        external_id=f"sage-bill-{bill.get('RECORDNO', '')}",
                        item_type="bill",
                        title=f"AP Bill: {bill.get('VENDORNAME', '')}",
                        summary=f"Bill #{bill.get('RECORDNO', '')} | Vendor: {bill.get('VENDORNAME', '')} | Due: ${bill.get('TOTALDUE', '0')} | Status: {bill.get('STATE', '')}",
                        raw_data=bill,
                        metadata={
                            "vendor": bill.get("VENDORNAME", ""),
                            "amount": bill.get("TOTALDUE", "0"),
                            "status": bill.get("STATE", ""),
                            "due_date": bill.get("WHENDUE", ""),
                        },
                        source_url="",
                        item_date=self._parse_date(bill.get("WHENCREATED")),
                        project_hint=bill.get("DESCRIPTION", ""),
                    ))

                # Fetch AR invoices
                invoices = await self._query(client, access_token, "ARINVOICE",
                    "RECORDNO,CUSTOMERID,CUSTOMERNAME,TOTALDUE,TOTALENTERED,STATE,WHENCREATED,WHENDUE,DESCRIPTION")
                for inv in invoices:
                    items.append(NormalizedItem(
                        external_id=f"sage-inv-{inv.get('RECORDNO', '')}",
                        item_type="invoice",
                        title=f"AR Invoice: {inv.get('CUSTOMERNAME', '')}",
                        summary=f"Invoice #{inv.get('RECORDNO', '')} | Customer: {inv.get('CUSTOMERNAME', '')} | Amount: ${inv.get('TOTALENTERED', '0')} | Due: ${inv.get('TOTALDUE', '0')}",
                        raw_data=inv,
                        metadata={
                            "customer": inv.get("CUSTOMERNAME", ""),
                            "amount": inv.get("TOTALENTERED", "0"),
                            "due": inv.get("TOTALDUE", "0"),
                            "status": inv.get("STATE", ""),
                        },
                        source_url="",
                        item_date=self._parse_date(inv.get("WHENCREATED")),
                        project_hint=inv.get("DESCRIPTION", ""),
                    ))

                # Fetch vendors
                vendors = await self._query(client, access_token, "VENDOR",
                    "VENDORID,NAME,STATUS,DISPLAYCONTACT.EMAIL1,DISPLAYCONTACT.PHONE1")
                for v in vendors:
                    items.append(NormalizedItem(
                        external_id=f"sage-vendor-{v.get('VENDORID', '')}",
                        item_type="vendor",
                        title=f"Vendor: {v.get('NAME', '')}",
                        summary=f"Vendor {v.get('VENDORID', '')} | {v.get('NAME', '')} | Status: {v.get('STATUS', '')}",
                        raw_data=v,
                        metadata={"vendor_id": v.get("VENDORID", ""), "status": v.get("STATUS", "")},
                        source_url="",
                        item_date=None,
                        project_hint=v.get("NAME", ""),
                    ))

        except Exception as e:
            logger.error("sage_fetch_error", error=str(e))
            raise

        logger.info("sage_fetched", count=len(items))
        return items, ""

    async def _query(self, client, session_id: str, obj: str, fields: str, page_size: int = 100) -> list:
        """Execute a Sage Intacct readByQuery and parse results."""
        xml = _build_xml_request(f"""
            <function controlid="query_{obj}">
                <readByQuery>
                    <object>{obj}</object>
                    <fields>{fields}</fields>
                    <query></query>
                    <pagesize>{page_size}</pagesize>
                </readByQuery>
            </function>
        """, session_id=session_id)

        try:
            resp = await client.post(
                SAGE_API_ENDPOINT,
                content=xml,
                headers={"Content-Type": "application/xml"},
            )
            if resp.status_code != 200:
                logger.warning("sage_query_failed", object=obj, status=resp.status_code)
                return []

            if "<status>failure</status>" in resp.text.lower():
                error = _parse_xml_value(resp.text, "description2") or _parse_xml_value(resp.text, "errormessage")
                logger.warning("sage_query_error", object=obj, error=error[:200])
                return []

            # Parse records from the response
            records = _parse_xml_records(resp.text, obj.lower())
            if not records:
                records = _parse_xml_records(resp.text, obj)
            logger.info("sage_query_result", object=obj, records=len(records))
            return records

        except Exception as e:
            logger.warning("sage_query_exception", object=obj, error=str(e))
            return []

    def _parse_date(self, date_str) -> datetime | None:
        if not date_str:
            return None
        try:
            if "T" in str(date_str):
                return datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).replace(tzinfo=None)
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                try:
                    return datetime.strptime(str(date_str)[:10], fmt)
                except ValueError:
                    continue
        except Exception:
            pass
        return None
