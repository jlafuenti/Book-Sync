"""
User management router: admin CRUD operations and audit log viewing.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User, VALID_ROLES
from models.audit_log import AuditLog
from schemas import UserResponse, UserCreateAdmin, UserUpdateAdmin, UserPasswordReset
from routers.auth import get_admin_user, hash_password, log_audit, get_client_ip

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/", response_model=list[UserResponse])
async def list_users(
    filter: Optional[str] = Query(None, pattern="^(pending|active|inactive|all)$"),
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users with optional status filter."""
    query = select(User).order_by(User.created_at.desc())

    if filter == "pending":
        query = query.where(User.is_active == False)
    elif filter == "active":
        query = query.where(User.is_active == True)
    elif filter == "inactive":
        query = query.where(User.is_active == False)
    # "all" or None: no filter

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/audit-log")
async def get_audit_log(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    action: Optional[str] = None,
    user_id: Optional[int] = None,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """View audit log entries (admin only)."""
    query = select(AuditLog).order_by(desc(AuditLog.created_at))

    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)

    # Get total count
    count_query = select(func.count(AuditLog.id))
    if action:
        count_query = count_query.where(AuditLog.action == action)
    if user_id:
        count_query = count_query.where(AuditLog.user_id == user_id)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginate
    offset = (page - 1) * limit
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    # Build response with user info
    # Collect unique user IDs to batch-load usernames
    user_ids = set()
    for entry in entries:
        if entry.user_id:
            user_ids.add(entry.user_id)
        if entry.target_user_id:
            user_ids.add(entry.target_user_id)

    username_map = {}
    if user_ids:
        users_result = await db.execute(
            select(User.id, User.username).where(User.id.in_(user_ids))
        )
        for uid, uname in users_result:
            username_map[uid] = uname

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "entries": [
            {
                "id": e.id,
                "user_id": e.user_id,
                "username": username_map.get(e.user_id),
                "action": e.action,
                "target_user_id": e.target_user_id,
                "target_username": username_map.get(e.target_user_id),
                "details": e.details,
                "ip_address": e.ip_address,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
    }


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreateAdmin,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin creates a new user with a role and default password."""
    if data.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")

    if data.role == "superadmin":
        raise HTTPException(status_code=403, detail="Cannot create superadmin accounts")

    # Check uniqueness
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        role=data.role,
        is_admin=data.role in ("admin", "superadmin"),
        is_active=True,
        must_reset_password=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    await log_audit(
        db, "user_created", user_id=admin.id, target_user_id=user.id,
        details=f"Created user '{user.username}' with role '{user.role}'",
        ip_address=get_client_ip(request),
    )

    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdateAdmin,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's role or active status."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "superadmin" and admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="Only superadmin can modify superadmin accounts")

    changes = []

    if data.role is not None:
        if data.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")
        if data.role == "superadmin" and admin.role != "superadmin":
            raise HTTPException(status_code=403, detail="Only superadmin can assign superadmin role")
        old_role = user.role
        user.role = data.role
        user.is_admin = data.role in ("admin", "superadmin")
        changes.append(f"role: {old_role} -> {data.role}")

    if data.is_active is not None:
        user.is_active = data.is_active
        changes.append(f"is_active: {data.is_active}")

    await db.flush()
    await db.refresh(user)

    if changes:
        await log_audit(
            db, "user_updated", user_id=admin.id, target_user_id=user.id,
            details="; ".join(changes),
            ip_address=get_client_ip(request),
        )

    return user


@router.post("/{user_id}/approve", response_model=UserResponse)
async def approve_user(
    user_id: int,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending user account."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_active:
        raise HTTPException(status_code=400, detail="User is already active")

    user.is_active = True
    await db.flush()
    await db.refresh(user)

    await log_audit(
        db, "user_approved", user_id=admin.id, target_user_id=user.id,
        details=f"Approved user '{user.username}'",
        ip_address=get_client_ip(request),
    )

    return user


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    data: UserPasswordReset,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin resets a user's password. User will be forced to change it on next login."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "superadmin" and admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="Only superadmin can reset superadmin password")

    user.hashed_password = hash_password(data.new_password)
    user.must_reset_password = True
    user.token_version += 1
    await db.flush()

    await log_audit(
        db, "password_reset", user_id=admin.id, target_user_id=user.id,
        details=f"Admin reset password for '{user.username}'",
        ip_address=get_client_ip(request),
    )

    return {"message": f"Password reset for '{user.username}'. They will be required to change it on next login."}


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user account. Cannot delete superadmin."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="Cannot delete superadmin account")

    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    username = user.username
    await db.delete(user)
    await db.flush()

    await log_audit(
        db, "user_deleted", user_id=admin.id, target_user_id=user_id,
        details=f"Deleted user '{username}'",
        ip_address=get_client_ip(request),
    )

    return {"message": f"User '{username}' deleted"}
