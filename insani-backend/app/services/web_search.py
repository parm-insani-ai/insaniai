"""
Web Search Service — Gives AI agents real-time web access.

Capabilities:
1. Search Google for current pricing, job postings, market data
2. Fetch and extract content from supplier websites
3. Parse construction tender listings

Uses httpx (already installed) for HTTP requests.
"""

import re
import httpx
import structlog
from urllib.parse import quote_plus

logger = structlog.get_logger()

# Shared HTTP client with reasonable timeouts
_http = httpx.AsyncClient(
    timeout=15.0,
    follow_redirects=True,
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
    },
)


# ═══════════════════════════════════════════════
# GOOGLE SEARCH
# ═══════════════════════════════════════════════

async def search_google(query: str, num_results: int = 5) -> list[dict]:
    """
    Search Google and return results with titles, URLs, and snippets.
    Uses the Google search page directly (no API key needed).
    """
    try:
        url = f"https://www.google.com/search?q={quote_plus(query)}&num={num_results}&hl=en&gl=ca"
        response = await _http.get(url)

        if response.status_code != 200:
            logger.warning("google_search_failed", status=response.status_code, query=query[:50])
            return []

        html = response.text
        results = []

        # Extract search results from HTML
        # Look for result blocks
        blocks = re.findall(r'<div class="[^"]*">.*?<a href="(/url\?q=|)(https?://[^"&]+).*?</a>.*?</div>', html, re.DOTALL)

        for _, link in blocks[:num_results]:
            if not link or 'google.com' in link:
                continue
            results.append({"url": link, "title": "", "snippet": ""})

        # Fallback: try simpler URL extraction
        if not results:
            urls = re.findall(r'href="(https?://(?:www\.)?(?:kent\.ca|homedepot\.ca|rona\.ca|lowes\.ca|merx\.com|novascotia\.ca|halifax\.ca|cans\.ns\.ca|bidcentral\.ca)[^"]*)"', html)
            for u in urls[:num_results]:
                results.append({"url": u, "title": "", "snippet": ""})

        logger.info("google_search_complete", query=query[:50], results=len(results))
        return results

    except Exception as e:
        logger.warning("google_search_error", query=query[:50], error=str(e))
        return []


# ═══════════════════════════════════════════════
# WEBSITE CONTENT FETCHER
# ═══════════════════════════════════════════════

async def fetch_page(url: str, max_chars: int = 5000) -> dict:
    """
    Fetch a web page and extract readable text content.
    Returns {url, title, text, success}.
    """
    try:
        response = await _http.get(url)

        if response.status_code != 200:
            return {"url": url, "title": "", "text": "", "success": False}

        html = response.text

        # Extract title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        # Strip HTML tags to get text
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return {
            "url": url,
            "title": title[:200],
            "text": text[:max_chars],
            "success": True,
        }

    except Exception as e:
        logger.warning("fetch_page_error", url=url[:80], error=str(e))
        return {"url": url, "title": "", "text": "", "success": False}


# ═══════════════════════════════════════════════
# MATERIAL PRICE SEARCH
# ═══════════════════════════════════════════════

async def search_material_prices(material_name: str, location: str = "Halifax NS") -> list[dict]:
    """
    Search for current prices of a construction material.
    Searches Canadian supplier websites.
    """
    queries = [
        f"{material_name} price {location}",
        f"{material_name} buy Canada building supply",
    ]

    all_results = []
    for query in queries:
        results = await search_google(query, num_results=3)
        all_results.extend(results)

    # Fetch content from top results
    price_data = []
    for result in all_results[:4]:
        page = await fetch_page(result["url"])
        if page["success"]:
            # Try to extract prices from the page text
            prices = extract_prices(page["text"])
            price_data.append({
                "source": result["url"],
                "title": page["title"],
                "prices_found": prices,
                "context": page["text"][:1000],
            })

    logger.info("material_price_search", material=material_name, sources=len(price_data))
    return price_data


def extract_prices(text: str) -> list[str]:
    """Extract price-like patterns from text."""
    # Match Canadian dollar prices
    patterns = [
        r'\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?',  # $1,234.56
        r'CAD\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?',  # CAD $1,234.56
        r'\d{1,3}(?:,\d{3})*(?:\.\d{2})?\s*(?:CAD|CDN)',  # 1,234.56 CAD
    ]

    prices = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            price = match.group(0).strip()
            if price not in prices:
                prices.append(price)

    return prices[:10]


# ═══════════════════════════════════════════════
# CONSTRUCTION JOB SEARCH
# ═══════════════════════════════════════════════

async def search_construction_jobs(location: str = "Halifax Nova Scotia") -> list[dict]:
    """
    Search for current construction job postings / tenders in the area.
    """
    queries = [
        f"construction tender {location} 2026",
        f"construction bid invitation {location}",
        f"site:merx.com construction {location}",
        f"site:novascotia.ca construction tender",
        f"site:halifax.ca tender construction",
    ]

    all_jobs = []
    seen_urls = set()

    for query in queries:
        results = await search_google(query, num_results=3)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                page = await fetch_page(r["url"])
                if page["success"] and len(page["text"]) > 100:
                    all_jobs.append({
                        "source": r["url"],
                        "title": page["title"],
                        "content": page["text"][:2000],
                    })

    logger.info("job_search_complete", location=location, jobs=len(all_jobs))
    return all_jobs


# ═══════════════════════════════════════════════
# SUPPLIER WEBSITE SEARCH
# ═══════════════════════════════════════════════

SUPPLIER_URLS = {
    "kent": "https://www.kent.ca",
    "homedepot": "https://www.homedepot.ca",
    "rona": "https://www.rona.ca",
    "homehardware": "https://www.homehardware.ca",
}


async def search_supplier(supplier: str, product: str) -> dict:
    """
    Search a specific supplier's website for a product.
    """
    base_url = SUPPLIER_URLS.get(supplier.lower())
    if not base_url:
        return {"supplier": supplier, "product": product, "results": [], "success": False}

    # Search Google with site restriction
    query = f"site:{base_url.replace('https://', '')} {product}"
    results = await search_google(query, num_results=3)

    product_data = []
    for r in results:
        page = await fetch_page(r["url"])
        if page["success"]:
            prices = extract_prices(page["text"])
            product_data.append({
                "url": r["url"],
                "title": page["title"],
                "prices": prices,
                "text": page["text"][:500],
            })

    return {
        "supplier": supplier,
        "product": product,
        "results": product_data,
        "success": bool(product_data),
    }
