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

    _safe_load("gmail", "app.integrations.gmail_connector", "GmailConnector")
    _safe_load("quickbooks", "app.integrations.quickbooks_connector", "QuickBooksConnector")
    _safe_load("procore", "app.integrations.procore_connector", "ProcoreConnector")
    _safe_load("autodesk", "app.integrations.autodesk_connector", "AutodeskConnector")
    _safe_load("outlook", "app.integrations.outlook_connector", "OutlookConnector")
    _safe_load("dropbox", "app.integrations.dropbox_connector", "DropboxConnector")
    _safe_load("sharepoint", "app.integrations.sharepoint_connector", "SharePointConnector")
    _safe_load("primavera", "app.integrations.primavera_connector", "PrimaveraConnector")
    _safe_load("sage", "app.integrations.sage_connector", "SageConnector")


def _safe_load(name, module_path, class_name):
    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        _connectors[name] = cls()
    except Exception as e:
        import structlog
        structlog.get_logger().error("connector_load_failed", connector=name, error=str(e))


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
