"""
Agents Router — Multi-step AI agents for construction intelligence.

POST /v1/agents/materials      — Run material price analysis
POST /v1/agents/bid            — Run bid estimation
GET  /v1/agents/history        — Get agent run history
GET  /v1/agents/history/{id}   — Get a specific agent run result
DELETE /v1/agents/history/{id} — Delete a run
GET  /v1/agents/suppliers      — Get local Halifax supplier list
GET  /v1/agents/rates          — Get Halifax labor rates
GET  /v1/agents/codes          — Get NS building code summary
GET  /v1/agents/job-sources    — Get construction job posting sources
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import structlog

from app.db import get_db
from app.models.db_models import AgentRun
from app.middleware.auth import require_auth_context, AuthContext
from app.services import material_agent, bid_agent
from app.services.halifax_data import (
    LOCAL_SUPPLIERS, HALIFAX_LABOR_RATES, NS_BUILDING_CODE,
    HRM_PERMITTING, JOB_POSTING_SOURCES, HALIFAX_COST_FACTORS,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/v1/agents", tags=["Agents"])


class AgentRequest(BaseModel):
    doc_ids: list[int]
    project_id: int


@router.post("/materials")
async def run_material_analysis(
    body: AgentRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Run multi-step material price analysis."""
    if not body.doc_ids:
        raise HTTPException(status_code=400, detail="At least one document is required")

    try:
        result = await material_agent.run_material_analysis(db, body.doc_ids, ctx.org_id)

        # Save to history
        title = f"Material Analysis — {result.get('materials_found', 0)} materials"
        summary = result.get("summary", {})
        if summary.get("total_mid"):
            title += f" — ${summary['total_mid']:,.0f} est."

        run = AgentRun(
            org_id=ctx.org_id,
            project_id=body.project_id,
            created_by=ctx.user_id,
            agent_type="materials",
            title=title,
            status=result.get("status", "complete"),
            result_json=result,
        )
        db.add(run)
        await db.commit()

        result["run_id"] = run.id
        return result

    except Exception as e:
        logger.error("material_agent_error", error=str(e))
        raise HTTPException(status_code=502, detail="Material analysis failed")


@router.post("/bid")
async def run_bid_analysis(
    body: AgentRequest,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Run multi-step bid estimation."""
    if not body.doc_ids:
        raise HTTPException(status_code=400, detail="At least one document is required")

    try:
        result = await bid_agent.run_bid_analysis(db, body.doc_ids, ctx.org_id)

        # Save to history
        scope = result.get("scope", {})
        title = scope.get("project_name", "Bid Estimate")
        estimate = result.get("estimate", {})
        sub = estimate.get("subtotals", {})
        if sub.get("total_with_tax"):
            title += f" — ${sub['total_with_tax']:,.0f}"

        run = AgentRun(
            org_id=ctx.org_id,
            project_id=body.project_id,
            created_by=ctx.user_id,
            agent_type="bid",
            title=title,
            status=result.get("status", "complete"),
            result_json=result,
        )
        db.add(run)
        await db.commit()

        result["run_id"] = run.id
        return result

    except Exception as e:
        logger.error("bid_agent_error", error=str(e))
        raise HTTPException(status_code=502, detail="Bid analysis failed")


@router.get("/history")
async def get_history(
    project_id: int = Query(...),
    agent_type: str = Query(None),
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get agent run history for a project."""
    query = select(AgentRun).where(
        AgentRun.project_id == project_id,
        AgentRun.org_id == ctx.org_id,
    )
    if agent_type:
        query = query.where(AgentRun.agent_type == agent_type)
    query = query.order_by(AgentRun.created_at.desc()).limit(20)

    result = await db.execute(query)
    runs = list(result.scalars().all())

    return [
        {
            "id": r.id,
            "agent_type": r.agent_type,
            "title": r.title,
            "status": r.status,
            "created_at": str(r.created_at) if r.created_at else None,
        }
        for r in runs
    ]


@router.get("/history/{run_id}")
async def get_run(
    run_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific agent run result."""
    result = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == ctx.org_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.result_json


@router.delete("/history/{run_id}")
async def delete_run(
    run_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Delete an agent run from history."""
    result = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == ctx.org_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await db.delete(run)
    await db.commit()
    return {"deleted": True}


@router.get("/suppliers")
async def get_suppliers(ctx: AuthContext = Depends(require_auth_context)):
    return {"suppliers": LOCAL_SUPPLIERS}


@router.get("/rates")
async def get_labor_rates(ctx: AuthContext = Depends(require_auth_context)):
    return HALIFAX_LABOR_RATES


@router.get("/codes")
async def get_building_codes(ctx: AuthContext = Depends(require_auth_context)):
    return {
        "building_code": NS_BUILDING_CODE,
        "permitting": HRM_PERMITTING,
        "cost_factors": HALIFAX_COST_FACTORS,
    }


@router.get("/job-sources")
async def get_job_sources(ctx: AuthContext = Depends(require_auth_context)):
    return {"sources": JOB_POSTING_SOURCES}
