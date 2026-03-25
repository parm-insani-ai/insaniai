"""
Connector Registry -- Maps provider names to connector instances.

All integrations:
- Gmail (emails, attachments)
- QuickBooks (invoices, expenses, vendors, accounts)
- Procore (RFIs, submittals, change orders, daily logs)
- Autodesk (models, issues, documents)
- Outlook / Office 365 (emails, calendar events)
- Dropbox (files, drawings, photos)
- SharePoint (documents, project files)
- Primavera P6 (schedules, activities, resources)
- Sage 300 CRE / Intacct (job costs, AP/AR, vendors, POs)
"""

from app.integrations.base import BaseConnector

_connectors = {}


def _load_connectors():
    if _connectors:
        return

    from app.integrations.gmail_connector import GmailConnector
    _connectors["gmail"] = GmailConnector()

    from app.integrations.quickbooks_connector import QuickBooksConnector
    _connectors["quickbooks"] = QuickBooksConnector()

    from app.integrations.procore_connector import ProcoreConnector
    _connectors["procore"] = ProcoreConnector()

    from app.integrations.autodesk_connector import AutodeskConnector
    _connectors["autodesk"] = AutodeskConnector()

    from app.integrations.outlook_connector import OutlookConnector
    _connectors["outlook"] = OutlookConnector()

    from app.integrations.dropbox_connector import DropboxConnector
    _connectors["dropbox"] = DropboxConnector()

    from app.integrations.sharepoint_connector import SharePointConnector
    _connectors["sharepoint"] = SharePointConnector()

    from app.integrations.primavera_connector import PrimaveraConnector
    _connectors["primavera"] = PrimaveraConnector()

    from app.integrations.sage_connector import SageConnector
    _connectors["sage"] = SageConnector()


def get_connector(provider: str) -> BaseConnector | None:
    _load_connectors()
    return _connectors.get(provider)


def list_providers() -> list[dict]:
    _load_connectors()
    return [
        {
            "provider": c.PROVIDER,
            "name": c.DISPLAY_NAME,
            "description": c.DESCRIPTION,
        }
        for c in _connectors.values()
    ]
