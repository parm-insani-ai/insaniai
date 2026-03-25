# ═══════════════════════════════════════════════
# insani backend — Production Dockerfile
#
# Build:  docker build -t insani-backend .
# Run:    docker run -p 8000:8000 --env-file .env insani-backend
# ═══════════════════════════════════════════════

FROM python:3.12-slim AS base

# Don't write .pyc files, don't buffer stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Dependencies layer (cached unless requirements change) ──
FROM base AS deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application layer ──
FROM deps AS app
COPY . .

# Create non-root user for security
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# Production server with 4 workers
# Adjust workers based on your CPU count: (2 * cores) + 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
