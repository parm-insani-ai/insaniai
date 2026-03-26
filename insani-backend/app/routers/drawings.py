"""
Drawings Router — Blueprint upload, sheet browsing, page images, and vision Q&A.

POST   /v1/drawings/upload              — Upload a blueprint PDF (renders + indexes)
GET    /v1/drawings/{doc_id}/sheets      — List all sheets with metadata
GET    /v1/drawings/{doc_id}/page/{n}/image — Serve a rendered page image
POST   /v1/drawings/ask                  — Ask a question about a drawing (vision)
POST   /v1/drawings/{doc_id}/reindex     — Re-run title block extraction
"""

import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import structlog

from app.db import get_db
from app.models.db_models import Document, DocumentPage
from app.services import document_service, blueprint_service
from app.services.blueprint_service import ask_about_drawings
from app.middleware.auth import require_auth_context, AuthContext

logger = structlog.get_logger()
router = APIRouter(prefix="/v1/drawings", tags=["Drawings"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB for blueprints


# ── Request/Response schemas ──

class DrawingAskRequest(BaseModel):
    doc_id: int
    question: str
    project_id: int
    session_id: int | None = None


class DrawingAskResponse(BaseModel):
    response: str
    pages_used: list[int]
    token_cost: int
    doc_id: int


class SheetResponse(BaseModel):
    page_number: int
    sheet_number: str
    sheet_title: str
    discipline: str
    scale: str
    has_image: bool
    drawing_type: str
    key_elements: list[str]
    dimensions_visible: bool


class DrawingUploadResponse(BaseModel):
    id: int
    filename: str
    page_count: int
    status: str
    doc_type: str
    sheets: list[SheetResponse]


# ── Endpoints ──

@router.post("/upload", response_model=DrawingUploadResponse, status_code=201)
async def upload_drawing(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a blueprint/drawing PDF. The file is:
    1. Saved to disk
    2. Text-extracted page by page (for notes/specs)
    3. Rendered to images at 200 DPI (for Claude vision)
    4. Each page analyzed for title block metadata (sheet number, discipline, etc.)
    """
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum {MAX_FILE_SIZE // (1024*1024)}MB.")

    # Validate file type
    media_type = file.content_type or "application/octet-stream"
    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF or image files.")

    ext_map = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff", ".tiff": "image/tiff"}
    media_type = ext_map.get(ext, media_type)

    # Save file to disk
    doc = await document_service.save_uploaded_file(
        db=db,
        org_id=ctx.org_id,
        project_id=project_id,
        user_id=ctx.user_id,
        filename=file.filename,
        file_bytes=contents,
        media_type=media_type,
    )
    doc.doc_type = "drawing"

    # Parse PDF text (existing flow — extracts notes, specs, title block text)
    if media_type == "application/pdf":
        await document_service.parse_pdf(db, doc.id)
    else:
        doc.status = "ready"
        doc.page_count = 1

    await db.flush()

    # Render pages to images
    rendered_pages = await blueprint_service.render_document_pages(db, doc)

    if not rendered_pages and media_type != "application/pdf":
        # For direct image uploads, the file itself is the image
        page_result = await db.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == doc.id,
                DocumentPage.page_number == 1,
            )
        )
        page = page_result.scalar_one_or_none()
        if page:
            page.image_path = doc.file_path

    await db.flush()

    # Index all pages (title block extraction via Claude vision)
    # This is the expensive part — runs vision on each page
    if rendered_pages:
        await blueprint_service.index_all_pages(db, doc, rendered_pages)

    await db.commit()

    # Return sheet list
    sheets = await blueprint_service.get_drawing_sheets(db, doc.id, ctx.org_id)

    return DrawingUploadResponse(
        id=doc.id,
        filename=doc.filename,
        page_count=doc.page_count,
        status=doc.status,
        doc_type="drawing",
        sheets=[SheetResponse(**s) for s in sheets],
    )


@router.get("/{doc_id}/sheets", response_model=list[SheetResponse])
async def list_sheets(
    doc_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get all sheets for a drawing document with their metadata."""
    sheets = await blueprint_service.get_drawing_sheets(db, doc_id, ctx.org_id)
    if not sheets:
        raise HTTPException(status_code=404, detail="Drawing not found or no sheets available")
    return [SheetResponse(**s) for s in sheets]


@router.get("/{doc_id}/page/{page_number}/image")
async def serve_page_image(
    doc_id: int,
    page_number: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Serve a rendered page image. Used by the frontend drawing viewer.
    No auth required — same pattern as document file serving (iframes can't set headers).
    """
    result = await db.execute(
        select(DocumentPage).where(
            DocumentPage.document_id == doc_id,
            DocumentPage.page_number == page_number,
        ).order_by(DocumentPage.id.desc()).limit(1)
    )
    page = result.scalar_one_or_none()
    if not page or not page.image_path:
        raise HTTPException(status_code=404, detail="Page image not found")

    if not os.path.exists(page.image_path):
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    return FileResponse(
        path=page.image_path,
        media_type="image/png",
        filename=f"page_{page_number}.png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/ask", response_model=DrawingAskResponse)
async def ask_drawing(
    body: DrawingAskRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Ask a question about a drawing document using Claude vision.
    Automatically selects the most relevant pages based on the question.
    """
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # Load project data for context
    from app.models.db_models import Project
    proj_result = await db.execute(
        select(Project).where(Project.id == body.project_id, Project.org_id == ctx.org_id)
    )
    project = proj_result.scalar_one_or_none()
    project_data = project.data_json if project else {}

    try:
        result = await ask_about_drawings(
            db=db,
            doc_id=body.doc_id,
            question=body.question,
            org_id=ctx.org_id,
            project_data=project_data,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return DrawingAskResponse(
        response=result["response"],
        pages_used=result["pages_used"],
        token_cost=result["token_cost"],
        doc_id=body.doc_id,
    )


@router.post("/{doc_id}/reindex")
async def reindex_drawing(
    doc_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Re-run title block extraction on all pages of a drawing."""
    doc_result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.org_id == ctx.org_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pages_result = await db.execute(
        select(DocumentPage).where(DocumentPage.document_id == doc_id)
    )
    pages = list(pages_result.scalars().all())

    rendered_pages = [
        {"page_number": p.page_number, "image_path": p.image_path}
        for p in pages if p.image_path
    ]

    await blueprint_service.index_all_pages(db, doc, rendered_pages)
    await db.commit()

    return {"reindexed": len(rendered_pages), "doc_id": doc_id}
