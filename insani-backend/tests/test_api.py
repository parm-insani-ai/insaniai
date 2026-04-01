"""
Tests for insani backend — auth, projects, chat, documents.
Runs against an in-memory SQLite database.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, init_db, async_session
from app.models.db_models import Base

# Disable ALL rate limiting for tests
import os
os.environ["RATELIMIT_ENABLED"] = "false"
from slowapi import Limiter
Limiter.enabled = False
if hasattr(app.state, 'limiter'):
    app.state.limiter._disabled = True


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create fresh tables for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    """Async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(client):
    """Client with a logged-in user. Returns (client, tokens)."""
    # Signup
    res = await client.post("/v1/auth/signup", json={
        "email": "test@test.com",
        "password": "TestPass123",
        "name": "Test User",
        "org_name": "Test Org",
    })
    assert res.status_code == 201
    tokens = res.json()
    client.headers["Authorization"] = f"Bearer {tokens['access_token']}"
    return client, tokens


# ═══ AUTH TESTS ═══

@pytest.mark.asyncio
async def test_signup(client):
    res = await client.post("/v1/auth/signup", json={
        "email": "user@example.com",
        "password": "SecurePass123",
        "name": "New User",
        "org_name": "New Org",
    })
    assert res.status_code == 201
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_signup_weak_password(client):
    res = await client.post("/v1/auth/signup", json={
        "email": "user@example.com",
        "password": "short",
        "name": "User",
        "org_name": "Org",
    })
    assert res.status_code in (400, 422)


@pytest.mark.asyncio
async def test_signup_duplicate_email(client):
    await client.post("/v1/auth/signup", json={
        "email": "dupe@test.com", "password": "TestPass123",
        "name": "User1", "org_name": "Org1",
    })
    res = await client.post("/v1/auth/signup", json={
        "email": "dupe@test.com", "password": "TestPass456",
        "name": "User2", "org_name": "Org2",
    })
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_login_success(client):
    await client.post("/v1/auth/signup", json={
        "email": "login@test.com", "password": "TestPass123",
        "name": "User", "org_name": "Org",
    })
    res = await client.post("/v1/auth/login", json={
        "email": "login@test.com", "password": "TestPass123",
    })
    assert res.status_code == 200
    assert "access_token" in res.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/v1/auth/signup", json={
        "email": "wrong@test.com", "password": "TestPass123",
        "name": "User", "org_name": "Org",
    })
    res = await client.post("/v1/auth/login", json={
        "email": "wrong@test.com", "password": "WrongPass",
    })
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me_authenticated(auth_client):
    client, _ = auth_client
    res = await client.get("/v1/auth/me")
    assert res.status_code == 200
    assert res.json()["email"] == "test@test.com"


@pytest.mark.asyncio
async def test_me_unauthenticated(client):
    res = await client.get("/v1/auth/me")
    assert res.status_code == 403 or res.status_code == 401


# ═══ PROJECT TESTS ═══

@pytest.mark.asyncio
async def test_create_project(auth_client):
    client, _ = auth_client
    res = await client.post("/v1/projects/", json={
        "name": "Test Project", "type": "Commercial", "location": "Halifax",
    })
    assert res.status_code == 201
    assert res.json()["name"] == "Test Project"


@pytest.mark.asyncio
async def test_list_projects(auth_client):
    client, _ = auth_client
    await client.post("/v1/projects/", json={"name": "P1"})
    await client.post("/v1/projects/", json={"name": "P2"})
    res = await client.get("/v1/projects/")
    assert res.status_code == 200
    assert len(res.json()) == 2


@pytest.mark.asyncio
async def test_project_tenant_isolation(client):
    """Projects from one org shouldn't be visible to another."""
    # Create org 1
    r1 = await client.post("/v1/auth/signup", json={
        "email": "org1@test.com", "password": "TestPass123",
        "name": "User1", "org_name": "Org1",
    })
    assert r1.status_code == 201
    data1 = r1.json()
    token1 = data1.get("access_token")
    assert token1, f"No access_token in response: {data1}"

    # Create org 2
    r2 = await client.post("/v1/auth/signup", json={
        "email": "org2@test.com", "password": "TestPass123",
        "name": "User2", "org_name": "Org2",
    })
    assert r2.status_code == 201
    data2 = r2.json()
    token2 = data2.get("access_token")
    assert token2, f"No access_token in response: {data2}"

    # Create project in org 1
    await client.post("/v1/projects/", json={"name": "Secret Project"},
        headers={"Authorization": f"Bearer {token1}"})

    # Org 2 should not see it
    res = await client.get("/v1/projects/",
        headers={"Authorization": f"Bearer {token2}"})
    assert len(res.json()) == 0


# ═══ HEALTH CHECK ═══

@pytest.mark.asyncio
async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


# ═══ ADMIN ENDPOINT PROTECTION ═══

@pytest.mark.asyncio
async def test_admin_requires_auth(client):
    res = await client.get("/v1/admin/metrics")
    assert res.status_code == 403 or res.status_code == 401


@pytest.mark.asyncio
async def test_admin_with_auth(auth_client):
    client, _ = auth_client
    res = await client.get("/v1/admin/metrics")
    assert res.status_code == 200
