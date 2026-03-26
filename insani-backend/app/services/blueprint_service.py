"""
Blueprint Service — PDF-to-image rendering, Claude vision analysis,
smart page selection, and drawing metadata extraction.

Accuracy-first design:
1. Renders PDF pages to high-res images (200 DPI) for Claude vision
2. Extracts title block metadata on upload for intelligent page routing
3. Sends only the most relevant pages as images per query (max 5)
4. Caches analysis results keyed by image hash to avoid redundant calls
5. Combines text extraction + vision for maximum accuracy — text for
   notes/specs, vision for graphical elements/dimensions/layouts

Requires: poppler-utils (system), pdf2image, Pillow, anthropic
"""

import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image
from pdf2image import convert_from_path
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import structlog

from app.config import settings
from app.models.db_models import Document, DocumentPage, DrawingAnalysis, DrawingRegion
import anthropic

logger = structlog.get_logger()

# Vision client — reuse the same API key
_vision_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
RENDER_DPI = int(os.getenv("BLUEPRINT_RENDER_DPI", "200"))
MAX_VISION_PAGES = int(os.getenv("BLUEPRINT_MAX_VISION_PAGES", "5"))
MAX_IMAGE_DIMENSION = 8000  # Claude vision max is 8192px per side


# ═══════════════════════════════════════════════
# PDF → IMAGE RENDERING
# ═══════════════════════════════════════════════

def _get_render_dir(org_id: int, project_id: int, doc_id: int) -> Path:
    """Get the directory for storing rendered page images."""
    render_dir = Path(UPLOAD_DIR) / str(org_id) / str(project_id) / "renders" / str(doc_id)
    render_dir.mkdir(parents=True, exist_ok=True)
    return render_dir


def _render_pdf_pages_sync(file_path: str, dpi: int = RENDER_DPI) -> list[Image.Image]:
    """
    Render all PDF pages to PIL Images. CPU-bound — call via run_in_executor.
    Uses poppler's pdftoppm under the hood for accurate rendering.
    """
    images = convert_from_path(
        file_path,
        dpi=dpi,
        fmt="png",
        thread_count=2,
    )
    return images


def _resize_if_needed(img: Image.Image) -> Image.Image:
    """Resize image if it exceeds Claude vision's max dimension."""
    w, h = img.size
    if max(w, h) <= MAX_IMAGE_DIMENSION:
        return img
    scale = MAX_IMAGE_DIMENSION / max(w, h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    return img.resize((new_w, new_h), Image.LANCZOS)


def _image_to_base64(img: Image.Image) -> str:
    """Convert PIL Image to base64-encoded PNG string."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _image_hash(img: Image.Image) -> str:
    """SHA-256 hash of image bytes for cache invalidation."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


async def render_document_pages(
    db: AsyncSession,
    doc: Document,
) -> list[dict]:
    """
    Render all pages of a PDF document to images and store on disk.
    Updates DocumentPage.image_path for each page.
    Returns list of {page_number, image_path, image_hash}.

    This is called once on upload for drawing-type documents.
    """
    if not os.path.exists(doc.file_path):
        logger.error("render_file_missing", doc_id=doc.id, path=doc.file_path)
        return []

    render_dir = _get_render_dir(doc.org_id, doc.project_id, doc.id)

    # Render in a thread to not block the event loop
    loop = asyncio.get_event_loop()
    try:
        images = await loop.run_in_executor(
            None, _render_pdf_pages_sync, doc.file_path, RENDER_DPI
        )
    except Exception as e:
        logger.error("render_failed", doc_id=doc.id, error=str(e))
        return []

    results = []
    for i, img in enumerate(images):
        page_num = i + 1
        img = _resize_if_needed(img)

        # Save to disk
        img_path = render_dir / f"page_{page_num}.png"
        img.save(str(img_path), format="PNG", optimize=True)

        img_h = _image_hash(img)

        # Update DocumentPage record if it exists
        page_result = await db.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == doc.id,
                DocumentPage.page_number == page_num,
            ).order_by(DocumentPage.id.desc()).limit(1)
        )
        page = page_result.scalar_one_or_none()
        if page:
            page.image_path = str(img_path)

        results.append({
            "page_number": page_num,
            "image_path": str(img_path),
            "image_hash": img_h,
        })

    logger.info("pages_rendered", doc_id=doc.id, count=len(results), dpi=RENDER_DPI)
    return results


# ═══════════════════════════════════════════════
# TITLE BLOCK EXTRACTION (run on upload)
# ═══════════════════════════════════════════════

TITLE_BLOCK_PROMPT = """You are analyzing a construction/architectural/engineering drawing page.
Extract the title block information and classify this drawing. Return ONLY valid JSON, no markdown.

{
  "sheet_number": "string or empty — e.g. A-101, S-201, M-301, E-101",
  "sheet_title": "string — e.g. First Floor Plan, Foundation Plan, Electrical Layout",
  "discipline": "one of: architectural, structural, mechanical, electrical, plumbing, civil, landscape, general, cover, schedule, detail, other",
  "scale": "string or empty — e.g. 1/4\\\"=1'-0\\\"",
  "revision": "string or empty",
  "key_elements": ["list of major elements visible — rooms, beams, panels, equipment, etc."],
  "dimensions_visible": true or false,
  "notes_summary": "brief summary of any visible notes or specifications on the drawing"
}

Be precise. If you cannot determine a field, use an empty string or empty list. Do NOT guess or fabricate information."""


async def extract_title_block(
    db: AsyncSession,
    doc_id: int,
    page_number: int,
    image_path: str,
) -> dict | None:
    """
    Send a drawing page image to Claude vision to extract title block
    metadata and classify the drawing type. Results are cached in
    DrawingAnalysis with analysis_type='general'.
    """
    if not os.path.exists(image_path):
        return None

    # Load image and encode
    img = Image.open(image_path)
    img_b64 = _image_to_base64(img)
    img_h = _image_hash(img)

    # Check cache — skip if we already analyzed this exact image
    existing = await db.execute(
        select(DrawingAnalysis).where(
            DrawingAnalysis.document_id == doc_id,
            DrawingAnalysis.page_number == page_number,
            DrawingAnalysis.analysis_type == "general",
        )
    )
    cached = existing.scalar_one_or_none()
    if cached and cached.image_hash == img_h:
        return cached.analysis_json

    try:
        response = await _vision_client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                    },
                    {"type": "text", "text": TITLE_BLOCK_PROMPT},
                ],
            }],
        )

        raw_text = "".join(b.text for b in response.content if b.type == "text")

        # Parse JSON — handle possible markdown wrapping
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

        analysis_data = json.loads(raw_text)
        token_cost = response.usage.input_tokens + response.usage.output_tokens

    except (json.JSONDecodeError, Exception) as e:
        logger.warning("title_block_extraction_failed", doc_id=doc_id, page=page_number, error=str(e))
        analysis_data = {
            "sheet_number": "",
            "sheet_title": f"Page {page_number}",
            "discipline": "other",
            "scale": "",
            "key_elements": [],
            "dimensions_visible": False,
            "notes_summary": "",
        }
        token_cost = 0

    # Store or update cache
    if cached:
        cached.analysis_json = analysis_data
        cached.image_hash = img_h
        cached.token_cost = token_cost
    else:
        analysis = DrawingAnalysis(
            document_id=doc_id,
            page_number=page_number,
            analysis_type="general",
            analysis_json=analysis_data,
            image_hash=img_h,
            token_cost=token_cost,
        )
        db.add(analysis)

    # Update the DocumentPage.drawing_type
    page_result = await db.execute(
        select(DocumentPage).where(
            DocumentPage.document_id == doc_id,
            DocumentPage.page_number == page_number,
        ).order_by(DocumentPage.id.desc()).limit(1)
    )
    page = page_result.scalar_one_or_none()
    if page:
        page.drawing_type = analysis_data.get("discipline", "other")

    # Store key elements as regions (for future highlighting)
    if analysis_data.get("key_elements") and not cached:
        latest = await db.execute(
            select(DrawingAnalysis).where(
                DrawingAnalysis.document_id == doc_id,
                DrawingAnalysis.page_number == page_number,
                DrawingAnalysis.analysis_type == "general",
            )
        )
        analysis_row = latest.scalar_one_or_none()
        if analysis_row:
            for element in analysis_data["key_elements"][:20]:
                region = DrawingRegion(
                    analysis_id=analysis_row.id,
                    label=str(element)[:255],
                    region_type="element",
                    metadata_json={},
                )
                db.add(region)

    logger.info(
        "title_block_extracted",
        doc_id=doc_id,
        page=page_number,
        sheet=analysis_data.get("sheet_number", ""),
        discipline=analysis_data.get("discipline", ""),
        tokens=token_cost,
    )
    return analysis_data


async def index_all_pages(db: AsyncSession, doc: Document, rendered_pages: list[dict]):
    """
    Run title block extraction on all rendered pages of a drawing document.
    This builds the searchable index used for smart page selection.
    """
    for page_info in rendered_pages:
        try:
            await extract_title_block(
                db,
                doc.id,
                page_info["page_number"],
                page_info["image_path"],
            )
            await db.flush()
        except Exception as e:
            logger.warning(
                "page_index_failed",
                doc_id=doc.id,
                page=page_info["page_number"],
                error=str(e),
            )


# ═══════════════════════════════════════════════
# SMART PAGE SELECTION
# ═══════════════════════════════════════════════

async def find_relevant_pages(
    db: AsyncSession,
    doc_id: int,
    question: str,
    max_pages: int = MAX_VISION_PAGES,
) -> list[int]:
    """
    Given a user question, find the most relevant drawing pages
    to send to Claude vision. Uses cached metadata from title block
    extraction to match against the question.

    Scoring strategy (accuracy-first):
    1. Exact sheet number match (e.g., "A-101") → highest score
    2. Discipline keyword match → high score
    3. Key element keyword match → medium score
    4. Title keyword match → medium score
    5. Notes keyword match → lower score

    Returns page numbers sorted by relevance, limited to max_pages.
    """
    result = await db.execute(
        select(DrawingAnalysis).where(
            DrawingAnalysis.document_id == doc_id,
            DrawingAnalysis.analysis_type == "general",
        )
    )
    analyses = list(result.scalars().all())

    if not analyses:
        # No index — return first N pages as fallback
        page_result = await db.execute(
            select(DocumentPage.page_number).where(
                DocumentPage.document_id == doc_id,
            ).order_by(DocumentPage.page_number).limit(max_pages)
        )
        return [row[0] for row in page_result.all()]

    q_lower = question.lower()
    q_words = set(q_lower.split())

    # Discipline keyword mapping
    discipline_keywords = {
        "architectural": {"floor plan", "room", "lobby", "corridor", "door", "window", "wall", "ceiling", "finish", "layout", "architectural", "a-"},
        "structural": {"beam", "column", "foundation", "steel", "concrete", "rebar", "structural", "footing", "slab", "s-", "w-section", "load"},
        "electrical": {"electrical", "outlet", "panel", "circuit", "conduit", "switch", "lighting", "wire", "e-", "receptacle", "transformer"},
        "mechanical": {"hvac", "duct", "air handler", "mechanical", "ahu", "vav", "diffuser", "m-", "heating", "cooling", "ventilation"},
        "plumbing": {"plumbing", "pipe", "drain", "fixture", "water", "sewer", "p-", "valve", "sprinkler"},
        "civil": {"site", "grading", "drainage", "civil", "paving", "c-", "survey", "topograph"},
    }

    scored_pages = []
    for analysis in analyses:
        data = analysis.analysis_json or {}
        score = 0.0

        sheet_num = str(data.get("sheet_number", "")).lower()
        sheet_title = str(data.get("sheet_title", "")).lower()
        discipline = str(data.get("discipline", "")).lower()
        elements = [str(e).lower() for e in (data.get("key_elements") or [])]
        notes = str(data.get("notes_summary", "")).lower()

        # 1. Exact sheet number reference (highest confidence)
        if sheet_num and sheet_num in q_lower:
            score += 100

        # 2. Discipline match
        if discipline in discipline_keywords:
            kw_set = discipline_keywords[discipline]
            matches = sum(1 for kw in kw_set if kw in q_lower)
            score += matches * 15

        # 3. Sheet title keyword match
        title_words = set(sheet_title.split())
        title_overlap = len(q_words & title_words)
        score += title_overlap * 10

        # 4. Key element match
        for elem in elements:
            elem_words = set(elem.split())
            if elem_words & q_words:
                score += 8

        # 5. Notes match
        notes_words = set(notes.split())
        notes_overlap = len(q_words & notes_words)
        score += notes_overlap * 3

        # 6. If question mentions "dimension" and this page has dimensions
        if ("dimension" in q_lower or "size" in q_lower or "measurement" in q_lower):
            if data.get("dimensions_visible"):
                score += 12

        if score > 0:
            scored_pages.append((analysis.page_number, score))

    # Sort by score descending, take top N
    scored_pages.sort(key=lambda x: x[1], reverse=True)
    selected = [p[0] for p in scored_pages[:max_pages]]

    # If nothing matched, return first page as fallback
    if not selected:
        selected = [analyses[0].page_number] if analyses else [1]

    logger.info(
        "pages_selected",
        doc_id=doc_id,
        question=question[:80],
        selected=selected,
        scores=[(p, s) for p, s in scored_pages[:max_pages]],
    )
    return selected


# ═══════════════════════════════════════════════
# VISION Q&A — Ask Claude about specific drawing pages
# ═══════════════════════════════════════════════

DRAWING_QA_SYSTEM_PROMPT = """You are insani — a construction AI copilot analyzing architectural/engineering drawings.

CRITICAL RULES FOR ACCURACY:
1. ONLY state what you can actually see in the drawing. Never guess or fabricate dimensions, specifications, or details.
2. If something is unclear or unreadable, say "I cannot clearly read this from the drawing" rather than guessing.
3. When citing dimensions, reproduce them EXACTLY as shown — including units, tolerances, and notation style.
4. Distinguish between what the drawing SHOWS vs what you are INFERRING. Label inferences explicitly.
5. Reference specific sheet numbers and areas when answering.
6. If the question cannot be answered from the visible drawings, say so clearly.

DRAWING CITATION FORMAT:
When referencing information from a drawing, use this exact HTML:
<span class="drawing-cite" data-doc-id="DOC_ID" data-page="PAGE">Sheet SHEET_NUM, DESCRIPTION</span>

FORMAT YOUR RESPONSES WITH HTML:
- Use <strong>bold</strong> for values, dimensions, sheet references
- Use <p> and <br> for structure
- Use <div class="risk-box"><span class="risk-icon">⚠</span><span>CONTENT</span></div> for warnings about unclear/unreadable content
- Be precise, factual, and cite the specific drawing sheet for every claim"""


async def ask_about_drawings(
    db: AsyncSession,
    doc_id: int,
    question: str,
    org_id: int,
    project_data: dict = None,
    conversation_history: list[dict] = None,
    max_pages: int = MAX_VISION_PAGES,
) -> dict:
    """
    Answer a question about a drawing document using Claude vision.

    Flow:
    1. Find the most relevant pages using cached metadata
    2. Load those page images
    3. Build a multimodal prompt with images + text context + question
    4. Send to Claude and return the response

    Returns {response: str, pages_used: list[int], token_cost: int}
    """
    # Verify document exists and belongs to org
    doc_result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.org_id == org_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        return {"response": "Document not found.", "pages_used": [], "token_cost": 0}

    # Find relevant pages
    relevant_pages = await find_relevant_pages(db, doc_id, question, max_pages)

    # Build multimodal content blocks
    content_blocks = []
    pages_used = []

    # Add drawing metadata context as text (cheap, covers all pages)
    metadata_context = await build_drawing_metadata_context(db, doc_id)
    if metadata_context:
        content_blocks.append({
            "type": "text",
            "text": f"DRAWING INDEX (all sheets in this document):\n{metadata_context}",
        })

    # Add relevant page images (expensive but accurate)
    for page_num in relevant_pages:
        page_result = await db.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == doc_id,
                DocumentPage.page_number == page_num,
            ).order_by(DocumentPage.id.desc()).limit(1)
        )
        page = page_result.scalar_one_or_none()
        if not page or not page.image_path or not os.path.exists(page.image_path):
            continue

        try:
            img = Image.open(page.image_path)
            img = _resize_if_needed(img)
            img_b64 = _image_to_base64(img)

            content_blocks.append({
                "type": "text",
                "text": f"\n--- DRAWING PAGE {page_num} ---",
            })
            content_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
            })

            # Include extracted text for this page if available (helps with specs/notes)
            if page.text_content and page.text_content.strip():
                content_blocks.append({
                    "type": "text",
                    "text": f"[Extracted text from page {page_num}]: {page.text_content[:2000]}",
                })

            pages_used.append(page_num)
        except Exception as e:
            logger.warning("page_image_load_failed", doc_id=doc_id, page=page_num, error=str(e))

    if not pages_used:
        return {
            "response": "No drawing pages could be loaded for analysis. Please try re-uploading the document.",
            "pages_used": [],
            "token_cost": 0,
        }

    # Add the user's question
    content_blocks.append({
        "type": "text",
        "text": f"\nQUESTION: {question}\n\nRemember: doc_id for citations is {doc_id}. Only state what you can actually see. Be precise.",
    })

    # Build system prompt with project context if available
    system = DRAWING_QA_SYSTEM_PROMPT
    if project_data:
        system += f"\n\nPROJECT CONTEXT:\n{json.dumps(project_data, indent=2)[:5000]}"

    messages = (conversation_history or []) + [{"role": "user", "content": content_blocks}]

    try:
        response = await _vision_client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=system,
            messages=messages,
        )

        response_text = "".join(b.text for b in response.content if b.type == "text")
        token_cost = response.usage.input_tokens + response.usage.output_tokens

        logger.info(
            "drawing_qa_complete",
            doc_id=doc_id,
            pages=pages_used,
            tokens=token_cost,
            question=question[:80],
        )

        return {
            "response": response_text,
            "pages_used": pages_used,
            "token_cost": token_cost,
        }

    except anthropic.AuthenticationError:
        logger.error("vision_auth_error")
        raise RuntimeError("AI service configuration error. Check your API key.")
    except anthropic.RateLimitError:
        logger.warning("vision_rate_limit")
        raise RuntimeError("AI service is temporarily busy. Please try again.")
    except Exception as e:
        logger.error("vision_error", error=str(type(e).__name__), detail=str(e))
        raise RuntimeError("An error occurred analyzing the drawing.")


# ═══════════════════════════════════════════════
# METADATA CONTEXT (text-based, cheap in tokens)
# ═══════════════════════════════════════════════

async def build_drawing_metadata_context(db: AsyncSession, doc_id: int) -> str:
    """
    Build a text summary of all analyzed drawing pages.
    This is injected alongside images so Claude knows what's on
    pages that weren't sent as images (helps with cross-referencing).
    """
    result = await db.execute(
        select(DrawingAnalysis).where(
            DrawingAnalysis.document_id == doc_id,
            DrawingAnalysis.analysis_type == "general",
        ).order_by(DrawingAnalysis.page_number)
    )
    analyses = list(result.scalars().all())

    if not analyses:
        return ""

    lines = []
    for a in analyses:
        data = a.analysis_json or {}
        sheet = data.get("sheet_number", "")
        title = data.get("sheet_title", f"Page {a.page_number}")
        discipline = data.get("discipline", "")
        elements = ", ".join(str(e) for e in (data.get("key_elements") or [])[:8])
        scale = data.get("scale", "")

        line = f"Page {a.page_number}"
        if sheet:
            line += f" | Sheet {sheet}"
        line += f" | {title}"
        if discipline:
            line += f" | {discipline}"
        if scale:
            line += f" | Scale: {scale}"
        if elements:
            line += f" | Elements: {elements}"
        lines.append(line)

    return "\n".join(lines)


async def get_drawing_sheets(db: AsyncSession, doc_id: int, org_id: int) -> list[dict]:
    """
    Get all sheets for a drawing document with their metadata.
    Used by the frontend to show the sheet browser.
    """
    doc_result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.org_id == org_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        return []

    # Get pages with their analysis data
    pages_result = await db.execute(
        select(DocumentPage).where(
            DocumentPage.document_id == doc_id,
        ).order_by(DocumentPage.page_number)
    )
    pages = list(pages_result.scalars().all())

    analyses_result = await db.execute(
        select(DrawingAnalysis).where(
            DrawingAnalysis.document_id == doc_id,
            DrawingAnalysis.analysis_type == "general",
        )
    )
    analyses = {a.page_number: a for a in analyses_result.scalars().all()}

    sheets = []
    for page in pages:
        analysis = analyses.get(page.page_number)
        data = analysis.analysis_json if analysis else {}

        sheets.append({
            "page_number": page.page_number,
            "sheet_number": data.get("sheet_number", ""),
            "sheet_title": data.get("sheet_title", f"Page {page.page_number}"),
            "discipline": data.get("discipline", ""),
            "scale": data.get("scale", ""),
            "has_image": bool(page.image_path and os.path.exists(page.image_path)),
            "drawing_type": page.drawing_type or "",
            "key_elements": data.get("key_elements", []),
            "dimensions_visible": data.get("dimensions_visible", False),
        })

    return sheets
