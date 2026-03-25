"""
Connector Registry — Maps provider names to connector instances.

To add a new integration:
1. Create a connector class in app/integrations/ that extends BaseConnector
2. Import it here and add to CONNECTORS dict
"""

from app.integrations.base import BaseConnector

# Lazy imports to avoid circular dependencies
_connectors = {}


def _load_connectors():
    if _connectors:
        return
    from app.integrations.gmail_connector import GmailConnector
    _connectors["gmail"] = GmailConnector()
    from app.integrations.quickbooks_connector import QuickBooksConnector
    _connectors["quickbooks"] = QuickBooksConnector()
    # Future connectors:
    # from app.integrations.procore_connector import ProcoreConnector
    # _connectors["procore"] = ProcoreConnector()
    # from app.integrations.autodesk_connector import AutodeskConnector
    # _connectors["autodesk"] = AutodeskConnector()


def get_connector(provider: str) -> BaseConnector | None:
    """Get the connector instance for a provider."""
    _load_connectors()
    return _connectors.get(provider)


def list_providers() -> list[dict]:
    """List all available integration providers with their display info."""
    _load_connectors()
    return [
        {
            "provider": c.PROVIDER,
            "name": c.DISPLAY_NAME,
            "description": c.DESCRIPTION,
        }
        for c in _connectors.values()
    ]
