"""
Chat Router — Session management with tenant isolation and pagination.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.db_models import ChatSession, ChatMessage
from app.models.schemas_chat import ChatSessionResponse, ChatSessionListItem, ChatMessageResponse
from app.middleware.auth import require_auth_context, AuthContext

router = APIRouter(prefix="/v1/chat", tags=["Chat"])


@router.get("/sessions", response_model=list[ChatSessionListItem])
async def list_sessions(
    project_id: int | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """List chat sessions with pagination. Scoped to user's org."""
    query = (
        select(
            ChatSession.id,
            ChatSession.project_id,
            ChatSession.title,
            ChatSession.created_at,
            ChatSession.updated_at,
            func.count(ChatMessage.id).label("message_count"),
        )
        .outerjoin(ChatMessage)
        .where(
            ChatSession.user_id == ctx.user_id,
            ChatSession.org_id == ctx.org_id,  # Tenant boundary
        )
        .group_by(ChatSession.id)
        .order_by(ChatSession.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    if project_id:
        query = query.where(ChatSession.project_id == project_id)

    result = await db.execute(query)
    rows = result.all()
    return [
        ChatSessionListItem(
            id=r.id, project_id=r.project_id, title=r.title,
            created_at=str(r.created_at) if r.created_at else None,
            updated_at=str(r.updated_at) if r.updated_at else None,
            message_count=r.message_count,
        )
        for r in rows
    ]


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_session(
    session_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get a session with all messages. Enforces tenant boundary."""
    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(
            ChatSession.id == session_id,
            ChatSession.user_id == ctx.user_id,
            ChatSession.org_id == ctx.org_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return ChatSessionResponse(
        id=session.id,
        user_id=session.user_id,
        project_id=session.project_id,
        title=session.title,
        created_at=str(session.created_at) if session.created_at else None,
        updated_at=str(session.updated_at) if session.updated_at else None,
        messages=[
            ChatMessageResponse(
                id=m.id, session_id=m.session_id, role=m.role,
                content=m.content, files_json=m.files_json or [],
                created_at=str(m.created_at) if m.created_at else None,
            )
            for m in session.messages
        ]
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Delete a session. Enforces tenant boundary."""
    result = await db.execute(
        delete(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == ctx.user_id,
            ChatSession.org_id == ctx.org_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}
