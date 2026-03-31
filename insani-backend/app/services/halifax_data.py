"""
Halifax Geographic Data — Local building codes, suppliers, labor rates,
permitting processes, and zoning data for Halifax Regional Municipality.

This is the GEOGRAPHIC MOAT — hard-to-replicate local knowledge that
makes insani uniquely valuable for Halifax-area construction companies.
"""

# ═══════════════════════════════════════════════
# NOVA SCOTIA BUILDING CODE & REGULATIONS
# ═══════════════════════════════════════════════

NS_BUILDING_CODE = {
    "code_name": "Nova Scotia Building Code Regulations",
    "base_code": "National Building Code of Canada 2020 (NBC 2020)",
    "amendments": "Nova Scotia amendments under the Building Code Act",
    "authority": "Nova Scotia Department of Municipal Affairs and Housing",
    "key_requirements": {
        "energy_efficiency": {
            "standard": "NECB 2017 or NBC 9.36 prescriptive path",
            "climate_zone": "Zone 6 (Halifax)",
            "min_r_values": {
                "walls_above_grade": "R-24 effective",
                "roof_ceiling": "R-50",
                "basement_walls": "R-20",
                "slab_on_grade": "R-10 (2ft perimeter)",
                "windows": "U-1.6 max (triple-pane recommended)",
            },
            "air_barrier": "Required — max 1.5 ACH @50Pa for Part 9 buildings",
            "hrv": "Required for all new residential construction",
        },
        "structural": {
            "snow_load": "2.2 kPa (ground snow load for Halifax)",
            "rain_load": "0.3 kPa",
            "wind_pressure": "0.45 kPa (1/50 year hourly wind pressure)",
            "seismic": "Site Class C default, PGA = 0.12g",
            "frost_depth": "1.5m (5 feet) minimum footing depth",
        },
        "fire": {
            "residential_separations": "1-hour fire separation between dwelling units",
            "sprinklers": "Required for buildings over 3 storeys or 600m2 per floor",
            "smoke_alarms": "Required on every storey and in every sleeping room",
        },
        "accessibility": {
            "standard": "CSA B651-18 Accessible Design for the Built Environment",
            "barrier_free": "Required for all public buildings",
        },
    },
}

# ═══════════════════════════════════════════════
# HRM PERMITTING & ZONING
# ═══════════════════════════════════════════════

HRM_PERMITTING = {
    "authority": "Halifax Regional Municipality — Planning & Development",
    "portal": "https://www.halifax.ca/business/planning-development/applications/building-permits",
    "permit_types": {
        "building_permit": {
            "required_for": "New construction, additions, renovations over $5,000",
            "typical_timeline": "4-8 weeks (residential), 8-16 weeks (commercial)",
            "fees": "Based on construction value — approximately $10-15 per $1,000 of construction value",
            "documents_required": [
                "Site plan showing setbacks and lot coverage",
                "Floor plans, elevations, cross-sections",
                "Structural drawings (stamped by P.Eng for Part 4 buildings)",
                "Energy compliance documentation (EnerGuide or NECB)",
                "Septic system design (if not on municipal sewer)",
                "Plot plan from licensed Nova Scotia Land Surveyor",
            ],
        },
        "development_permit": {
            "required_for": "New development, change of use, variances",
            "typical_timeline": "6-12 weeks",
            "public_hearing": "Required for variances and rezonings",
        },
        "demolition_permit": {
            "required_for": "Demolition of any structure",
            "heritage": "Additional approval required in Heritage Conservation Districts",
        },
        "blasting_permit": {
            "note": "Common in Halifax due to granite bedrock",
            "authority": "Nova Scotia Department of Natural Resources and Renewables",
        },
    },
    "zoning_categories": {
        "R-1": "Single-unit dwelling",
        "R-2": "Two-unit dwelling",
        "R-2A": "Two-unit dwelling (alternate)",
        "R-3": "Low-density multiple-unit dwelling",
        "R-4": "Multiple-unit dwelling",
        "C-1": "Minor commercial",
        "C-2": "General business",
        "C-3": "Highway commercial",
        "I-1": "Light industrial",
        "I-2": "General industrial",
        "DH": "Downtown Halifax",
        "DH-1": "Downtown Halifax waterfront",
    },
    "setbacks": {
        "R-1": {"front": "6.0m", "side": "1.5m", "rear": "7.5m"},
        "R-2": {"front": "6.0m", "side": "1.5m", "rear": "7.5m"},
        "C-2": {"front": "0m", "side": "0m", "rear": "6.0m"},
    },
}

# ═══════════════════════════════════════════════
# LOCAL SUPPLIERS — Halifax/Nova Scotia
# ═══════════════════════════════════════════════

LOCAL_SUPPLIERS = [
    {
        "name": "Kent Building Supplies",
        "type": "General building materials",
        "locations": ["Halifax (Bayers Lake)", "Dartmouth", "Bedford"],
        "website": "https://www.kent.ca",
        "specialties": ["Lumber", "Roofing", "Drywall", "Insulation", "Hardware"],
        "pricing_note": "Atlantic Canadian chain — competitive on lumber and framing",
    },
    {
        "name": "Home Hardware Building Centre",
        "type": "General building materials",
        "locations": ["Multiple Halifax locations"],
        "website": "https://www.homehardware.ca",
        "specialties": ["Lumber", "Paint", "Plumbing", "Electrical"],
        "pricing_note": "Dealer-owned — pricing varies by location",
    },
    {
        "name": "Home Depot",
        "type": "General building materials",
        "locations": ["Halifax (Bayers Lake)", "Dartmouth Crossing"],
        "website": "https://www.homedepot.ca",
        "specialties": ["General materials", "Tools", "Appliances"],
        "pricing_note": "National pricing — good for price comparison baseline",
    },
    {
        "name": "Rona",
        "type": "General building materials",
        "locations": ["Halifax", "Dartmouth"],
        "website": "https://www.rona.ca",
        "specialties": ["Lumber", "Plumbing", "Electrical", "Paint"],
        "pricing_note": "Owned by Lowe's Canada",
    },
    {
        "name": "BMR Atlantic",
        "type": "Contractor supply",
        "locations": ["Dartmouth"],
        "website": "https://www.bmr.co",
        "specialties": ["Framing lumber", "Engineered wood", "Trusses"],
        "pricing_note": "Contractor-focused — bulk pricing available",
    },
    {
        "name": "Halifax Specialty Hardwoods",
        "type": "Hardwood lumber",
        "locations": ["Burnside Industrial Park"],
        "website": "https://www.halifaxhardwoods.com",
        "specialties": ["Hardwood lumber", "Hardwood plywood", "Specialty wood"],
        "pricing_note": "Specialty — premium pricing for quality hardwoods",
    },
    {
        "name": "Moffatt & Powell",
        "type": "Concrete & aggregates",
        "locations": ["Halifax metro"],
        "website": "N/A",
        "specialties": ["Ready-mix concrete", "Aggregates", "Precast"],
        "pricing_note": "Local concrete supplier — seasonal pricing varies",
    },
    {
        "name": "Shaw Brick",
        "type": "Masonry products",
        "locations": ["Lantz, NS (serves Halifax metro)"],
        "website": "https://www.shawbrick.ca",
        "specialties": ["Clay brick", "Concrete block", "Stone veneer", "Pavers"],
        "pricing_note": "NS manufacturer — competitive on masonry",
    },
    {
        "name": "Steelform Building Products",
        "type": "Steel studs & framing",
        "locations": ["Burnside Industrial Park"],
        "website": "https://www.steelform.ca",
        "specialties": ["Steel studs", "Track", "Accessories", "Drywall"],
        "pricing_note": "Atlantic Canada steel framing specialist",
    },
    {
        "name": "Guildfords",
        "type": "Plumbing & HVAC",
        "locations": ["Multiple NS locations"],
        "website": "https://www.guildfords.com",
        "specialties": ["Plumbing", "HVAC", "Hydronics", "Water treatment"],
        "pricing_note": "Atlantic Canada distributor — contractor accounts",
    },
    {
        "name": "Electrical Distributors (EWS)",
        "type": "Electrical supply",
        "locations": ["Halifax", "Dartmouth"],
        "website": "https://www.ewsgroup.com",
        "specialties": ["Electrical wire", "Panels", "Lighting", "Controls"],
        "pricing_note": "Regional electrical wholesaler",
    },
]

# ═══════════════════════════════════════════════
# HALIFAX LABOR RATES (2024-2026 estimates)
# ═══════════════════════════════════════════════

HALIFAX_LABOR_RATES = {
    "source": "Nova Scotia Construction Association / industry averages",
    "note": "Rates include benefits and overhead — adjust for specific company rates",
    "rates_per_hour_cad": {
        "general_laborer": {"low": 22, "mid": 28, "high": 35},
        "carpenter": {"low": 30, "mid": 38, "high": 48},
        "electrician": {"low": 35, "mid": 45, "high": 58},
        "plumber": {"low": 35, "mid": 44, "high": 55},
        "hvac_technician": {"low": 35, "mid": 45, "high": 58},
        "concrete_finisher": {"low": 28, "mid": 36, "high": 45},
        "ironworker": {"low": 35, "mid": 45, "high": 55},
        "drywaller": {"low": 26, "mid": 34, "high": 42},
        "painter": {"low": 24, "mid": 32, "high": 40},
        "roofer": {"low": 28, "mid": 36, "high": 45},
        "mason": {"low": 32, "mid": 42, "high": 52},
        "heavy_equipment_operator": {"low": 30, "mid": 40, "high": 50},
        "project_manager": {"low": 45, "mid": 60, "high": 85},
        "site_superintendent": {"low": 40, "mid": 55, "high": 75},
    },
    "overtime": "1.5x after 48 hours/week (NS Labour Standards Code)",
    "holiday_pay": "6% of gross earnings (NS minimum)",
}

# ═══════════════════════════════════════════════
# CONSTRUCTION JOB SOURCES — Atlantic Canada
# ═══════════════════════════════════════════════

JOB_POSTING_SOURCES = [
    {
        "name": "MERX Canadian Public Tenders",
        "url": "https://www.merx.com",
        "type": "Public procurement",
        "coverage": "Federal, provincial, municipal tenders across Canada",
        "halifax_filter": "Filter by Nova Scotia / Halifax Regional Municipality",
    },
    {
        "name": "Nova Scotia Procurement",
        "url": "https://procurement.novascotia.ca",
        "type": "Provincial government",
        "coverage": "Nova Scotia provincial contracts",
        "halifax_filter": "All NS provincial construction tenders",
    },
    {
        "name": "Halifax Regional Municipality Tenders",
        "url": "https://www.halifax.ca/business/doing-business-municipality/tenders",
        "type": "Municipal government",
        "coverage": "HRM municipal construction projects",
        "halifax_filter": "Direct — all Halifax municipal",
    },
    {
        "name": "Atlantic Provinces Opportunities Agency (APOA)",
        "url": "https://www.canada.ca/en/atlantic-canada-opportunities.html",
        "type": "Federal regional",
        "coverage": "Atlantic Canada infrastructure projects",
        "halifax_filter": "Filter by Nova Scotia",
    },
    {
        "name": "Construction Association of Nova Scotia (CANS)",
        "url": "https://www.cans.ns.ca",
        "type": "Industry association",
        "coverage": "Private and public construction opportunities in NS",
        "halifax_filter": "Member access — Halifax metro projects",
    },
    {
        "name": "BidCentral (formerly dcnonl.com)",
        "url": "https://www.bidcentral.ca",
        "type": "Private and public",
        "coverage": "Atlantic Canada construction opportunities",
        "halifax_filter": "Regional filter available",
    },
]

# ═══════════════════════════════════════════════
# HALIFAX-SPECIFIC COST FACTORS
# ═══════════════════════════════════════════════

HALIFAX_COST_FACTORS = {
    "site_conditions": {
        "rock_excavation": "Very common — Halifax sits on granite bedrock. Budget 15-30% premium for excavation.",
        "blasting": "Frequently required for foundations. Typical cost: $15-30/m3 for rock removal.",
        "soil_conditions": "Till over bedrock in most areas. Geotechnical investigation strongly recommended.",
        "water_table": "Variable — can be high near harbour and lakes. Dewatering may be needed.",
    },
    "climate_factors": {
        "winter_construction": "Nov-Mar requires winter protection. Budget 5-10% premium for winter work.",
        "salt_air": "Coastal exposure requires corrosion-resistant materials near harbour.",
        "wind": "Higher wind loads than inland locations — affects crane operations and temporary structures.",
        "fog": "Frequent fog can slow exterior work — factor into schedule.",
    },
    "transportation": {
        "shipping_premium": "Atlantic Canada pays 5-15% more for materials shipped from central Canada.",
        "local_preference": "Many public projects have Atlantic Canada content requirements.",
        "ferry_access": "Some island communities require ferry transport of materials.",
    },
    "regulatory": {
        "heritage_districts": "Additional approvals needed in Heritage Conservation Districts (Old Town Halifax, etc.)",
        "wetland_protection": "NS Environment Act — wetland alteration requires provincial approval.",
        "coastal_protection": "Coastal setbacks apply — typically 3.8m vertical geodetic datum.",
    },
}
