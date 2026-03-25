"""
Token Service — Token counting and context window management.

Estimates token counts for messages and project data to:
1. Prevent exceeding Claude's context window
2. Track API costs per user/project
3. Decide when to truncate or summarize project data

Uses a simple word-based estimator (1 token ≈ 0.75 words for English).
For exact counts, use the Anthropic tokenizer — but the estimate is
fast enough for real-time decisions.
"""

import json
import structlog

logger = structlog.get_logger()

# Claude Sonnet context window
MAX_CONTEXT_TOKENS = 200_000
# Reserve tokens for the response
RESPONSE_RESERVE = 1_500
# Max tokens we'll use for the system prompt (project data)
MAX_SYSTEM_TOKENS = 50_000


def estimate_tokens(text: str) -> int:
    """
    Estimate token count from text.
    Rule of thumb: 1 token ≈ 4 characters for English.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_message_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a conversation history."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # Multimodal content — estimate text parts, add flat cost for files
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total += estimate_tokens(block.get("text", ""))
                    elif block.get("type") in ("image", "document"):
                        total += 1000  # Rough estimate for image/doc tokens
        total += 4  # Per-message overhead
    return total


def estimate_project_data_tokens(project_data: dict) -> int:
    """Estimate tokens for the project data JSON in the system prompt."""
    return estimate_tokens(json.dumps(project_data))


def check_context_budget(
    project_data: dict,
    conversation_history: list[dict],
    new_message: str
) -> dict:
    """
    Check if the full request fits within the context window.
    Returns a budget report with recommendations.
    
    Returns:
        {
            "fits": bool,
            "system_tokens": int,
            "history_tokens": int,
            "message_tokens": int,
            "total_tokens": int,
            "remaining": int,
            "recommendation": str | None
        }
    """
    system_tokens = estimate_project_data_tokens(project_data) + 500  # Prompt template overhead
    history_tokens = estimate_message_tokens(conversation_history)
    message_tokens = estimate_tokens(new_message)
    total = system_tokens + history_tokens + message_tokens + RESPONSE_RESERVE

    remaining = MAX_CONTEXT_TOKENS - total
    fits = remaining > 0

    recommendation = None
    if not fits:
        if system_tokens > MAX_SYSTEM_TOKENS:
            recommendation = "truncate_project_data"
        elif history_tokens > MAX_CONTEXT_TOKENS * 0.6:
            recommendation = "truncate_history"
        else:
            recommendation = "reduce_all"

    return {
        "fits": fits,
        "system_tokens": system_tokens,
        "history_tokens": history_tokens,
        "message_tokens": message_tokens,
        "total_tokens": total,
        "remaining": max(0, remaining),
        "recommendation": recommendation,
    }


def truncate_history(messages: list[dict], max_tokens: int) -> list[dict]:
    """
    Trim conversation history to fit within max_tokens.
    Keeps the most recent messages. Always preserves at least
    the last 2 messages (one user + one assistant exchange).
    """
    if not messages:
        return messages

    # Always keep at least 2 messages
    min_keep = min(2, len(messages))
    result = list(messages)

    while len(result) > min_keep:
        current_tokens = estimate_message_tokens(result)
        if current_tokens <= max_tokens:
            break
        result.pop(0)  # Remove oldest message

    return result


def truncate_project_data(project_data: dict, max_tokens: int) -> dict:
    """
    Reduce project data size to fit within max_tokens.
    Prioritizes: RFIs > submittals > schedule > budget > emails.
    Drops lower-priority sections first.
    """
    data = dict(project_data)

    # Try full data first
    if estimate_project_data_tokens(data) <= max_tokens:
        return data

    # Drop in order of decreasing expendability
    drop_order = ["emails", "drawings", "budget", "schedule", "submittals", "rfis"]
    for key in drop_order:
        if key in data:
            del data[key]
            logger.info("truncated_project_data", dropped=key)
            if estimate_project_data_tokens(data) <= max_tokens:
                return data

    return data
