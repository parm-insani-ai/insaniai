"""
Projects Router — CRUD with tenant isolation.

All queries filter by org_id from the JWT so users
can never access another organization's projects.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.db_models import Project, ProjectMember
from app.models.schemas_project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListItem
from app.services import cache_service
from app.middleware.auth import require_auth_context, AuthContext

router = APIRouter(prefix="/v1/projects", tags=["Projects"])


@router.get("/", response_model=list[ProjectListItem])
async def list_projects(ctx: AuthContext = Depends(require_auth_context), db: AsyncSession = Depends(get_db)):
    """List all projects in the user's organization."""
    result = await db.execute(
        select(Project)
        .where(Project.org_id == ctx.org_id)  # Tenant boundary
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    return [
        ProjectListItem(
            id=p.id, name=p.name, type=p.type, location=p.location,
            created_at=str(p.created_at) if p.created_at else None,
        )
        for p in projects
    ]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get a project with full data. Enforces tenant boundary."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.org_id == ctx.org_id,  # Tenant boundary
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectResponse(
        id=project.id, name=project.name, type=project.type,
        location=project.location, data_json=project.data_json,
        owner_id=project.owner_id,
        created_at=str(project.created_at) if project.created_at else None,
    )


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: ProjectCreate,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Create a project. Scoped to the user's organization."""
    project = Project(
        org_id=ctx.org_id,
        name=body.name, type=body.type, location=body.location,
        data_json=body.data_json, owner_id=ctx.user_id,
    )
    db.add(project)
    await db.flush()

    # Add creator as admin member
    member = ProjectMember(project_id=project.id, user_id=ctx.user_id, role="admin")
    db.add(member)

    return ProjectResponse(
        id=project.id, name=project.name, type=project.type,
        location=project.location, data_json=project.data_json,
        owner_id=ctx.user_id,
        created_at=str(project.created_at) if project.created_at else None,
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    ctx: AuthContext = Depends(require_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Update project fields. Invalidates response cache when data changes."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.org_id == ctx.org_id,
            Project.owner_id == ctx.user_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    data_changed = False
    if body.name is not None:
        project.name = body.name
    if body.type is not None:
        project.type = body.type
    if body.location is not None:
        project.location = body.location
    if body.data_json is not None:
        project.data_json = body.data_json
        data_changed = True

    # Invalidate cache when project data changes
    # (cached AI responses are based on old data)
    if data_changed:
        await cache_service.invalidate_project_cache(db, project_id)

    return ProjectResponse(
        id=project.id, name=project.name, type=project.type,
        location=project.location, data_json=project.data_json,
        owner_id=project.owner_id,
        created_at=str(project.created_at) if project.created_at else None,
    )
