"""
AI Streaming Router — Server-Sent Events for real-time AI responses.

POST /v1/ai/stream — Streams tokens as they arrive from Claude.
"""

import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.db_models import Project, ChatSession
from app.models.schemas_chat import AiAskRequest
from app.services import chat_service, ai_service, document_service
from app.middleware.auth import require_auth_context, AuthContext
import structlog

logger = structlog.get_logger()

router = APIRouter(prefix="/v1/ai", tags=["AI Streaming"])


@router.post("/stream")
async def stream_ask(
    request: Request,
    body: AiAskRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream an AI response via Server-Sent Events.

    Events sent to client:
      event: session   → {"session_id": 123, "title": "..."}
      event: token     → {"text": "The "}
      event: done      → {"full_response": "<p>The RFI...</p>"}
      event: error     → {"message": "..."}
    """

    # ── Session management ──
    session_id = body.session_id

    if not session_id:
        title = body.message[:50] if len(body.message) <= 50 else body.message[:50] + "..."
        session = await chat_service.create_session(db, ctx.user_id, body.project_id, title, ctx.org_id)
        session_id = session.id
        await db.commit()
    else:
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == ctx.user_id,
                ChatSession.org_id == ctx.org_id,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Session not found")

    # ── Load project with tenant check ──
    result = await db.execute(
        select(Project).where(
            Project.id == body.project_id,
            Project.org_id == ctx.org_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_data = project.data_json or {}

    # ── Load document context ──
    doc_context = ""
    project_docs = await document_service.get_project_documents(db, body.project_id, ctx.org_id)
    ready_docs = [d for d in project_docs if d.status == "ready"]
    if ready_docs:
        loaded_docs = []
        for d in ready_docs[:10]:
            full_doc = await document_service.get_document_with_pages(db, d.id, ctx.org_id)
            if full_doc:
                loaded_docs.append(full_doc)
        doc_context = document_service.build_document_context(loaded_docs)

    # ── Save user message ──
    file_meta = [{"name": f.get("name", "file")} for f in body.files] if body.files else []
    await chat_service.save_message(db, session_id, "user", body.message, file_meta)
    await db.commit()

    # ── Get history ──
    history = await chat_service.get_conversation_history(db, session_id, limit=20)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    # ── Get session title ──
    title_result = await db.execute(select(ChatSession.title).where(ChatSession.id == session_id))
    row = title_result.one_or_none()
    title = row.title if row else "Chat"

    # ── Stream generator ──
    async def generate():
        full_response = ""

        yield f"event: session\ndata: {json.dumps({'session_id': session_id, 'title': title})}\n\n"

        try:
            async for chunk in ai_service.stream_claude(
                message=body.message,
                project_data=project_data,
                conversation_history=history,
                files=body.files or None,
                document_context=doc_context,
            ):
                full_response += chunk
                yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"

            formatted = ai_service.format_response(full_response)

            # Save to DB using a fresh session
            async with get_db_session() as save_db:
                await chat_service.save_message(save_db, session_id, "assistant", formatted)
                await save_db.commit()

            yield f"event: done\ndata: {json.dumps({'full_response': formatted})}\n\n"

        except Exception as e:
            logger.error("stream_error", error=str(e), session_id=session_id)
            error_msg = "AI service encountered an error. Please try again."
            yield f"event: error\ndata: {json.dumps({'message': error_msg})}\n\n"

            try:
                async with get_db_session() as save_db:
                    await chat_service.save_message(save_db, session_id, "assistant", f"Error: {error_msg}")
                    await save_db.commit()
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


async def get_db_session():
    """Get a standalone async session for use inside generators."""
    from app.db import async_session
    return async_session()
