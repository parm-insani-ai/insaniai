"""
Document Service — PDF parsing and document context for AI.

Handles:
1. Saving uploaded files to disk
2. Extracting text page-by-page from PDFs (using PyPDF2)
3. Building document context for Claude prompts with page markers
4. Looking up page content for citation verification

The key insight: when we send document content to Claude, we wrap
each page's text with markers like [PAGE 1], [PAGE 2], etc. Claude
is instructed to cite these page numbers. The frontend then uses
the page number to navigate the PDF viewer.
"""

import os
import uuid
import hashlib
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import structlog

from app.models.db_models import Document, DocumentPage
from app.config import settings

logger = structlog.get_logger()

# Where uploaded files are stored on disk
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")


async def save_uploaded_file(
    db: AsyncSession,
    org_id: int,
    project_id: int,
    user_id: int,
    filename: str,
    file_bytes: bytes,
    media_type: str,
) -> Document:
    """
    Save an uploaded file to disk and create a Document record.
    Returns the Document (status='processing' until parsing completes).
    """
    # Create upload directory
    project_dir = Path(UPLOAD_DIR) / str(org_id) / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename to avoid collisions
    ext = Path(filename).suffix
    unique_name = f"{uuid.uuid4().hex[:12]}{ext}"
    file_path = project_dir / unique_name

    # Write file
    file_path.write_bytes(file_bytes)

    # Create DB record
    doc = Document(
        org_id=org_id,
        project_id=project_id,
        uploaded_by=user_id,
        filename=filename,
        file_path=str(file_path),
        file_size=len(file_bytes),
        media_type=media_type,
        status="processing",
    )
    db.add(doc)
    await db.flush()

    logger.info("document_saved", doc_id=doc.id, filename=filename, size=len(file_bytes))
    return doc


async def parse_pdf(db: AsyncSession, doc_id: int):
    """
    Extract text from a PDF page-by-page and store in DocumentPage rows.
    Updates the document status to 'ready' when done.
    
    Uses PyPDF2 for extraction. For scanned PDFs (images), this will
    extract no text — in production, add OCR via Tesseract or similar.
    """
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        return

    try:
        import pypdf
        reader = pypdf.PdfReader(doc.file_path)
        page_count = len(reader.pages)

        char_offset = 0
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            text = text.strip()

            page_obj = DocumentPage(
                document_id=doc.id,
                page_number=i + 1,
                text_content=text,
                char_offset=char_offset,
            )
            db.add(page_obj)
            char_offset += len(text)

        doc.page_count = page_count
        doc.status = "ready"
        logger.info("pdf_parsed", doc_id=doc.id, pages=page_count)

    except Exception as e:
        doc.status = "error"
        logger.error("pdf_parse_error", doc_id=doc.id, error=str(e))


async def get_project_documents(db: AsyncSession, project_id: int, org_id: int) -> list[Document]:
    """Get all documents for a project (with tenant check)."""
    result = await db.execute(
        select(Document)
        .where(Document.project_id == project_id, Document.org_id == org_id)
        .order_by(Document.created_at.desc())
    )
    return list(result.scalars().all())


async def get_document_with_pages(db: AsyncSession, doc_id: int, org_id: int) -> Document | None:
    """Get a document with all its parsed pages."""
    result = await db.execute(
        select(Document)
        .options(selectinload(Document.pages))
        .where(Document.id == doc_id, Document.org_id == org_id)
    )
    return result.scalar_one_or_none()


def build_document_context(documents: list) -> str:
    """
    Build a text block of document content with page markers.
    This gets injected into the Claude system prompt so the AI
    knows what's in each document and can cite specific pages.
    
    Format:
        === DOCUMENT: "Spec Section 09 21 16.pdf" (doc_id: 5) ===
        [PAGE 1]
        Fire-rated partition assemblies shall comply with...
        [PAGE 2]
        UL Design No. U419 is required for all corridor walls...
        === END DOCUMENT ===
    """
    if not documents:
        return ""

    context_parts = []
    for doc in documents:
        if doc.status != "ready" or not hasattr(doc, 'pages') or not doc.pages:
            continue

        parts = [f'\n=== DOCUMENT: "{doc.filename}" (doc_id: {doc.id}) ===']
        for page in doc.pages:
            if page.text_content:
                parts.append(f"[PAGE {page.page_number}]")
                # Truncate very long pages to avoid blowing the context window
                text = page.text_content
                if len(text) > 3000:
                    text = text[:3000] + "... [truncated]"
                parts.append(text)
        parts.append("=== END DOCUMENT ===\n")
        context_parts.append("\n".join(parts))

    return "\n".join(context_parts)


def build_citation_prompt_addition() -> str:
    """
    Additional prompt instructions for citation-aware responses.
    Appended to the system prompt when documents are present.
    """
    return """

DOCUMENT CITATION RULES (CRITICAL — follow these exactly):
When your answer references information from an uploaded document:
1. Cite the specific page using this exact HTML format:
   <span class="doc-cite" data-doc-id="DOC_ID" data-page="PAGE_NUMBER">DOCUMENT_NAME, p. PAGE_NUMBER</span>
2. Replace DOC_ID with the actual doc_id number from the document header.
3. Replace PAGE_NUMBER with the page number from the [PAGE X] marker where you found the information.
4. Replace DOCUMENT_NAME with a short version of the filename (without extension).
5. ALWAYS cite the most specific page. If info spans pages 3-4, cite both: p. 3-4.
6. Multiple citations in one response are expected. Cite every claim from a document.
7. If you cannot find the answer in the documents, say so explicitly.

Example citation:
<span class="doc-cite" data-doc-id="5" data-page="47">Spec 09 21 16, p. 47</span>"""
