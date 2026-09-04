"""
FastAPI 依赖注入 — 数据库 Session / JWT 认证 / 权限校验
"""
import hashlib
import uuid
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User, UserRole

# HTTP Bearer Token 提取器（optional=True 允许匿名访问接口可选认证）
bearer_scheme = HTTPBearer(auto_error=False)
bearer_required = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _get_user_from_token(
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
) -> User | None:
    if not credentials:
        return None
    if settings.SSO_AUTH_REQUIRED:
        return await _get_user_from_sso(credentials.credentials, db)
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id: str = payload.get("sub")
        token_version = int(payload.get("ver", 0))
        if not user_id:
            return None
        parsed_user_id = uuid.UUID(user_id)
    except (JWTError, TypeError, ValueError):
        return None

    result = await db.execute(select(User).where(User.id == parsed_user_id))
    user = result.scalar_one_or_none()
    if not user or user.token_version != token_version:
        return None
    return user


async def _get_user_from_sso(token: str, db: AsyncSession) -> User | None:
    """Validate the bearer against the sole SSO user source, then load local ACL data."""
    userinfo_url = settings.SSO_USERINFO_URL.strip()
    if not userinfo_url.startswith("https://sso.ixicai.cn/"):
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.SSO_TIMEOUT_SECONDS) as client:
            response = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code >= 400:
            return None
        claims = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if not isinstance(claims, dict):
        return None
    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    if not subject or not email:
        return None
    result = await db.execute(select(User).where(User.sso_subject == subject))
    user = result.scalar_one_or_none()
    if user is None:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None and user.sso_subject not in {None, "", subject}:
            return None
    if user is None:
        suffix = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
        user = User(
            email=email,
            username=f"sso_{suffix}",
            sso_subject=subject,
            hashed_password=f"!sso-managed:{suffix}",
            role=UserRole.USER,
            is_active=True,
            is_verified=bool(claims.get("email_verified", True)),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        changed = False
        if not user.sso_subject:
            user.sso_subject = subject
            changed = True
        if user.email != email:
            duplicate = await db.execute(select(User.id).where(User.email == email, User.id != user.id))
            if duplicate.scalar_one_or_none() is not None:
                return None
            user.email = email
            changed = True
        verified = bool(claims.get("email_verified", True))
        if user.is_verified != verified:
            user.is_verified = verified
            changed = True
        if changed:
            await db.commit()
            await db.refresh(user)
    if not user.is_active:
        return None
    return user


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User | None:
    """可选认证 — 未登录时返回 None"""
    user = await _get_user_from_token(credentials, db)
    if not user or not user.is_active:
        return None
    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_required)],
    db: DbSession,
) -> User:
    """必须认证 — 未登录返回 401"""
    user = await _get_user_from_token(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    return user


async def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """仅管理员可访问"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
AdminUser = Annotated[User, Depends(require_admin)]
