"""
Monitoring Service — Error tracking, usage metrics, and health probes.

Integrates with:
- Sentry (error tracking + performance monitoring)
- Internal metrics (token usage, cache hit rate, response times)

Setup:
    Set SENTRY_DSN in .env to enable Sentry.
    Metrics are exposed at GET /v1/admin/metrics (admin-only).
"""

import os
import time
from collections import defaultdict
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger()

# ── Sentry integration ──

SENTRY_DSN = os.getenv("SENTRY_DSN", "")


def init_sentry():
    """Initialize Sentry error tracking. Call once at app startup."""
    if not SENTRY_DSN:
        logger.info("sentry_disabled", reason="No SENTRY_DSN set")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=0.1,  # 10% of requests get performance monitoring
            profiles_sample_rate=0.05,
            environment=os.getenv("ENV", "development"),
            release=os.getenv("APP_VERSION", "1.0.0"),
        )
        logger.info("sentry_initialized")
    except ImportError:
        logger.warning("sentry_not_installed", hint="pip install sentry-sdk")
    except Exception as e:
        logger.error("sentry_init_failed", error=str(e))


# ── Internal metrics (in-memory, reset on restart) ──
# For production, replace with Prometheus, Datadog, or similar.

class Metrics:
    """Simple in-memory metrics collector."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.started_at = datetime.now(timezone.utc)
        self.requests_total = 0
        self.ai_calls_total = 0
        self.ai_tokens_total = 0
        self.ai_errors_total = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.response_times = []  # Last 1000 response times in ms
        self.errors_by_code = defaultdict(int)

    def record_request(self):
        self.requests_total += 1

    def record_ai_call(self, tokens: int = 0, error: bool = False):
        self.ai_calls_total += 1
        self.ai_tokens_total += tokens
        if error:
            self.ai_errors_total += 1

    def record_cache(self, hit: bool):
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def record_response_time(self, ms: float):
        self.response_times.append(ms)
        if len(self.response_times) > 1000:
            self.response_times = self.response_times[-1000:]

    def record_error(self, status_code: int):
        self.errors_by_code[status_code] += 1

    def to_dict(self) -> dict:
        uptime_seconds = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        avg_response = (
            sum(self.response_times) / len(self.response_times)
            if self.response_times else 0
        )
        cache_rate = (
            self.cache_hits / (self.cache_hits + self.cache_misses) * 100
            if (self.cache_hits + self.cache_misses) > 0 else 0
        )

        return {
            "uptime_seconds": round(uptime_seconds),
            "uptime_human": f"{uptime_seconds / 3600:.1f} hours",
            "requests_total": self.requests_total,
            "ai": {
                "calls_total": self.ai_calls_total,
                "tokens_total": self.ai_tokens_total,
                "errors_total": self.ai_errors_total,
                "estimated_cost_usd": round(self.ai_tokens_total * 0.000003, 4),  # ~$3/M tokens
            },
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "hit_rate_pct": round(cache_rate, 1),
            },
            "response_times": {
                "avg_ms": round(avg_response, 1),
                "p95_ms": round(sorted(self.response_times)[int(len(self.response_times) * 0.95)] if self.response_times else 0, 1),
                "samples": len(self.response_times),
            },
            "errors_by_code": dict(self.errors_by_code),
        }


# Global metrics instance
metrics = Metrics()
