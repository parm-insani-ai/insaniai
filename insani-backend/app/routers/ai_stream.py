"""
AI Streaming Router -- with integration data injection.
"""

import json
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, async_session
from app.models.db_models import Project, ChatSession
from app.models.schemas_chat import AiAskRequest
from app.services import chat_service, ai_service, document_service
from app.middleware.auth import require_auth_context, AuthContext
from slowapi import Limiter
from slowapi.util import get_remote_address
import structlog

logger = structlog.get_logger()
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/v1/ai", tags=["AI Streaming"])


@router.post("/stream")
@limiter.limit("20/minute")
async def stream_ask(
    request: Request,
    body: AiAskRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
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

    # -- Load context (lightweight — only heavy drawing work if question is about drawings) --
    doc_context = ""
    drawing_images = []
    project_docs = await document_service.get_project_documents(db, body.project_id, ctx.org_id)

    # Quick check: does this question seem related to drawings/blueprints?
    msg_lower = body.message.lower()
    drawing_keywords = ["drawing", "blueprint", "sheet", "plan", "floor", "elevation",
        "section", "detail", "structural", "electrical", "mechanical", "plumbing",
        "dimension", "scale", "beam", "column", "wall", "room", "layout",
        "a-", "s-", "m-", "e-", "p-", "spec", "note"]
    has_drawing_docs = any(getattr(d, 'doc_type', '') == 'drawing' for d in project_docs)
    question_about_drawings = has_drawing_docs and any(kw in msg_lower for kw in drawing_keywords)

    # Load regular document context (fast — just text)
    ready_docs = [d for d in project_docs if d.status == "ready" and getattr(d, 'doc_type', '') != 'drawing']
    if ready_docs:
        loaded_docs = []
        for d in ready_docs[:5]:
            full_doc = await document_service.get_document_with_pages(db, d.id, ctx.org_id)
            if full_doc:
                loaded_docs.append(full_doc)
        doc_context = document_service.build_document_context(loaded_docs)

    # Load drawing context ONLY if question is about drawings
    if question_about_drawings:
        try:
            drawing_docs = [d for d in project_docs if getattr(d, 'doc_type', '') == 'drawing']
            from app.services.blueprint_service import find_relevant_pages, build_drawing_metadata_context
            from app.services.hybrid_extraction import build_hybrid_context, should_use_vision
            from app.models.db_models import DocumentPage
            from PIL import Image
            import base64, io

            for dd in drawing_docs[:2]:
                metadata_ctx = await build_drawing_metadata_context(db, dd.id)
                if metadata_ctx:
                    doc_context = (doc_context + "\n\nDRAWING INDEX:\n" + metadata_ctx) if doc_context else ("DRAWING INDEX:\n" + metadata_ctx)

                relevant = await find_relevant_pages(db, dd.id, body.message, max_pages=2)

                if os.path.exists(dd.file_path):
                    hybrid_data = build_hybrid_context(dd.file_path, relevant)
                    if hybrid_data.get("text_context"):
                        hybrid_text = f"\nDIRECTLY EXTRACTED DATA (doc_id: {dd.id}, high accuracy):\n{hybrid_data['text_context'][:4000]}"
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
                logger.info("stream_drawing_images_loaded", count=len(drawing_images))
        except Exception as e:
            logger.warning("stream_drawing_load_error", error=str(e))

    # Load synced integration data (fast)
    try:
        from app.integrations.sync_service import get_synced_items_for_project, build_synced_data_context
        synced_items = await get_synced_items_for_project(db, ctx.org_id, None, limit=50)
        if synced_items:
            synced_context = build_synced_data_context(synced_items)
            doc_context = (doc_context + "\n\n" + synced_context) if doc_context else synced_context
    except Exception as e:
        logger.warning("stream_synced_data_error", error=str(e))

    # -- Save user message --
    file_meta = [{"name": f.get("name", "file")} for f in body.files] if body.files else []
    await chat_service.save_message(db, session_id, "user", body.message, file_meta)
    await db.commit()

    # -- Get history --
    history = await chat_service.get_conversation_history(db, session_id, limit=20)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    title_result = await db.execute(select(ChatSession.title).where(ChatSession.id == session_id))
    row = title_result.one_or_none()
    title = row.title if row else "Chat"

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
                drawing_images=drawing_images or None,
            ):
                full_response += chunk
                yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"

            formatted = ai_service.format_response(full_response)

            save_db = async_session()
            try:
                await chat_service.save_message(save_db, session_id, "assistant", formatted)
                await save_db.commit()
            finally:
                await save_db.close()

            yield f"event: done\ndata: {json.dumps({'full_response': formatted})}\n\n"

        except Exception as e:
            logger.error("stream_error", error=str(e), session_id=session_id)
            error_msg = "AI service encountered an error. Please try again."
            yield f"event: error\ndata: {json.dumps({'message': error_msg})}\n\n"

            try:
                save_db = async_session()
                try:
                    await chat_service.save_message(save_db, session_id, "assistant", f"Error: {error_msg}")
                    await save_db.commit()
                finally:
                    await save_db.close()
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
