"""
Bid Estimating Assistant Agent — Multi-step agent that:
1. Analyzes ITB/RFP documents to extract scope requirements
2. Estimates costs using Halifax-specific rates and local knowledge
3. Checks against Nova Scotia Building Code requirements
4. Identifies risks and missing scope items
5. Generates a complete bid proposal

Uses Halifax geographic data as a competitive moat — local building codes,
permitting timelines, supplier pricing, labor rates, and site condition
knowledge that outside competitors don't have.
"""

import json
import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
from app.services.web_search import search_construction_jobs

from app.config import settings
from app.models.db_models import Document
from app.services.document_service import get_document_with_pages
from app.services.hybrid_extraction import build_hybrid_context
from app.services.halifax_data import (
    NS_BUILDING_CODE,
    HRM_PERMITTING,
    HALIFAX_LABOR_RATES,
    HALIFAX_COST_FACTORS,
    LOCAL_SUPPLIERS,
    JOB_POSTING_SOURCES,
)

logger = structlog.get_logger()
_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


# ═══════════════════════════════════════════════
# STEP 1: Extract scope from ITB/RFP documents
# ═══════════════════════════════════════════════

SCOPE_EXTRACTION_PROMPT = """You are a construction bid estimator reviewing an Invitation to Bid (ITB) or Request for Proposal (RFP) document.
Extract ALL scope requirements, deliverables, and constraints.

Return ONLY valid JSON:
{
  "project_name": "Name of the project",
  "owner": "Project owner / client",
  "location": "Project location",
  "project_type": "Type (residential, commercial, institutional, infrastructure)",
  "bid_deadline": "Submission deadline if mentioned",
  "scope_items": [
    {
      "division": "CSI division (e.g., 03 - Concrete, 09 - Finishes)",
      "description": "Scope item description",
      "quantity": "Quantity if mentioned",
      "unit": "Unit of measure",
      "specifications": "Any spec requirements",
      "notes": "Special conditions or requirements"
    }
  ],
  "general_requirements": {
    "bonding": "Bond requirements (bid bond, performance bond, etc.)",
    "insurance": "Insurance requirements",
    "timeline": "Project duration / milestones",
    "liquidated_damages": "LD provisions if mentioned",
    "retainage": "Retainage percentage",
    "safety": "Safety requirements",
    "local_content": "Atlantic Canada / NS content requirements"
  },
  "special_conditions": ["List of any special conditions"],
  "exclusions": ["Items explicitly excluded from scope"],
  "questions_to_ask": ["Ambiguities or missing information that should be clarified via RFI"]
}"""


async def extract_scope(db: AsyncSession, doc_ids: list[int], org_id: int) -> dict:
    """Step 1: Extract scope requirements from ITB/RFP documents."""

    doc_text = ""
    for doc_id in doc_ids:
        doc = await get_document_with_pages(db, doc_id, org_id)
        if not doc:
            continue

        import os
        if os.path.exists(doc.file_path):
            hybrid = build_hybrid_context(doc.file_path)
            if hybrid.get("text_context"):
                doc_text += f'\n=== ITB/RFP: "{doc.filename}" ===\n{hybrid["text_context"][:8000]}\n'
                continue

        if doc.pages:
            doc_text += f'\n=== ITB/RFP: "{doc.filename}" ===\n'
            for page in doc.pages[:30]:
                if page.text_content:
                    doc_text += f"[PAGE {page.page_number}] {page.text_content[:2000]}\n"

    if not doc_text:
        return {"error": "No text could be extracted from documents"}

    try:
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=4096,
            system=SCOPE_EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": f"Extract the complete scope from this ITB/RFP:\n{doc_text[:20000]}"}],
        )

        raw = "".join(b.text for b in response.content if b.type == "text")
        result = _parse_json(raw)
        if result:
            logger.info("scope_extracted", items=len(result.get("scope_items", [])))
            return result

    except Exception as e:
        logger.error("scope_extraction_failed", error=str(e))

    return {"error": "Failed to extract scope"}


# ═══════════════════════════════════════════════
# STEP 2: Estimate costs using Halifax rates
# ═══════════════════════════════════════════════

COST_ESTIMATION_PROMPT = """You are a construction cost estimator specializing in Halifax, Nova Scotia.
Given scope items from an ITB/RFP, estimate costs using local Halifax rates.

HALIFAX LABOR RATES (CAD/hour):
{labor_rates}

HALIFAX COST FACTORS:
{cost_factors}

NOVA SCOTIA BUILDING CODE REQUIREMENTS:
{building_code}

LOCAL SUPPLIERS:
{suppliers}

CRITICAL HALIFAX-SPECIFIC CONSIDERATIONS:
1. Halifax bedrock (granite) — excavation costs 15-30% higher than average
2. Atlantic Canada shipping premium — materials cost 5-15% more than Ontario
3. Climate Zone 6 — higher insulation requirements (R-24 walls, R-50 roof, triple-pane windows)
4. Coastal exposure — corrosion-resistant materials needed near harbour
5. Winter construction premium — 5-10% for Nov-Mar work
6. HRM permit timelines — 4-8 weeks residential, 8-16 weeks commercial
7. HST is 15% in Nova Scotia

Return ONLY valid JSON:
{{
  "line_items": [
    {{
      "division": "CSI division",
      "description": "Work item",
      "quantity": "Amount",
      "unit": "Unit",
      "unit_cost": 0.00,
      "total_cost": 0.00,
      "labor_hours": 0,
      "labor_cost": 0.00,
      "material_cost": 0.00,
      "notes": "Estimation basis / assumptions"
    }}
  ],
  "subtotals": {{
    "direct_costs": 0.00,
    "general_conditions": 0.00,
    "overhead": 0.00,
    "profit": 0.00,
    "contingency": 0.00,
    "bonding": 0.00,
    "total_before_tax": 0.00,
    "hst": 0.00,
    "total_with_tax": 0.00
  }},
  "schedule": {{
    "estimated_duration": "X months",
    "permit_lead_time": "X weeks",
    "critical_path_items": ["item1", "item2"],
    "seasonal_considerations": "Winter work impacts if applicable"
  }},
  "risks": [
    {{
      "risk": "Description",
      "impact": "Cost / schedule impact",
      "mitigation": "How to mitigate"
    }}
  ]
}}"""


async def estimate_costs(scope: dict) -> dict:
    """Step 2: Estimate costs using Halifax-specific rates."""

    labor_text = json.dumps(HALIFAX_LABOR_RATES["rates_per_hour_cad"], indent=2)[:2000]
    cost_text = json.dumps(HALIFAX_COST_FACTORS, indent=2)[:2000]
    code_text = json.dumps(NS_BUILDING_CODE["key_requirements"], indent=2)[:2000]
    suppliers_text = json.dumps([{"name": s["name"], "type": s["type"], "specialties": s["specialties"]} for s in LOCAL_SUPPLIERS], indent=2)[:1500]

    prompt = COST_ESTIMATION_PROMPT.format(
        labor_rates=labor_text,
        cost_factors=cost_text,
        building_code=code_text,
        suppliers=suppliers_text,
    )

    scope_text = json.dumps(scope, indent=2)[:10000]

    try:
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=4096,
            system=prompt,
            messages=[{"role": "user", "content": f"Estimate costs for this scope in Halifax, NS:\n{scope_text}"}],
        )

        raw = "".join(b.text for b in response.content if b.type == "text")
        result = _parse_json(raw)
        if result:
            logger.info("costs_estimated", line_items=len(result.get("line_items", [])))
            return result

    except Exception as e:
        logger.error("cost_estimation_failed", error=str(e))

    return {"error": "Failed to estimate costs"}


# ═══════════════════════════════════════════════
# STEP 3: Code compliance check
# ═══════════════════════════════════════════════

CODE_CHECK_PROMPT = """You are a Nova Scotia Building Code compliance specialist.
Review this construction scope and cost estimate against NS Building Code requirements.

NOVA SCOTIA BUILDING CODE:
{building_code}

HRM PERMITTING REQUIREMENTS:
{permitting}

Flag any items where:
1. The scope doesn't meet minimum code requirements
2. Permits or approvals are needed that aren't accounted for
3. Energy efficiency requirements may be underspecified
4. Accessibility requirements are missing
5. Fire safety requirements need attention

Return ONLY valid JSON:
{{
  "compliance_status": "compliant|issues_found|review_needed",
  "issues": [
    {{
      "severity": "critical|warning|info",
      "code_reference": "Specific code section",
      "issue": "What's missing or non-compliant",
      "recommendation": "What needs to be added or changed",
      "cost_impact": "Estimated additional cost if any"
    }}
  ],
  "permits_required": [
    {{
      "permit_type": "Type of permit",
      "authority": "Issuing authority",
      "timeline": "Expected timeline",
      "estimated_fee": "Fee if known"
    }}
  ],
  "summary": "Overall compliance assessment"
}}"""


async def check_code_compliance(scope: dict, estimate: dict) -> dict:
    """Step 3: Check against Nova Scotia Building Code."""

    code_text = json.dumps(NS_BUILDING_CODE, indent=2)[:3000]
    permit_text = json.dumps(HRM_PERMITTING, indent=2)[:3000]

    prompt = CODE_CHECK_PROMPT.format(
        building_code=code_text,
        permitting=permit_text,
    )

    combined = {"scope": scope, "estimate_summary": estimate.get("subtotals", {})}
    combined_text = json.dumps(combined, indent=2)[:8000]

    try:
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=2048,
            system=prompt,
            messages=[{"role": "user", "content": f"Check this Halifax construction project for code compliance:\n{combined_text}"}],
        )

        raw = "".join(b.text for b in response.content if b.type == "text")
        result = _parse_json(raw)
        if result:
            logger.info("code_check_complete", issues=len(result.get("issues", [])))
            return result

    except Exception as e:
        logger.error("code_check_failed", error=str(e))

    return {"compliance_status": "review_needed", "issues": [], "summary": "Unable to complete code check"}


# ═══════════════════════════════════════════════
# STEP 4: Generate proposal document
# ═══════════════════════════════════════════════

PROPOSAL_PROMPT = """You are a proposal writer for a Halifax, Nova Scotia construction company.
Generate a professional bid proposal from the scope analysis, cost estimate, and compliance review.

Format the proposal as clean HTML suitable for display:
- Professional tone, confident but accurate
- Highlight Halifax-specific knowledge (local conditions, suppliers, code compliance)
- Include clear pricing summary table
- Note all assumptions and exclusions
- Include a schedule overview
- Mention risk mitigation strategies
- Reference Nova Scotia Building Code compliance

Use these HTML elements:
- <h2>, <h3> for sections
- <table> with <thead> and <tbody> for pricing
- <strong> for key figures
- <p> for paragraphs
- <ul>/<li> for lists
- Style tables with: style="width:100%;border-collapse:collapse;font-size:0.82rem" and cells with style="padding:0.4rem 0.6rem;border-bottom:1px solid #e0e0e0"

Make it look like a real construction bid proposal."""


async def generate_proposal(scope: dict, estimate: dict, compliance: dict) -> str:
    """Step 4: Generate a formatted bid proposal."""

    context = json.dumps({
        "scope": scope,
        "estimate": estimate,
        "compliance": compliance,
        "job_sources": JOB_POSTING_SOURCES,
    }, indent=2)[:12000]

    try:
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=4096,
            system=PROPOSAL_PROMPT,
            messages=[{"role": "user", "content": f"Generate a bid proposal for this Halifax construction project:\n{context}"}],
        )

        return "".join(b.text for b in response.content if b.type == "text")

    except Exception as e:
        logger.error("proposal_generation_failed", error=str(e))
        return "<p>Unable to generate proposal.</p>"


# ═══════════════════════════════════════════════
# FULL PIPELINE — Run all steps
# ═══════════════════════════════════════════════

async def run_bid_analysis(
    db: AsyncSession,
    doc_ids: list[int],
    org_id: int,
) -> dict:
    """
    Full multi-step bid analysis pipeline:
    1. Extract scope from ITB/RFP documents
    2. Estimate costs using Halifax rates
    3. Check NS Building Code compliance
    4. Generate bid proposal

    Returns complete analysis with proposal.
    """
    logger.info("bid_agent_started", doc_ids=doc_ids)

    # Step 1: Extract scope
    scope = await extract_scope(db, doc_ids, org_id)
    if scope.get("error"):
        return {"status": "error", "error": scope["error"]}

    # Step 2: Estimate costs
    estimate = await estimate_costs(scope)
    if estimate.get("error"):
        return {"status": "error", "error": estimate["error"], "scope": scope}

    # Step 3: Code compliance check
    compliance = await check_code_compliance(scope, estimate)

    # Step 4: Generate proposal
    proposal_html = await generate_proposal(scope, estimate, compliance)

    # Step 5: Search for similar live tenders in Halifax
    live_jobs = []
    try:
        live_jobs = await search_construction_jobs("Halifax Nova Scotia")
        logger.info("live_jobs_found", count=len(live_jobs))
    except Exception as e:
        logger.warning("live_job_search_failed", error=str(e))

    logger.info("bid_agent_complete",
        scope_items=len(scope.get("scope_items", [])),
        line_items=len(estimate.get("line_items", [])),
        compliance_issues=len(compliance.get("issues", [])),
    )

    return {
        "status": "complete",
        "scope": scope,
        "estimate": estimate,
        "compliance": compliance,
        "proposal_html": proposal_html,
        "job_sources": JOB_POSTING_SOURCES,
        "live_jobs": live_jobs,
    }


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass
    return None
