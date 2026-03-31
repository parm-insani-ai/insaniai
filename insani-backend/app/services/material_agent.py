"""
Material Price Tracker Agent — Multi-step agent that:
1. Extracts material requirements from uploaded project documents
2. Searches supplier websites for current pricing
3. Compares prices across local Halifax suppliers
4. Generates cost optimization recommendations

Uses Claude as the reasoning engine, with Halifax-specific supplier
data and local cost factors baked in.
"""

import json
import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.config import settings
from app.services.web_search import search_material_prices, search_supplier
from app.models.db_models import Document
from app.services.document_service import get_document_with_pages
from app.services.hybrid_extraction import build_hybrid_context
from app.services.halifax_data import (
    LOCAL_SUPPLIERS,
    HALIFAX_COST_FACTORS,
    HALIFAX_LABOR_RATES,
)

logger = structlog.get_logger()
_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


# ═══════════════════════════════════════════════
# STEP 1: Extract materials from documents
# ═══════════════════════════════════════════════

MATERIAL_EXTRACTION_PROMPT = """You are a construction materials quantity surveyor analyzing project documents.
Extract ALL materials mentioned with quantities where available.

Return ONLY valid JSON:
{
  "materials": [
    {
      "name": "Material name (e.g., 2x6 SPF Lumber)",
      "category": "lumber|concrete|steel|insulation|roofing|drywall|plumbing|electrical|masonry|flooring|paint|hardware|other",
      "specification": "Any spec details (grade, size, type, standard)",
      "quantity": "Quantity if mentioned (e.g., '450 LF', '200 sheets', '15 CY')",
      "unit": "Unit of measure (LF, SF, CY, EA, sheets, etc.)",
      "notes": "Any special requirements or conditions"
    }
  ],
  "total_categories": {"lumber": 5, "concrete": 2, ...}
}

Be thorough — extract every material reference including:
- Structural materials (concrete, steel, lumber, masonry)
- Finishes (drywall, paint, flooring, tile)
- MEP materials (pipe, wire, duct, fixtures)
- Insulation and air/vapour barriers
- Roofing and waterproofing
- Hardware and fasteners"""


async def extract_materials(db: AsyncSession, doc_ids: list[int], org_id: int) -> dict:
    """Step 1: Extract material list from uploaded documents."""

    doc_text = ""
    for doc_id in doc_ids:
        doc = await get_document_with_pages(db, doc_id, org_id)
        if not doc:
            continue

        # Use hybrid extraction for accuracy
        import os
        if os.path.exists(doc.file_path):
            hybrid = build_hybrid_context(doc.file_path)
            if hybrid.get("text_context"):
                doc_text += f'\n=== DOCUMENT: "{doc.filename}" ===\n{hybrid["text_context"][:6000]}\n'
                continue

        # Fallback to page text
        if doc.pages:
            doc_text += f'\n=== DOCUMENT: "{doc.filename}" ===\n'
            for page in doc.pages[:20]:
                if page.text_content:
                    doc_text += f"[PAGE {page.page_number}] {page.text_content[:2000]}\n"

    if not doc_text:
        return {"materials": [], "error": "No text could be extracted from documents"}

    try:
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=4096,
            system=MATERIAL_EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": f"Extract all materials from these documents:\n{doc_text[:15000]}"}],
        )

        raw = "".join(b.text for b in response.content if b.type == "text")
        result = _parse_json(raw)
        if result:
            logger.info("materials_extracted", count=len(result.get("materials", [])))
            return result

    except Exception as e:
        logger.error("material_extraction_failed", error=str(e))

    return {"materials": [], "error": "Failed to extract materials"}


# ═══════════════════════════════════════════════
# STEP 2: Search for pricing
# ═══════════════════════════════════════════════

PRICING_PROMPT = """You are a construction cost estimator for Halifax, Nova Scotia.
Given a list of materials, estimate current market pricing from local Halifax suppliers.

LOCAL SUPPLIERS IN HALIFAX:
{suppliers}

HALIFAX COST FACTORS:
{cost_factors}

IMPORTANT:
- Use Canadian Dollars (CAD)
- Factor in Atlantic Canada shipping premiums (5-15% over Ontario prices)
- Consider Halifax's coastal climate requirements (corrosion resistance near harbour)
- Include Nova Scotia HST (15%) note
- Reference specific local suppliers where appropriate
- Be honest about price ranges — give low/mid/high estimates
- Note seasonal variations (lumber peaks in spring/summer)

Return ONLY valid JSON:
{{
  "materials": [
    {{
      "name": "Material name",
      "specification": "Spec details",
      "quantity": "Quantity needed",
      "unit_price_low": 0.00,
      "unit_price_mid": 0.00,
      "unit_price_high": 0.00,
      "total_low": 0.00,
      "total_mid": 0.00,
      "total_high": 0.00,
      "best_supplier": "Recommended local supplier",
      "alt_supplier": "Alternative supplier",
      "notes": "Pricing notes, seasonal factors, bulk discounts"
    }}
  ],
  "summary": {{
    "total_low": 0.00,
    "total_mid": 0.00,
    "total_high": 0.00,
    "currency": "CAD",
    "tax_note": "Add 15% HST",
    "savings_tips": ["tip1", "tip2"]
  }}
}}"""


async def estimate_pricing(materials: list[dict]) -> dict:
    """Step 2: Estimate pricing from local Halifax suppliers."""

    suppliers_text = json.dumps(LOCAL_SUPPLIERS, indent=2)[:3000]
    cost_factors_text = json.dumps(HALIFAX_COST_FACTORS, indent=2)[:2000]

    prompt = PRICING_PROMPT.format(
        suppliers=suppliers_text,
        cost_factors=cost_factors_text,
    )

    materials_text = json.dumps(materials[:50], indent=2)

    try:
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_SMART,
            max_tokens=4096,
            system=prompt,
            messages=[{"role": "user", "content": f"Estimate pricing for these materials in Halifax, NS:\n{materials_text}"}],
        )

        raw = "".join(b.text for b in response.content if b.type == "text")
        result = _parse_json(raw)
        if result:
            logger.info("pricing_estimated", materials=len(result.get("materials", [])))
            return result

    except Exception as e:
        logger.error("pricing_estimation_failed", error=str(e))

    return {"materials": [], "error": "Failed to estimate pricing"}


# ═══════════════════════════════════════════════
# STEP 3: Generate optimization recommendations
# ═══════════════════════════════════════════════

OPTIMIZATION_PROMPT = """You are a construction cost optimization specialist for Halifax, Nova Scotia.
Given material pricing data, generate specific cost-saving recommendations.

HALIFAX LABOR RATES:
{labor_rates}

Consider:
1. Bulk purchasing opportunities (which materials to buy together)
2. Seasonal timing (when to buy for best prices in Atlantic Canada)
3. Local vs imported materials (Atlantic Canada content requirements)
4. Alternative materials that meet Nova Scotia Building Code requirements
5. Waste reduction strategies
6. Supplier negotiation leverage points

Return practical, actionable recommendations formatted as HTML:
- Use <strong>bold</strong> for key numbers and savings
- Use <p> tags for paragraphs
- Use bullet lists for recommendations
- Be specific to Halifax/Nova Scotia"""


async def generate_recommendations(pricing_data: dict) -> str:
    """Step 3: Generate cost optimization recommendations."""

    labor_text = json.dumps(HALIFAX_LABOR_RATES, indent=2)[:2000]

    try:
        response = await _client.messages.create(
            model=settings.ANTHROPIC_MODEL_FAST,
            max_tokens=2048,
            system=OPTIMIZATION_PROMPT.format(labor_rates=labor_text),
            messages=[{"role": "user", "content": f"Generate cost optimization recommendations for this material estimate:\n{json.dumps(pricing_data, indent=2)[:8000]}"}],
        )

        return "".join(b.text for b in response.content if b.type == "text")

    except Exception as e:
        logger.error("optimization_failed", error=str(e))
        return "Unable to generate recommendations."


# ═══════════════════════════════════════════════
# FULL PIPELINE — Run all steps
# ═══════════════════════════════════════════════

async def run_material_analysis(
    db: AsyncSession,
    doc_ids: list[int],
    org_id: int,
) -> dict:
    """
    Full multi-step material analysis pipeline:
    1. Extract materials from documents
    2. Estimate pricing from local suppliers
    3. Generate optimization recommendations

    Returns complete analysis result.
    """
    logger.info("material_agent_started", doc_ids=doc_ids)

    # Step 1: Extract materials
    extraction = await extract_materials(db, doc_ids, org_id)
    materials = extraction.get("materials", [])

    if not materials:
        return {
            "status": "error",
            "error": extraction.get("error", "No materials found in documents"),
            "materials": [],
            "pricing": {},
            "recommendations": "",
            "web_results": [],
        }

    # Step 1.5: Search web for real prices on top materials
    web_results = []
    try:
        # Search for the top 5 most important materials
        top_materials = materials[:5]
        for mat in top_materials:
            name = mat.get("name", "")
            if name:
                prices = await search_material_prices(name, "Halifax NS")
                if prices:
                    web_results.append({"material": name, "sources": prices})
        logger.info("web_price_search_done", materials_searched=len(top_materials), results=len(web_results))
    except Exception as e:
        logger.warning("web_price_search_failed", error=str(e))

    # Inject web results into the pricing step
    if web_results:
        for mat in materials:
            for wr in web_results:
                if wr["material"].lower() == mat.get("name", "").lower():
                    mat["web_prices"] = wr["sources"]

    # Step 2: Estimate pricing (now with web data)
    pricing = await estimate_pricing(materials)

    # Step 3: Generate recommendations
    recommendations = await generate_recommendations(pricing)

    logger.info("material_agent_complete", materials=len(materials))

    return {
        "status": "complete",
        "materials_found": len(materials),
        "categories": extraction.get("total_categories", {}),
        "materials": pricing.get("materials", []),
        "summary": pricing.get("summary", {}),
        "recommendations": recommendations,
        "suppliers": LOCAL_SUPPLIERS,
        "web_results": web_results,
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
