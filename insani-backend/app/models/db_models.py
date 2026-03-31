"""
Database Models — SQLAlchemy ORM table definitions.

Phase 3 additions:
- Organization: tenant isolation — every user belongs to an org,
  every project belongs to an org. Queries filter by org_id so
  users can never see another org's data.
- RefreshToken: long-lived tokens that issue short-lived JWTs.
  Stored in DB so they can be revoked.
- ResponseCache: cache AI responses keyed by project_id + query hash.
  Avoids redundant Claude calls for identical questions.
- token_count on ChatMessage: tracks Claude token usage per message
  for cost monitoring and context window management.
"""

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey,
    Index, JSON, Boolean, BigInteger, UniqueConstraint
)
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════
# ORGANIZATION — Tenant isolation
# ═══════════════════════════════════════════════

class Organization(Base):
    """
    Tenant boundary. All data is scoped to an org.
    Users see only their org's projects, chats, and data.
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)  # URL-safe identifier
    plan = Column(String(50), default="free")      # free, pro, enterprise
    max_users = Column(Integer, default=10)
    max_projects = Column(Integer, default=5)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    users = relationship("User", back_populates="organization")
    projects = relationship("Project", back_populates="organization")


# ═══════════════════════════════════════════════
# USER
# ═══════════════════════════════════════════════

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    role = Column(String(100), default="member")   # admin, member, viewer
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="users")
    projects_owned = relationship("Project", back_populates="owner")
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    memberships = relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")


# ═══════════════════════════════════════════════
# REFRESH TOKEN — Secure token rotation
# ═══════════════════════════════════════════════

class RefreshToken(Base):
    """
    Long-lived refresh tokens stored in DB.
    - Access token (JWT): 15 minutes, stateless
    - Refresh token: 30 days, stored in DB, revocable
    
    When the access token expires, the client sends the
    refresh token to get a new access token. If the refresh
    token is revoked (logout, password change), the user
    must re-authenticate.
    """
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(255), unique=True, nullable=False, index=True)
    device_info = Column(String(255), default="")  # e.g., "Chrome on MacOS"
    is_revoked = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="refresh_tokens")


# ═══════════════════════════════════════════════
# PROJECT
# ═══════════════════════════════════════════════

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(100), default="")
    location = Column(String(255), default="")
    data_json = Column(JSON, default=dict)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="projects")
    owner = relationship("User", back_populates="projects_owned")
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(50), default="viewer")
    added_at = Column(DateTime, server_default=func.now())

    project = relationship("Project", back_populates="members")
    user = relationship("User", back_populates="memberships")

    __table_args__ = (Index("idx_member_user", "user_id"),)


# ═══════════════════════════════════════════════
# CHAT SESSION + MESSAGES
# ═══════════════════════════════════════════════

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), default="New conversation")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="chat_sessions")
    project = relationship("Project", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at")

    __table_args__ = (
        Index("idx_session_org", "org_id"),
        Index("idx_session_user", "user_id"),
        Index("idx_session_project", "project_id"),
        Index("idx_session_updated", "updated_at"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    files_json = Column(JSON, default=list)
    token_count = Column(Integer, default=0)   # Claude tokens used (for cost tracking)
    created_at = Column(DateTime, server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (Index("idx_message_session", "session_id"),)


# ═══════════════════════════════════════════════
# RESPONSE CACHE — Avoid redundant Claude calls
# ═══════════════════════════════════════════════

class ResponseCache(Base):
    """
    Cache AI responses keyed by project + query hash.
    
    When a user asks a question that's been asked before on the
    same project (and the project data hasn't changed), serve
    the cached response instead of calling Claude again.
    
    TTL-based: entries expire after cache_ttl_hours.
    Invalidated when project data_json is updated.
    """
    __tablename__ = "response_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    query_hash = Column(String(64), nullable=False, index=True)  # SHA-256 of normalized query
    query_text = Column(Text, nullable=False)                     # Original query for debugging
    response = Column(Text, nullable=False)                       # Cached response HTML
    token_count = Column(Integer, default=0)                      # Tokens saved by cache hit
    hit_count = Column(Integer, default=0)                        # Times this cache entry was used
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "query_hash", name="uq_cache_project_query"),
        Index("idx_cache_lookup", "project_id", "query_hash"),
        Index("idx_cache_expires", "expires_at"),
    )


# ═══════════════════════════════════════════════
# DOCUMENT — Uploaded files with parsed page content
# ═══════════════════════════════════════════════

class Document(Base):
    """
    An uploaded document (PDF, image, etc.) attached to a project.
    The raw file is stored on disk at file_path.
    Parsed text content is stored page-by-page in DocumentPage.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(500), nullable=False)          # Original filename
    file_path = Column(String(1000), nullable=False)         # Server storage path
    file_size = Column(Integer, default=0)                   # Bytes
    media_type = Column(String(100), default="application/pdf")
    doc_type = Column(String(50), default="general")          # general, drawing, specification, submittal
    page_count = Column(Integer, default=0)
    status = Column(String(50), default="processing")        # processing, ready, error
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    pages = relationship("DocumentPage", back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_number")

    __table_args__ = (
        Index("idx_doc_project", "project_id"),
        Index("idx_doc_org", "org_id"),
    )


class DocumentPage(Base):
    """
    One page of a parsed document. Stores the extracted text
    so the AI can cite specific pages. Used to build the
    context sent to Claude with page markers.
    """
    __tablename__ = "document_pages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=False)   # 1-indexed
    text_content = Column(Text, default="")          # Extracted text from this page
    char_offset = Column(Integer, default=0)         # Character offset from start of doc (for positioning)
    image_path = Column(String(1000), default="")    # Path to rendered page image (for blueprints)
    drawing_type = Column(String(50), default="")    # floor_plan, structural, electrical, etc.

    document = relationship("Document", back_populates="pages")

    __table_args__ = (
        Index("idx_page_doc", "document_id"),
    )


# ═══════════════════════════════════════════════
# DRAWING ANALYSIS — Cached vision analysis of blueprint pages
# ═══════════════════════════════════════════════

class DrawingAnalysis(Base):
    """
    Caches Claude's vision analysis of a single drawing page.
    Avoids re-sending expensive images for repeated questions
    about the same sheet. Each page can have multiple analysis
    types (general, dimensions, electrical, etc.).
    """
    __tablename__ = "drawing_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=False)
    analysis_type = Column(String(50), default="general")  # general, dimensions, electrical, structural, mep
    analysis_json = Column(JSON, default=dict)              # Structured extraction from Claude vision
    image_hash = Column(String(64), default="")             # SHA-256 of image bytes for cache invalidation
    token_cost = Column(Integer, default=0)                 # Tokens consumed for this analysis
    created_at = Column(DateTime, server_default=func.now())

    regions = relationship("DrawingRegion", back_populates="analysis", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_analysis_doc_page", "document_id", "page_number"),
        Index("idx_analysis_type", "document_id", "analysis_type"),
    )


class DrawingRegion(Base):
    """
    An identified region/element within a drawing page.
    Stores normalized bounding box coordinates (0.0-1.0) for
    visual back-referencing in the frontend viewer.
    """
    __tablename__ = "drawing_regions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_id = Column(Integer, ForeignKey("drawing_analyses.id", ondelete="CASCADE"), nullable=False)
    label = Column(String(255), nullable=False)      # "Lobby", "Electrical Panel EP-1"
    region_type = Column(String(50), default="")     # room, element, detail, callout
    bbox_x = Column(String(20), default="0")         # Normalized x (0.0-1.0), stored as string for SQLite compat
    bbox_y = Column(String(20), default="0")         # Normalized y
    bbox_w = Column(String(20), default="0")         # Normalized width
    bbox_h = Column(String(20), default="0")         # Normalized height
    metadata_json = Column(JSON, default=dict)       # {dimensions, material, notes, ...}

    analysis = relationship("DrawingAnalysis", back_populates="regions")

    __table_args__ = (
        Index("idx_region_analysis", "analysis_id"),
    )


# ═══════════════════════════════════════════════
# DISCREPANCY DETECTION — Spec vs Submittal comparison
# ═══════════════════════════════════════════════

class DiscrepancyReport(Base):
    """
    A comparison run between spec documents and submittal documents.
    Stores the overall summary and links to individual findings.
    """
    __tablename__ = "discrepancy_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(500), default="")
    status = Column(String(50), default="pending")           # pending, analyzing, complete, error
    spec_doc_ids = Column(JSON, default=list)                 # [doc_id, doc_id, ...]
    submittal_doc_ids = Column(JSON, default=list)            # [doc_id, doc_id, ...]
    summary = Column(Text, default="")                        # AI-generated overall summary
    discrepancy_count = Column(Integer, default=0)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())

    items = relationship("DiscrepancyItem", back_populates="report", cascade="all, delete-orphan", order_by="DiscrepancyItem.id")

    __table_args__ = (
        Index("idx_disc_report_project", "project_id"),
        Index("idx_disc_report_org", "org_id"),
    )


class DiscrepancyItem(Base):
    """
    A single discrepancy found between a spec and a submittal.
    """
    __tablename__ = "discrepancy_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey("discrepancy_reports.id", ondelete="CASCADE"), nullable=False)
    severity = Column(String(20), default="info")             # critical, major, minor, info
    category = Column(String(100), default="other")           # material_mismatch, dimension_mismatch, missing_item, non_compliant, other
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    spec_reference = Column(String(255), default="")          # "Spec Section 03 20 00, p. 12"
    spec_doc_id = Column(Integer, nullable=True)
    spec_page = Column(Integer, nullable=True)
    spec_excerpt = Column(Text, default="")
    submittal_reference = Column(String(255), default="")
    submittal_doc_id = Column(Integer, nullable=True)
    submittal_page = Column(Integer, nullable=True)
    submittal_excerpt = Column(Text, default="")
    recommendation = Column(Text, default="")
    status = Column(String(50), default="open")               # open, acknowledged, resolved, dismissed
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    report = relationship("DiscrepancyReport", back_populates="items")

    __table_args__ = (
        Index("idx_disc_item_report", "report_id"),
    )


# ═══════════════════════════════════════════════
# INTEGRATIONS — OAuth connections and synced data
# ═══════════════════════════════════════════════

class IntegrationConnection(Base):
    """
    An OAuth connection between an org and an external service.
    Stores encrypted tokens, sync state, and connection health.
    
    One row per org per provider (e.g., org 1 + gmail, org 1 + quickbooks).
    """
    __tablename__ = "integration_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    provider = Column(String(50), nullable=False)       # gmail, quickbooks, procore, autodesk
    status = Column(String(20), default="pending")       # pending, connected, error, revoked
    access_token_enc = Column(Text, default="")          # Encrypted OAuth access token
    refresh_token_enc = Column(Text, default="")         # Encrypted OAuth refresh token
    token_expires_at = Column(DateTime, nullable=True)
    scopes = Column(Text, default="")                    # Granted OAuth scopes
    external_account = Column(String(255), default="")   # e.g., user@gmail.com or company name
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(20), default="never") # never, success, error
    last_sync_error = Column(Text, default="")
    sync_cursor = Column(Text, default="")               # Provider-specific pagination cursor
    config_json = Column(JSON, default=dict)              # Provider-specific settings
    connected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("org_id", "provider", name="uq_org_provider"),
        Index("idx_integration_org", "org_id"),
    )


class SyncedItem(Base):
    """
    A normalized data item pulled from an external service.
    All integrations normalize their data into this common table
    so the AI context builder can query one place.
    
    item_type examples: email, invoice, rfi, submittal, change_order,
    clash_report, daily_log, drawing, expense, vendor
    """
    __tablename__ = "synced_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    connection_id = Column(Integer, ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False)        # gmail, quickbooks, procore, autodesk
    item_type = Column(String(50), nullable=False)       # email, invoice, rfi, etc.
    external_id = Column(String(255), nullable=False)    # ID in the source system
    title = Column(String(500), default="")               # Display title
    summary = Column(Text, default="")                    # Extracted/normalized text for AI
    raw_json = Column(JSON, default=dict)                 # Full raw data from provider
    metadata_json = Column(JSON, default=dict)            # Normalized metadata (dates, people, amounts)
    source_url = Column(String(1000), default="")         # Deep link back to source system
    item_date = Column(DateTime, nullable=True)           # When the item was created in the source
    synced_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_connection_external"),
        Index("idx_synced_org", "org_id"),
        Index("idx_synced_project", "project_id"),
        Index("idx_synced_type", "item_type"),
        Index("idx_synced_provider", "provider"),
        Index("idx_synced_date", "item_date"),
    )


class IntegrationSyncLog(Base):
    """
    Audit trail for sync operations. One row per sync run.
    Tracks what was synced, how many items, and any errors.
    """
    __tablename__ = "integration_sync_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(Integer, ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)          # started, success, partial, error
    items_fetched = Column(Integer, default=0)
    items_created = Column(Integer, default=0)
    items_updated = Column(Integer, default=0)
    error_message = Column(Text, default="")
    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_synclog_connection", "connection_id"),
    )
