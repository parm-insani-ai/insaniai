"""
Base Connector — Abstract interface for all integrations.

Every integration (Gmail, QuickBooks, Procore, Autodesk) implements
this interface. The framework calls these methods generically so
adding a new integration is just writing a new connector class.

To add a new integration:
1. Create a file in app/integrations/ (e.g., procore.py)
2. Subclass BaseConnector and implement all abstract methods
3. Register it in CONNECTORS dict in app/integrations/registry.py
4. Add OAuth config to .env
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from datetime import datetime


@dataclass
class OAuthConfig:
    """OAuth 2.0 configuration for a provider."""
    client_id: str
    client_secret: str
    auth_url: str           # Where to redirect user for consent
    token_url: str          # Where to exchange code for tokens
    scopes: list[str]       # Requested permissions
    redirect_uri: str       # Our callback URL


@dataclass
class SyncResult:
    """Result of a sync operation."""
    items_fetched: int = 0
    items_created: int = 0
    items_updated: int = 0
    errors: list[str] = None
    new_cursor: str = ""     # Updated pagination cursor

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


@dataclass
class NormalizedItem:
    """
    A single data item normalized from any source.
    This is the common format that all connectors produce.
    The AI context builder consumes these.
    """
    external_id: str          # ID in the source system
    item_type: str            # email, invoice, rfi, submittal, etc.
    title: str                # Display title
    summary: str              # Text summary for AI consumption
    raw_data: dict            # Full raw data from provider
    metadata: dict            # Structured metadata (dates, people, amounts)
    source_url: str = ""      # Deep link back to source
    item_date: datetime = None
    project_hint: str = ""    # Hint for matching to a project (e.g., project name in subject)


class BaseConnector(ABC):
    """
    Abstract base class for all integration connectors.
    Implement all abstract methods for each new provider.
    """

    # Provider identifier (gmail, quickbooks, procore, autodesk)
    PROVIDER: str = ""

    # Human-readable name
    DISPLAY_NAME: str = ""

    # Description shown in the integrations UI
    DESCRIPTION: str = ""

    @abstractmethod
    def get_oauth_config(self) -> OAuthConfig:
        """Return OAuth configuration for this provider."""
        pass

    @abstractmethod
    def get_auth_url(self, state: str) -> str:
        """
        Build the full OAuth authorization URL.
        state: CSRF token to verify the callback.
        Returns the URL to redirect the user to.
        """
        pass

    @abstractmethod
    async def exchange_code(self, code: str) -> dict:
        """
        Exchange an OAuth authorization code for tokens.
        Returns: {"access_token": "...", "refresh_token": "...", "expires_in": 3600, ...}
        """
        pass

    @abstractmethod
    async def refresh_tokens(self, refresh_token: str) -> dict:
        """
        Refresh an expired access token.
        Returns: {"access_token": "...", "expires_in": 3600, ...}
        """
        pass

    @abstractmethod
    async def fetch_data(
        self,
        access_token: str,
        since: datetime = None,
        cursor: str = "",
    ) -> tuple[list[NormalizedItem], str]:
        """
        Fetch data from the provider's API.
        
        Args:
            access_token: Valid OAuth access token
            since: Only fetch items created/modified after this time
            cursor: Pagination cursor from previous fetch
            
        Returns:
            (items, new_cursor) — list of normalized items + cursor for next page
        """
        pass

    @abstractmethod
    async def test_connection(self, access_token: str) -> bool:
        """Test if the connection is still valid. Returns True if healthy."""
        pass

    @abstractmethod
    async def get_account_info(self, access_token: str) -> dict:
        """Get info about the connected account (email, name, etc.)."""
        pass
