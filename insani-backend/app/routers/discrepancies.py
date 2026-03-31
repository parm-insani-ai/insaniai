"""
Discrepancies Router — Spec vs Submittal comparison.

POST   /v1/discrepancies/analyze          — Start a new comparison
GET    /v1/discrepancies?project_id=X     — List reports for a project
GET    /v1/discrepancies/{id}             — Get full report with findings
PATCH  /v1/discrepancies/items/{id}       — Update a finding's status
DELETE /v1/discrepancies/{id}             — Delete a report
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import structlog

from app.db import get_db
from app.services import discrepancy_service
from app.middleware.auth import require_auth_context, AuthContext

logger = structlog.get_logger()
router = APIRouter(prefix="/v1/discrepancies", tags=["Discrepancies"])


# ── Schemas ──

class AnalyzeRequest(BaseModel):
    project_id: int
    spec_doc_ids: list[int]
    submittal_doc_ids: list[int]
    title: str = ""


class ItemUpdateRequest(BaseModel):
    status: str  # open, acknowledged, resolved, dismissed


class DiscrepancyItemResponse(BaseModel):
    id: int
    severity: str
    category: str
    title: str
    description: str
    spec_reference: str
    spec_doc_id: int | None
    spec_page: int | None
    spec_excerpt: str
    submittal_reference: str
    submittal_doc_id: int | None
    submittal_page: int | None
    submittal_excerpt: str
    recommendation: str
    status: str


class ReportResponse(BaseModel):
    id: int
    title: str
    status: str
    summary: str
    discrepancy_count: int
    spec_doc_ids: list[int]
    submittal_doc_ids: list[int]
    created_at: str | None = None
    items: list[DiscrepancyItemResponse] = []


class ReportListItem(BaseModel):
    id: int
    title: str
    status: str
    discrepancy_count: int
    created_at: str | None = None


# ── Endpoints ──

@router.post("/analyze", response_model=ReportResponse, status_code=201)
async def analyze(
    body: AnalyzeRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Start a spec vs submittal comparison. Runs synchronously and returns the full report."""
    if not body.spec_doc_ids:
        raise HTTPException(status_code=400, detail="At least one spec document is required")
    if not body.submittal_doc_ids:
        raise HTTPException(status_code=400, detail="At least one submittal document is required")

    # Create report
    report = await discrepancy_service.create_report(
        db, ctx.org_id, body.project_id, ctx.user_id,
        body.spec_doc_ids, body.submittal_doc_ids, body.title,
    )
    await db.flush()

    # Run analysis
    report = await discrepancy_service.run_analysis(db, report.id, ctx.org_id)
    await db.commit()

    # Reload with items
    report = await discrepancy_service.get_report(db, report.id, ctx.org_id)

    return _report_to_response(report)


@router.get("/", response_model=list[ReportListItem])
async def list_reports(
    project_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """List all discrepancy reports for a project."""
    reports = await discrepancy_service.list_reports(db, project_id, ctx.org_id)
    return [
        ReportListItem(
            id=r.id,
            title=r.title,
            status=r.status,
            discrepancy_count=r.discrepancy_count,
            created_at=str(r.created_at) if r.created_at else None,
        )
        for r in reports
    ]


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get a full report with all findings."""
    report = await discrepancy_service.get_report(db, report_id, ctx.org_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _report_to_response(report)


@router.patch("/items/{item_id}")
async def update_item(
    item_id: int,
    body: ItemUpdateRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Update a finding's status (resolve, dismiss, etc.)."""
    if body.status not in ("open", "acknowledged", "resolved", "dismissed"):
        raise HTTPException(status_code=400, detail="Invalid status")

    item = await discrepancy_service.update_item_status(db, item_id, ctx.org_id, body.status, ctx.user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await db.commit()

    return {"id": item.id, "status": item.status}


@router.delete("/{report_id}")
async def delete_report(
    report_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Delete a report and all its findings."""
    deleted = await discrepancy_service.delete_report(db, report_id, ctx.org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Report not found")
    await db.commit()
    return {"deleted": True}


def _report_to_response(report) -> ReportResponse:
    items = []
    if hasattr(report, 'items') and report.items:
        items = [
            DiscrepancyItemResponse(
                id=i.id,
                severity=i.severity,
                category=i.category,
                title=i.title,
                description=i.description,
                spec_reference=i.spec_reference,
                spec_doc_id=i.spec_doc_id,
                spec_page=i.spec_page,
                spec_excerpt=i.spec_excerpt,
                submittal_reference=i.submittal_reference,
                submittal_doc_id=i.submittal_doc_id,
                submittal_page=i.submittal_page,
                submittal_excerpt=i.submittal_excerpt,
                recommendation=i.recommendation,
                status=i.status,
            )
            for i in report.items
        ]

    return ReportResponse(
        id=report.id,
        title=report.title,
        status=report.status,
        summary=report.summary,
        discrepancy_count=report.discrepancy_count,
        spec_doc_ids=report.spec_doc_ids or [],
        submittal_doc_ids=report.submittal_doc_ids or [],
        created_at=str(report.created_at) if report.created_at else None,
        items=items,
    )
