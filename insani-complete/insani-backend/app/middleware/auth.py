"""
Auth Middleware — JWT validation with tenant isolation.

Provides two dependencies:
- require_auth: returns user_id (for user-scoped queries)
- require_auth_context: returns {user_id, org_id} (for tenant-scoped queries)

The org_id is embedded in the JWT at login time, so tenant filtering
doesn't require an extra DB lookup on every request.
"""

import jwt as pyjwt
from dataclasses import dataclass
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.services.auth_service import decode_token

security = HTTPBearer()


@dataclass
class AuthContext:
    """Authenticated request context with user and org info."""
    user_id: int
    org_id: int


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    """Validate JWT. Returns user_id. Use for simple auth checks."""
    try:
        payload = decode_token(credentials.credentials)
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return user_id
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired — please refresh")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def require_auth_context(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> AuthContext:
    """
    Validate JWT. Returns AuthContext with user_id AND org_id.
    Use for any query that needs tenant isolation.
    
    Usage:
        @router.get("/projects")
        async def list_projects(ctx: AuthContext = Depends(require_auth_context)):
            # Query WHERE org_id = ctx.org_id
    """
    try:
        payload = decode_token(credentials.credentials)
        user_id = payload.get("user_id")
        org_id = payload.get("org_id")
        if not user_id or not org_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return AuthContext(user_id=user_id, org_id=org_id)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired — please refresh")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
