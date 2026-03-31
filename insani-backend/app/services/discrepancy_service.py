"""
Discrepancy Service — Spec vs Submittal comparison using Claude.

Compares specification documents against submittals and detects:
- Material mismatches (wrong grade, type, manufacturer)
- Dimension mismatches (wrong size, tolerance)
- Missing required items (spec requires something submittal doesn't include)
- Non-compliant products (doesn't meet code/standard requirements)
- Other discrepancies

Uses hybrid extraction (pdfplumber) for text, Claude for analysis.
Returns structured JSON findings.
"""

import json
import anthropic
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import structlog

from app.config import settings
from app.models.db_models import (
    Document, DocumentPage, DiscrepancyReport, DiscrepancyItem
)
from app.services.document_service import get_document_with_pages, build_document_context
from app.services.hybrid_extraction import build_hybrid_context

logger = structlog.get_logger()

_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

COMPARISON_PROMPT = """You are a construction specification compliance reviewer. Compare the SPECIFICATION DOCUMENTS against the SUBMITTAL DOCUMENTS below. Identify EVERY discrepancy where the submittal does not match the spec requirements.

For each discrepancy found, provide:
- severity: "critical" (safety/code issue), "major" (significant non-compliance), "minor" (small deviation), or "info" (observation)
- category: "material_mismatch", "dimension_mismatch", "missing_item", "non_compliant", or "other"
- title: short description (under 100 chars)
- description: full explanation of the discrepancy
- spec_reference: where in the spec (filename, page)
- spec_doc_id: the doc_id number from the spec header
- spec_page: page number
- spec_excerpt: exact text from the spec
- submittal_reference: where in the submittal
- submittal_doc_id: the doc_id number from the submittal header
- submittal_page: page number
- submittal_excerpt: exact text from the submittal
- recommendation: suggested resolution

CRITICAL RULES:
1. Only report REAL discrepancies — do not fabricate issues
2. Quote exact text from the documents when possible
3. If a spec requirement has no corresponding submittal data, flag it as "missing_item"
4. Use the doc_id numbers from the document headers for references
5. Be precise about page numbers

Return ONLY valid JSON with this structure (no markdown, no explanation):
{
  "summary": "Overall assessment in 2-3 sentences",
  "discrepancies": [
    {
      "severity": "critical|major|minor|info",
      "category": "material_mismatch|dimension_mismatch|missing_item|non_compliant|other",
      "title": "Short description",
      "description": "Full explanation",
      "spec_reference": "filename, p. X",
      "spec_doc_id": 1,
      "spec_page": 1,
      "spec_excerpt": "Exact text from spec",
      "submittal_reference": "filename, p. Y",
      "submittal_doc_id": 2,
      "submittal_page": 1,
      "submittal_excerpt": "Exact text from submittal",
      "recommendation": "Suggested fix"
    }
  ]
}"""


async def create_report(
    db: AsyncSession,
    org_id: int,
    project_id: int,
    user_id: int,
    spec_doc_ids: list[int],
    submittal_doc_ids: list[int],
    title: str = "",
) -> DiscrepancyReport:
    """Create a new discrepancy report (status: pending)."""
    if not title:
        title = f"Comparison — {len(spec_doc_ids)} spec(s) vs {len(submittal_doc_ids)} submittal(s)"

    report = DiscrepancyReport(
        org_id=org_id,
        project_id=project_id,
        created_by=user_id,
        title=title,
        status="pending",
        spec_doc_ids=spec_doc_ids,
        submittal_doc_ids=submittal_doc_ids,
    )
    db.add(report)
    await db.flush()
    return report


async def run_analysis(db: AsyncSession, report_id: int, org_id: int) -> DiscrepancyReport:
    """
    Run the actual comparison. Loads spec + submittal text,
    sends to Claude, parses JSON response, stores findings.
    """
    result = await db.execute(
        select(DiscrepancyReport).where(
            DiscrepancyReport.id == report_id,
            DiscrepancyReport.org_id == org_id,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise ValueError("Report not found")

    report.status = "analyzing"
    await db.flush()

    try:
        # Build context for spec documents
        spec_context = await _build_doc_context(db, report.spec_doc_ids, org_id, "SPECIFICATION")
        submittal_context = await _build_doc_context(db, report.submittal_doc_ids, org_id, "SUBMITTAL")

        if not spec_context and not submittal_context:
            report.status = "error"
            report.error_message = "No text could be extracted from the documents"
            return report

        # Build the prompt
        prompt = f"""SPECIFICATION DOCUMENTS:
{spec_context}

SUBMITTAL DOCUMENTS:
{submittal_context}"""

        # Call Claude
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=4096,
            system=COMPARISON_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = "".join(b.text for b in response.content if b.type == "text")

        # Parse JSON response
        analysis = _parse_json_response(raw_text)

        if not analysis:
            report.status = "error"
            report.error_message = "Failed to parse AI response"
            return report

        # Store summary
        report.summary = analysis.get("summary", "")

        # Store individual discrepancy items
        discrepancies = analysis.get("discrepancies", [])
        for d in discrepancies:
            item = DiscrepancyItem(
                report_id=report.id,
                severity=d.get("severity", "info"),
                category=d.get("category", "other"),
                title=d.get("title", "Untitled finding"),
                description=d.get("description", ""),
                spec_reference=d.get("spec_reference", ""),
                spec_doc_id=d.get("spec_doc_id"),
                spec_page=d.get("spec_page"),
                spec_excerpt=d.get("spec_excerpt", ""),
                submittal_reference=d.get("submittal_reference", ""),
                submittal_doc_id=d.get("submittal_doc_id"),
                submittal_page=d.get("submittal_page"),
                submittal_excerpt=d.get("submittal_excerpt", ""),
                recommendation=d.get("recommendation", ""),
            )
            db.add(item)

        report.discrepancy_count = len(discrepancies)
        report.status = "complete"

        logger.info(
            "discrepancy_analysis_complete",
            report_id=report.id,
            findings=len(discrepancies),
            tokens=response.usage.input_tokens + response.usage.output_tokens,
        )

    except anthropic.APIError as e:
        report.status = "error"
        report.error_message = f"AI service error: {getattr(e, 'status_code', 'unknown')}"
        logger.error("discrepancy_api_error", report_id=report.id, error=str(e))
    except Exception as e:
        report.status = "error"
        report.error_message = str(e)[:500]
        logger.error("discrepancy_analysis_error", report_id=report.id, error=str(e))

    return report


async def _build_doc_context(
    db: AsyncSession,
    doc_ids: list[int],
    org_id: int,
    label: str,
) -> str:
    """Build text context for a list of documents, labeled as SPEC or SUBMITTAL."""
    parts = []

    for doc_id in doc_ids:
        doc = await get_document_with_pages(db, doc_id, org_id)
        if not doc:
            continue

        # Try hybrid extraction first (higher accuracy for vector PDFs)
        import os
        if os.path.exists(doc.file_path):
            hybrid = build_hybrid_context(doc.file_path)
            if hybrid.get("text_context"):
                parts.append(f'=== {label}: "{doc.filename}" (doc_id: {doc.id}) ===')
                parts.append(hybrid["text_context"][:8000])
                parts.append(f"=== END {label} ===\n")
                continue

        # Fallback: use page text
        if doc.pages:
            parts.append(f'=== {label}: "{doc.filename}" (doc_id: {doc.id}) ===')
            for page in doc.pages:
                if page.text_content:
                    parts.append(f"[PAGE {page.page_number}]")
                    text = page.text_content[:3000]
                    if len(page.text_content) > 3000:
                        text += "... [truncated]"
                    parts.append(text)
            parts.append(f"=== END {label} ===\n")

    return "\n".join(parts)


def _parse_json_response(raw: str) -> dict | None:
    """Parse Claude's JSON response, handling markdown wrapping."""
    raw = raw.strip()

    # Remove markdown code fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to find JSON in the response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

    logger.warning("json_parse_failed", raw=raw[:200])
    return None


async def get_report(db: AsyncSession, report_id: int, org_id: int) -> DiscrepancyReport | None:
    """Get a report with all its items."""
    result = await db.execute(
        select(DiscrepancyReport)
        .options(selectinload(DiscrepancyReport.items))
        .where(DiscrepancyReport.id == report_id, DiscrepancyReport.org_id == org_id)
    )
    return result.scalar_one_or_none()


async def list_reports(db: AsyncSession, project_id: int, org_id: int) -> list[DiscrepancyReport]:
    """List all reports for a project."""
    result = await db.execute(
        select(DiscrepancyReport)
        .where(DiscrepancyReport.project_id == project_id, DiscrepancyReport.org_id == org_id)
        .order_by(DiscrepancyReport.created_at.desc())
    )
    return list(result.scalars().all())


async def update_item_status(
    db: AsyncSession,
    item_id: int,
    org_id: int,
    new_status: str,
    user_id: int,
) -> DiscrepancyItem | None:
    """Update a discrepancy item's status (resolve, dismiss, etc.)."""
    result = await db.execute(
        select(DiscrepancyItem)
        .join(DiscrepancyReport)
        .where(DiscrepancyItem.id == item_id, DiscrepancyReport.org_id == org_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        return None

    item.status = new_status
    if new_status in ("resolved", "dismissed"):
        item.resolved_by = user_id
        item.resolved_at = datetime.now(timezone.utc)

    return item


async def delete_report(db: AsyncSession, report_id: int, org_id: int) -> bool:
    """Delete a report and all its items."""
    result = await db.execute(
        select(DiscrepancyReport).where(
            DiscrepancyReport.id == report_id, DiscrepancyReport.org_id == org_id
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        return False

    await db.delete(report)
    return True
