"""
AI Service — Async Claude API integration.

Uses anthropic.AsyncAnthropic so Claude calls don't block the
event loop. The API key lives here and never reaches the client.
"""

import json
import re
import anthropic
from app.config import settings
import structlog

logger = structlog.get_logger()

# Async client — non-blocking API calls
client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


def build_system_prompt(project_data: dict, document_context: str = "") -> str:
    """Build system prompt with project data and optional document content."""
    from app.services.document_service import build_citation_prompt_addition

    base = f"""You are insani — an AI copilot for construction. You connect siloed data from Procore, Autodesk, Sage, Primavera, email, and drawings into one conversation.

PROJECT DATA:
{json.dumps(project_data, indent=2)}

DATE: March 15, 2026

FORMAT YOUR RESPONSES WITH HTML:
- Citations: <span class="cite cite-default">Source</span> or <span class="cite cite-blue">Autodesk</span> or <span class="cite cite-orange">Budget source</span>
- Risks: <div class="risk-box"><span class="risk-icon">⚠</span><span>CONTENT</span></div>
- Actions: <div class="action-box"><span>✓</span><span>CONTENT</span></div>
- Use <strong>bold</strong> for IDs, values, dates. Use <p> and <br> for structure.
- Reference actual IDs, dates, people, amounts from the data.
- Be precise, data-driven, actionable. Lead with the answer.
- Do NOT include any JSON blocks."""

    if document_context:
        # Check if the context contains synced integration data (emails, invoices)
        has_synced_data = "=== GMAIL" in document_context or "=== QUICKBOOKS" in document_context
        has_documents = "=== DOCUMENT:" in document_context
        has_drawings = "DRAWING INDEX" in document_context or "--- DRAWING PAGE" in document_context
        has_extracted_data = "DIRECTLY EXTRACTED DATA" in document_context

        if has_drawings:
            base += """

DRAWING CITATION RULES:
When referencing information from a drawing/blueprint:
1. Cite using: <span class="drawing-cite" data-doc-id="DOC_ID" data-page="PAGE">Sheet SHEET_NUM, DESCRIPTION</span>
2. ONLY state what you can actually verify. Never guess or fabricate.
3. If something is unclear, say so explicitly rather than guessing.
4. When citing dimensions, reproduce them EXACTLY as shown."""

        if has_extracted_data:
            base += """

ACCURACY PRIORITY RULES (CRITICAL):
The DIRECTLY EXTRACTED DATA sections contain text pulled programmatically from the PDF file.
This data is MORE ACCURATE than reading from images. When answering:
1. For dimensions, measurements, text, specs, notes: PREFER the directly extracted data.
2. For spatial relationships, layouts, symbol identification: Use the drawing images.
3. If extracted data and image analysis conflict, trust the extracted data for text/numbers.
4. Always cite the specific page where you found the information."""

        if has_documents:
            base += f"""

UPLOADED DOCUMENTS (search these for answers):
{document_context}
{build_citation_prompt_addition()}"""
        elif has_synced_data:
            from app.integrations.sync_service import build_email_citation_prompt
            base += f"""

SYNCED DATA FROM CONNECTED INTEGRATIONS:
{document_context}
{build_email_citation_prompt()}"""
        else:
            base += f"""

ADDITIONAL CONTEXT:
{document_context}"""

        # If both documents and synced data exist
        if has_documents and has_synced_data:
            from app.integrations.sync_service import build_email_citation_prompt
            base += f"""
{build_email_citation_prompt()}"""

    return base


async def ask_claude(
    message: str,
    project_data: dict,
    conversation_history: list[dict],
    files: list[dict] | None = None,
    document_context: str = "",
    drawing_images: list[dict] | None = None,
) -> str:
    """
    Send a message to Claude asynchronously.

    Returns the AI's response text.
    Raises a sanitized error on failure (no API key leakage).

    drawing_images: optional list of {page_number, base64, doc_id} for
    blueprint vision queries injected from the chat pipeline.
    """
    # Build multimodal or text-only content
    user_content = []
    has_media = bool(files or drawing_images)

    if drawing_images:
        for di in drawing_images:
            user_content.append({
                "type": "text",
                "text": f"\n--- DRAWING PAGE {di['page_number']} (doc_id: {di['doc_id']}) ---",
            })
            user_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": di["base64"]},
            })

    if files:
        for f in files:
            mt = f.get("media_type", "")
            if mt == "application/pdf":
                user_content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": f["base64"]}
                })
            elif mt.startswith("image/"):
                user_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mt, "data": f["base64"]}
                })

    if has_media:
        user_content.append({
            "type": "text",
            "text": message or "Analyze these documents in the context of the current project."
        })
    else:
        user_content = message

    messages = conversation_history + [{"role": "user", "content": user_content}]

    try:
        # Async call — doesn't block the event loop
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048 if drawing_images else settings.ANTHROPIC_MAX_TOKENS,
            system=build_system_prompt(project_data, document_context),
            messages=messages
        )
    except anthropic.AuthenticationError:
        logger.error("anthropic_auth_error", detail="Invalid API key")
        raise RuntimeError("AI service configuration error. Contact support.")
    except anthropic.RateLimitError:
        logger.warning("anthropic_rate_limit")
        raise RuntimeError("AI service is temporarily busy. Please try again in a moment.")
    except anthropic.APIError as e:
        # Sanitize — don't leak API details to the user
        logger.error("anthropic_api_error", status=getattr(e, 'status_code', None))
        raise RuntimeError("AI service encountered an error. Please try again.")
    except Exception as e:
        logger.error("anthropic_unknown_error", error=str(type(e).__name__))
        raise RuntimeError("An unexpected error occurred with the AI service.")

    text = "".join(block.text for block in response.content if block.type == "text")
    return text


def format_response(raw: str) -> str:
    """
    Convert Claude's markdown response to clean HTML.
    Handles: headers, bold, bullet lists, numbered lists,
    paragraphs, and preserves existing HTML (citations, risk boxes).
    """
    lines = raw.split('\n')
    html_lines = []
    in_ul = False
    in_ol = False

    for line in lines:
        stripped = line.strip()

        # Skip empty lines — they become paragraph breaks
        if not stripped:
            if in_ul:
                html_lines.append('</ul>')
                in_ul = False
            if in_ol:
                html_lines.append('</ol>')
                in_ol = False
            html_lines.append('<br>')
            continue

        # Headers: ## Title or ### Title
        header_match = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if header_match:
            if in_ul:
                html_lines.append('</ul>')
                in_ul = False
            if in_ol:
                html_lines.append('</ol>')
                in_ol = False
            level = len(header_match.group(1))
            text = _inline_format(header_match.group(2))
            tag = f'h{min(level + 1, 5)}'  # ## -> h3, ### -> h4
            html_lines.append(f'<{tag} style="margin:0.8em 0 0.3em;font-family:var(--heading);font-weight:500">{text}</{tag}>')
            continue

        # Bullet list: - item or * item or • item
        bullet_match = re.match(r'^[\-\*\u2022]\s+(.+)$', stripped)
        if bullet_match:
            if in_ol:
                html_lines.append('</ol>')
                in_ol = False
            if not in_ul:
                html_lines.append('<ul style="margin:0.3em 0;padding-left:1.2em">')
                in_ul = True
            html_lines.append(f'<li>{_inline_format(bullet_match.group(1))}</li>')
            continue

        # Numbered list: 1. item or 1) item
        num_match = re.match(r'^\d+[\.\)]\s+(.+)$', stripped)
        if num_match:
            if in_ul:
                html_lines.append('</ul>')
                in_ul = False
            if not in_ol:
                html_lines.append('<ol style="margin:0.3em 0;padding-left:1.2em">')
                in_ol = True
            html_lines.append(f'<li>{_inline_format(num_match.group(1))}</li>')
            continue

        # Regular paragraph — close any open lists
        if in_ul:
            html_lines.append('</ul>')
            in_ul = False
        if in_ol:
            html_lines.append('</ol>')
            in_ol = False

        # If line already contains HTML tags (citations, risk boxes), keep as-is
        if '<div ' in stripped or '<span ' in stripped:
            html_lines.append(_inline_format(stripped))
        else:
            html_lines.append(f'<p style="margin:0.3em 0">{_inline_format(stripped)}</p>')

    # Close any open lists
    if in_ul:
        html_lines.append('</ul>')
    if in_ol:
        html_lines.append('</ol>')

    return '\n'.join(html_lines)


def _inline_format(text: str) -> str:
    """Apply inline formatting: bold, italic, inline code."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`(.+?)`', r'<code style="background:var(--surface);padding:0.1em 0.3em;border-radius:3px;font-size:0.85em">\1</code>', text)
    return text


async def stream_claude(
    message: str,
    project_data: dict,
    conversation_history: list[dict],
    files: list[dict] | None = None,
    document_context: str = "",
    drawing_images: list[dict] | None = None,
):
    """
    Stream tokens from Claude as an async generator.
    Yields individual text chunks as they arrive.

    drawing_images: optional list of {page_number, base64, doc_id} for
    blueprint vision queries injected from the chat pipeline.
    """
    # Build content (same as ask_claude)
    user_content = []
    has_media = bool(files or drawing_images)

    if drawing_images:
        for di in drawing_images:
            user_content.append({
                "type": "text",
                "text": f"\n--- DRAWING PAGE {di['page_number']} (doc_id: {di['doc_id']}) ---",
            })
            user_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": di["base64"]},
            })

    if files:
        for f in files:
            mt = f.get("media_type", "")
            if mt == "application/pdf":
                user_content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": f["base64"]}
                })
            elif mt.startswith("image/"):
                user_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mt, "data": f["base64"]}
                })

    if has_media:
        user_content.append({
            "type": "text",
            "text": message or "Analyze these documents in the context of the current project."
        })
    else:
        user_content = message

    messages = conversation_history + [{"role": "user", "content": user_content}]

    try:
        async with client.messages.stream(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048 if drawing_images else settings.ANTHROPIC_MAX_TOKENS,
            system=build_system_prompt(project_data, document_context),
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    except anthropic.AuthenticationError:
        logger.error("stream_auth_error")
        raise RuntimeError("AI service configuration error.")
    except anthropic.RateLimitError:
        logger.warning("stream_rate_limit")
        raise RuntimeError("AI service is temporarily busy.")
    except anthropic.APIError as e:
        logger.error("stream_api_error", status=getattr(e, 'status_code', None))
        raise RuntimeError("AI service encountered an error.")
    except Exception as e:
        logger.error("stream_unknown_error", error=str(type(e).__name__))
        raise RuntimeError("An unexpected error occurred.")
