# insani — Backend API (Production)

> Python / FastAPI backend for the insani construction AI copilot.
> Handles auth, multi-tenancy, chat persistence, AI proxy with streaming,
> response caching, token management, and monitoring.

---

## Quick Start (Development)

```bash
pip install -r requirements.txt
cp .env.example .env          # Add your Anthropic key + JWT secret
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

## Quick Start (Production with Docker)

```bash
cp .env.example .env          # Fill in all values
docker compose up -d          # Starts PostgreSQL + backend
```

---

## Project Structure

```
insani-backend/
├── app/
│   ├── main.py                ← FastAPI entry: routers, middleware, lifecycle
│   ├── config.py              ← Typed settings from environment variables
│   ├── db.py                  ← SQLAlchemy async engine + session factory
│   │
│   ├── models/
│   │   ├── db_models.py       ← ORM tables: Org, User, Project, Chat, Cache, RefreshToken
│   │   ├── schemas_user.py    ← Pydantic: signup/login validation
│   │   ├── schemas_project.py ← Pydantic: project CRUD validation
│   │   └── schemas_chat.py    ← Pydantic: chat/AI request/response shapes
│   │
│   ├── routers/
│   │   ├── auth.py            ← Signup, login, token refresh, logout
│   │   ├── projects.py        ← Project CRUD with tenant isolation
│   │   ├── chat.py            ← Session list, load, delete with pagination
│   │   ├── ai.py              ← POST /v1/ai/ask — cached, token-managed
│   │   └── ai_stream.py       ← POST /v1/ai/stream — SSE real-time tokens
│   │
│   ├── services/
│   │   ├── auth_service.py    ← bcrypt, JWT access tokens, refresh token CRUD
│   │   ├── chat_service.py    ← Message/session persistence via SQLAlchemy
│   │   ├── ai_service.py      ← Async Claude client, streaming, prompt builder
│   │   ├── cache_service.py   ← Query-hash response cache with TTL
│   │   ├── token_service.py   ← Token counting, context budget, truncation
│   │   └── monitoring.py      ← Sentry init, in-memory metrics collector
│   │
│   └── middleware/
│       ├── auth.py            ← JWT validation: require_auth, require_auth_context
│       ├── errors.py          ← Global exception handler, structured error format
│       └── logging.py         ← Request logging with ID, duration, user
│
├── alembic/                   ← Database migration system
│   ├── env.py                 ← Connects Alembic to SQLAlchemy models
│   ├── script.py.mako         ← Migration template
│   └── versions/              ← Generated migration files
│
├── scripts/
│   └── backup.py              ← Automated DB backup with rotation + S3 upload
│
├── Dockerfile                 ← Multi-stage production build
├── docker-compose.yml         ← Backend + PostgreSQL local stack
├── requirements.txt
├── alembic.ini
└── .env.example
```

---

## Architecture

### Multi-Tenancy

Every record is scoped to an **Organization** (`org_id`):

```
Organization
  └── Users (each belongs to exactly one org)
  └── Projects (scoped to org)
       └── Chat Sessions (scoped to org + user)
            └── Messages
```

The JWT contains both `user_id` and `org_id`. The `require_auth_context` middleware
extracts both, and every database query filters by `org_id`. A user can never see
another organization's data, even by guessing IDs.

### Auth Flow

```
Signup → creates Organization + User
       → returns access_token (15min) + refresh_token (30 days)

Login → validates credentials
      → returns access_token + refresh_token

API Request → Authorization: Bearer <access_token>
            → middleware decodes JWT, extracts user_id + org_id

Token Expired → POST /v1/auth/refresh with refresh_token
              → returns new access_token

Logout → POST /v1/auth/logout
       → revokes refresh_token in DB
```

### AI Request Flow

```
POST /v1/ai/ask
  │
  ├── 1. Auth middleware → extract user_id, org_id
  ├── 2. Tenant check → project.org_id == ctx.org_id
  ├── 3. Cache check → hash(query) lookup for this project
  │     ├── HIT → return cached response (skip Claude)
  │     └── MISS → continue
  ├── 4. Token budget → estimate tokens for prompt + history
  │     └── Over budget → truncate history or project data
  ├── 5. Claude API call (async, non-blocking)
  ├── 6. Save user message + AI response to DB
  ├── 7. Cache the response (TTL: 1 hour)
  └── 8. Return response + session_id
```

### Streaming

`POST /v1/ai/stream` uses Server-Sent Events:

```
event: session
data: {"session_id": 42, "title": "RFI delays"}

event: token
data: {"text": "There "}

event: token
data: {"text": "are "}

event: token
data: {"text": "2 open RFIs..."}

event: done
data: {"full_response": "<p>There are <strong>2 open RFIs</strong>..."}
```

Frontend reads the stream and appends tokens to the chat bubble in real-time.

### Response Caching

- Keyed by `project_id` + SHA-256 of normalized query
- TTL: 1 hour (configurable)
- Automatically invalidated when project `data_json` is updated via PATCH
- File-attached queries bypass cache (they're always unique)
- Hit count tracked per entry for analytics

### Token Budget

Before every Claude call, the system estimates token usage:
- System prompt (project data): tracked, truncated if > 50K tokens
- Conversation history: tracked, oldest messages dropped if needed
- New message: tracked
- Response reserve: 1,500 tokens held for the response

If the total exceeds 200K (Claude's context window), the system automatically
truncates — dropping email data first, then drawings, budget, schedule,
and only RFIs/submittals as a last resort.

---

## API Reference

### Auth
| Method | Path | Rate Limit | Description |
|--------|------|-----------|-------------|
| POST | `/v1/auth/signup` | 3/min | Create org + account |
| POST | `/v1/auth/login` | 5/min | Get access + refresh tokens |
| POST | `/v1/auth/refresh` | 10/min | Exchange refresh for new access token |
| POST | `/v1/auth/logout` | — | Revoke refresh token |
| GET | `/v1/auth/me` | — | Get current user |

### Projects
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/projects` | List org's projects |
| GET | `/v1/projects/{id}` | Get project with data |
| POST | `/v1/projects` | Create project |
| PATCH | `/v1/projects/{id}` | Update project (invalidates cache) |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/chat/sessions?page=1&per_page=20` | List sessions (paginated) |
| GET | `/v1/chat/sessions/{id}` | Get session with messages |
| DELETE | `/v1/chat/sessions/{id}` | Delete session |

### AI
| Method | Path | Rate Limit | Description |
|--------|------|-----------|-------------|
| POST | `/v1/ai/ask` | 20/min | Send message, get response (cached) |
| POST | `/v1/ai/stream` | — | Stream response via SSE |

### Admin
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | DB connectivity + app status |
| GET | `/v1/admin/metrics` | Token usage, cache rates, errors |

---

## Database Migrations

```bash
# After changing models in db_models.py:
alembic revision --autogenerate -m "description of change"

# Apply migrations:
alembic upgrade head

# See current version:
alembic current

# Rollback one migration:
alembic downgrade -1
```

---

## Backups

```bash
# Manual backup:
python scripts/backup.py

# Cron (every 6 hours):
0 */6 * * * cd /app && python scripts/backup.py >> /var/log/backup.log 2>&1
```

Supports PostgreSQL (pg_dump) and SQLite (file copy). Gzip compressed.
Automatic rotation (keeps last 48). Optional S3 upload.

---

## Monitoring

- **Sentry**: Set `SENTRY_DSN` in `.env`. Captures errors + 10% performance traces.
- **Metrics**: `GET /v1/admin/metrics` returns token usage, cache hit rate, response times, error counts.
- **Structured logs**: JSON in production, colored console in dev. Every request logged with ID, duration, user.
