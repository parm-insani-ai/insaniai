"""
Documents Router — Upload, list, serve, and manage project documents.

POST   /v1/documents/upload     — Upload a file (multipart form)
GET    /v1/documents?project_id — List project documents
GET    /v1/documents/{id}       — Get document metadata + pages
GET    /v1/documents/{id}/file  — Serve the raw file (for PDF viewer)
DELETE /v1/documents/{id}       — Delete a document
"""

import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import structlog

from app.db import get_db
from app.models.db_models import Document, DocumentPage
from app.services import document_service
from app.middleware.auth import require_auth_context, AuthContext

logger = structlog.get_logger()
router = APIRouter(prefix="/v1/documents", tags=["Documents"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB (blueprints can be large)


# ── Response schemas ──

class DocumentResponse(BaseModel):
    id: int
    filename: str
    media_type: str
    file_size: int
    page_count: int
    status: str
    doc_type: str = "general"
    created_at: str | None = None


class DocumentDetailResponse(DocumentResponse):
    pages: list[dict] = []


# ── Endpoints ──

@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a document (PDF, image, etc.) to a project.
    The file is saved to disk and parsed asynchronously.
    For PDFs, text is extracted page-by-page for citation support.
    """
    # Validate file size
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum {MAX_FILE_SIZE // (1024*1024)}MB.")

    # Validate file type
    allowed_types = [
        "application/pdf",
        "image/png", "image/jpeg", "image/webp", "image/gif",
        "text/plain", "text/csv",
    ]
    media_type = file.content_type or "application/octet-stream"
    if media_type not in allowed_types:
        # Try to infer from extension
        ext = Path(file.filename).suffix.lower()
        ext_map = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        media_type = ext_map.get(ext, media_type)

    # Save file
    doc = await document_service.save_uploaded_file(
        db=db,
        org_id=ctx.org_id,
        project_id=project_id,
        user_id=ctx.user_id,
        filename=file.filename,
        file_bytes=contents,
        media_type=media_type,
    )

    # Parse PDF synchronously (for now — move to background job in production)
    if media_type == "application/pdf":
        await document_service.parse_pdf(db, doc.id)
    else:
        doc.status = "ready"
        doc.page_count = 1

    await db.commit()

    return DocumentResponse(
        id=doc.id,
        filename=doc.filename,
        media_type=doc.media_type,
        file_size=doc.file_size,
        page_count=doc.page_count,
        status=doc.status,
        created_at=str(doc.created_at) if doc.created_at else None,
    )


@router.get("/", response_model=list[DocumentResponse])
async def list_documents(
    project_id: int = Query(...),
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """List all documents for a project."""
    docs = await document_service.get_project_documents(db, project_id, ctx.org_id)
    return [
        DocumentResponse(
            id=d.id, filename=d.filename, media_type=d.media_type,
            file_size=d.file_size, page_count=d.page_count, status=d.status,
            doc_type=getattr(d, 'doc_type', 'general') or 'general',
            created_at=str(d.created_at) if d.created_at else None,
        )
        for d in docs
    ]


@router.get("/{doc_id}", response_model=DocumentDetailResponse)
async def get_document(
    doc_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get document metadata and parsed page text."""
    doc = await document_service.get_document_with_pages(db, doc_id, ctx.org_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentDetailResponse(
        id=doc.id, filename=doc.filename, media_type=doc.media_type,
        file_size=doc.file_size, page_count=doc.page_count, status=doc.status,
        created_at=str(doc.created_at) if doc.created_at else None,
        pages=[
            {"page_number": p.page_number, "text_preview": p.text_content[:200] + "..." if len(p.text_content) > 200 else p.text_content}
            for p in doc.pages
        ],
    )


@router.get("/{doc_id}/file")
async def serve_document_file(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Serve the raw document file. Used by the frontend PDF viewer.
    No auth required — iframes cannot set Authorization headers.
    """
    result = await db.execute(
        select(Document).where(Document.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=doc.file_path,
        media_type=doc.media_type,
        filename=doc.filename,
        headers={"Content-Disposition": f"inline; filename=\"{doc.filename}\""}
    )


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and its file from disk."""
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.org_id == ctx.org_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete file from disk
    try:
        if os.path.exists(doc.file_path):
            os.remove(doc.file_path)
    except Exception as e:
        logger.warning("file_delete_failed", path=doc.file_path, error=str(e))

    await db.execute(delete(Document).where(Document.id == doc_id))
    return {"deleted": True}
