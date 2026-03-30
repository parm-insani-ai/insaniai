"""
AI Router -- Secure Claude API proxy with caching, token management,
and integration data injection.

POST /v1/ai/ask -- Non-streaming. Checks cache first, manages token budget.
Rate limited to 20/minute per IP.
"""

import os
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
import structlog

from app.db import get_db
from app.models.db_models import Project, ChatSession
from app.models.schemas_chat import AiAskRequest, AiAskResponse
from app.services import chat_service, ai_service, cache_service, token_service, document_service
from app.middleware.auth import require_auth_context, AuthContext

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/v1/ai", tags=["AI"])
logger = structlog.get_logger()


@router.post("/ask", response_model=AiAskResponse)
@limiter.limit("20/minute")
async def ask(
    request: Request,
    body: AiAskRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    # -- 1. Session + project with tenant isolation --
    session_id = body.session_id

    if not session_id:
        title = body.message[:50] + "..." if len(body.message) > 50 else body.message
        session = await chat_service.create_session(db, ctx.user_id, body.project_id, title, ctx.org_id)
        session_id = session.id
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

    # -- Load project documents for citation-aware responses --
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

    # -- Load synced data from integrations (emails, invoices, etc.) --
    try:
        from app.integrations.sync_service import get_synced_items_for_project, build_synced_data_context
        synced_items = await get_synced_items_for_project(db, ctx.org_id, None, limit=100)
        if synced_items:
            synced_context = build_synced_data_context(synced_items)
            if doc_context:
                doc_context = doc_context + "\n\n" + synced_context
            else:
                doc_context = synced_context
            logger.info("synced_data_injected", items=len(synced_items))
    except Exception as e:
        logger.warning("synced_data_load_error", error=str(e))

    # -- Load drawing data for blueprint documents (hybrid: text + optional images) --
    drawing_images = []
    try:
        drawing_docs = [d for d in project_docs if getattr(d, 'doc_type', '') == 'drawing']
        if drawing_docs:
            from app.services.blueprint_service import find_relevant_pages, build_drawing_metadata_context
            from app.services.hybrid_extraction import build_hybrid_context, should_use_vision
            from app.models.db_models import DocumentPage
            from PIL import Image
            import base64, io

            for dd in drawing_docs[:3]:
                metadata_ctx = await build_drawing_metadata_context(db, dd.id)
                if metadata_ctx:
                    doc_context = (doc_context + "\n\nDRAWING INDEX:\n" + metadata_ctx) if doc_context else ("DRAWING INDEX:\n" + metadata_ctx)

                relevant = await find_relevant_pages(db, dd.id, body.message, max_pages=3)

                # Hybrid extraction: get text data directly from PDF
                if os.path.exists(dd.file_path):
                    hybrid_data = build_hybrid_context(dd.file_path, relevant)
                    if hybrid_data.get("text_context"):
                        hybrid_text = f"\nDIRECTLY EXTRACTED DATA (doc_id: {dd.id}, high accuracy):\n{hybrid_data['text_context'][:6000]}"
                        doc_context = (doc_context + "\n" + hybrid_text) if doc_context else hybrid_text

                    use_vision = should_use_vision(hybrid_data.get("pdf_type", "raster"), body.message)
                else:
                    use_vision = True

                if use_vision:
                    for page_num in relevant:
                        page_result = await db.execute(
                            select(DocumentPage).where(
                                DocumentPage.document_id == dd.id,
                                DocumentPage.page_number == page_num,
                            ).order_by(DocumentPage.id.desc()).limit(1)
                        )
                        page = page_result.scalar_one_or_none()
                        if page and page.image_path and os.path.exists(page.image_path):
                            img = Image.open(page.image_path)
                            w, h = img.size
                            if max(w, h) > 4000:
                                scale = 4000 / max(w, h)
                                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")
                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=75)
                            img_b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
                            drawing_images.append({
                                "page_number": page_num,
                                "base64": img_b64,
                                "doc_id": dd.id,
                            })

            if drawing_images:
                logger.info("drawing_images_loaded", count=len(drawing_images))
    except Exception as e:
        logger.warning("drawing_load_error", error=str(e))

    # -- 2. Check cache (skip if files attached) --
    cached_response = None
    if not body.files:
        cached_response = await cache_service.get_cached_response(db, body.project_id, body.message)

    if cached_response:
        logger.info("cache_hit", project_id=body.project_id, query=body.message[:50])
        formatted = cached_response

        file_meta = [{"name": f.get("name", "file")} for f in body.files] if body.files else []
        await chat_service.save_message(db, session_id, "user", body.message, file_meta)
        await chat_service.save_message(db, session_id, "assistant", formatted)

    else:
        # -- 3. Token budget check --
        history = await chat_service.get_conversation_history(db, session_id, limit=20)
        if history and history[-1]["role"] == "user":
            history = history[:-1]

        budget = token_service.check_context_budget(project_data, history, body.message)

        if not budget["fits"]:
            logger.warning("token_budget_exceeded", total=budget["total_tokens"], recommendation=budget["recommendation"])

            if budget["recommendation"] == "truncate_project_data":
                project_data = token_service.truncate_project_data(project_data, token_service.MAX_SYSTEM_TOKENS)
            elif budget["recommendation"] == "truncate_history":
                max_hist_tokens = token_service.MAX_CONTEXT_TOKENS - budget["system_tokens"] - budget["message_tokens"] - token_service.RESPONSE_RESERVE
                history = token_service.truncate_history(history, max_hist_tokens)
            else:
                project_data = token_service.truncate_project_data(project_data, token_service.MAX_SYSTEM_TOKENS // 2)
                history = token_service.truncate_history(history, 5000)

        # Save user message
        file_meta = [{"name": f.get("name", "file")} for f in body.files] if body.files else []
        await chat_service.save_message(db, session_id, "user", body.message, file_meta)

        # -- Call Claude --
        try:
            raw = await ai_service.ask_claude(
                message=body.message,
                project_data=project_data,
                conversation_history=history,
                files=body.files or None,
                document_context=doc_context,
                drawing_images=drawing_images or None,
            )
        except RuntimeError as e:
            await chat_service.save_message(db, session_id, "assistant", f"Error: {str(e)}")
            raise HTTPException(status_code=502, detail=str(e))

        formatted = ai_service.format_response(raw)

        # Save AI response with token count
        response_tokens = token_service.estimate_tokens(raw)
        msg = await chat_service.save_message(db, session_id, "assistant", formatted)
        msg.token_count = response_tokens

        # -- Cache the response (only for non-file queries) --
        if not body.files:
            await cache_service.store_cached_response(
                db, body.project_id, body.message, formatted, response_tokens
            )

    # -- 4. Return --
    result = await db.execute(select(ChatSession.title).where(ChatSession.id == session_id))
    row = result.one_or_none()
    title = row.title if row else "Chat"

    return AiAskResponse(session_id=session_id, response=formatted, title=title)
