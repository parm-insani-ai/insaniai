"""
Agents Router — Multi-step AI agents for construction intelligence.

POST /v1/agents/materials   — Run material price analysis on documents
POST /v1/agents/bid         — Run bid estimation on ITB/RFP documents
GET  /v1/agents/suppliers   — Get local Halifax supplier list
GET  /v1/agents/rates       — Get Halifax labor rates
GET  /v1/agents/codes       — Get NS building code summary
GET  /v1/agents/job-sources — Get construction job posting sources
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import structlog

from app.db import get_db
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
    """Run multi-step material price analysis.
    Steps: Extract materials → Estimate pricing → Generate recommendations"""
    if not body.doc_ids:
        raise HTTPException(status_code=400, detail="At least one document is required")

    try:
        result = await material_agent.run_material_analysis(db, body.doc_ids, ctx.org_id)
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
    """Run multi-step bid estimation.
    Steps: Extract scope → Estimate costs → Check code compliance → Generate proposal"""
    if not body.doc_ids:
        raise HTTPException(status_code=400, detail="At least one document is required")

    try:
        result = await bid_agent.run_bid_analysis(db, body.doc_ids, ctx.org_id)
        return result
    except Exception as e:
        logger.error("bid_agent_error", error=str(e))
        raise HTTPException(status_code=502, detail="Bid analysis failed")


@router.get("/suppliers")
async def get_suppliers(ctx: AuthContext = Depends(require_auth_context)):
    """Get list of local Halifax construction suppliers."""
    return {"suppliers": LOCAL_SUPPLIERS}


@router.get("/rates")
async def get_labor_rates(ctx: AuthContext = Depends(require_auth_context)):
    """Get Halifax construction labor rates."""
    return HALIFAX_LABOR_RATES


@router.get("/codes")
async def get_building_codes(ctx: AuthContext = Depends(require_auth_context)):
    """Get Nova Scotia Building Code summary."""
    return {
        "building_code": NS_BUILDING_CODE,
        "permitting": HRM_PERMITTING,
        "cost_factors": HALIFAX_COST_FACTORS,
    }


@router.get("/job-sources")
async def get_job_sources(ctx: AuthContext = Depends(require_auth_context)):
    """Get construction job posting sources for Halifax/Atlantic Canada."""
    return {"sources": JOB_POSTING_SOURCES}
