"""
Chat Service — Session and message persistence via SQLAlchemy.

All database operations for chat sessions and messages.
Uses ORM queries instead of raw SQL.
"""

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.db_models import ChatSession, ChatMessage


async def create_session(db: AsyncSession, user_id: int, project_id: int, title: str = "New conversation", org_id: int = None) -> ChatSession:
    """Create a new chat session. Returns the session object."""
    session = ChatSession(user_id=user_id, project_id=project_id, title=title, org_id=org_id)
    db.add(session)
    await db.flush()  # Get the auto-generated ID without committing
    return session


async def update_session_title(db: AsyncSession, session_id: int, title: str):
    """Update a session's title."""
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if session:
        session.title = title


async def save_message(db: AsyncSession, session_id: int, role: str, content: str, files: list = None) -> ChatMessage:
    """Save a message to a chat session. Returns the message object."""
    msg = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        files_json=files or []
    )
    db.add(msg)
    await db.flush()
    return msg


async def get_session_with_messages(db: AsyncSession, session_id: int, user_id: int) -> ChatSession | None:
    """Load a session with all its messages. Returns None if not found or not owned by user."""
    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_sessions(db: AsyncSession, user_id: int, project_id: int = None, limit: int = 20) -> list[dict]:
    """Get user's chat sessions with message counts, most recent first."""
    query = (
        select(
            ChatSession.id,
            ChatSession.project_id,
            ChatSession.title,
            ChatSession.created_at,
            ChatSession.updated_at,
            func.count(ChatMessage.id).label("message_count")
        )
        .outerjoin(ChatMessage)
        .where(ChatSession.user_id == user_id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    )
    if project_id:
        query = query.where(ChatSession.project_id == project_id)

    result = await db.execute(query)
    rows = result.all()
    return [
        {
            "id": r.id,
            "project_id": r.project_id,
            "title": r.title,
            "created_at": str(r.created_at) if r.created_at else None,
            "updated_at": str(r.updated_at) if r.updated_at else None,
            "message_count": r.message_count,
        }
        for r in rows
    ]


async def delete_session(db: AsyncSession, session_id: int, user_id: int) -> bool:
    """Delete a session and all its messages. Returns True if deleted."""
    result = await db.execute(
        delete(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )
    return result.rowcount > 0


async def get_conversation_history(db: AsyncSession, session_id: int, limit: int = 20) -> list[dict]:
    """Get recent messages formatted for the Claude API: [{role, content}, ...]."""
    result = await db.execute(
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    messages = [{"role": r.role, "content": r.content} for r in rows]
    messages.reverse()  # Chronological order for Claude
    return messages
