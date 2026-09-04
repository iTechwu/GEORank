"""
认证 API — 注册 / 登录 / 获取当前用户
"""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import re
import secrets
import uuid
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from jose import JWTError, jwt
import bcrypt
from sqlalchemy import or_, select
from starlette.responses import RedirectResponse

from app.core.config import settings
from app.core.deps import DbSession, CurrentUser
from app.models.user import User, UserRole
from app.schemas.user import (
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
    UserProfileUpdateRequest,
)

router = APIRouter()
SSO_STATE_COOKIE = "georank_sso_state"


# ---------- 工具函数 ----------

def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _normalize_phone(phone: str | None) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    if not re.fullmatch(r"1\d{10}", digits):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请输入有效的手机号",
        )
    return digits


async def _username_exists(db: DbSession, username: str) -> bool:
    result = await db.execute(select(User.id).where(User.username == username))
    return result.scalar_one_or_none() is not None


async def _build_phone_identity(db: DbSession, phone: str) -> tuple[str, str]:
    base_username = f"u_{phone}"
    username = base_username
    suffix = 1
    while await _username_exists(db, username):
        username = f"{base_username}_{suffix}"
        suffix += 1
    email = f"phone_{phone}@phone.local"
    return username, email


def _create_access_token(
    user_id: str,
    *,
    token_version: int = 0,
    persistent: bool = False,
) -> str:
    expire = datetime.now(timezone.utc) + (
        timedelta(days=settings.JWT_PERSIST_DAYS)
        if persistent
        else timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    payload = {"sub": user_id, "ver": int(token_version), "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _safe_return_to(value: str | None) -> str:
    candidate = str(value or "/").strip()
    return candidate if candidate.startswith("/") and not candidate.startswith("//") else "/"


def _sso_completion_path(locale: str) -> str:
    return "/login"


# ---------- 路由 ----------

@router.get("/sso/start")
async def sso_start(return_to: str = "/", locale: str = "zh-CN"):
    """Start the SSO authorization-code flow with PKCE and a signed state cookie."""
    if not settings.SSO_AUTH_REQUIRED or not settings.SSO_CLIENT_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO 登录尚未配置")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    expires = datetime.now(timezone.utc) + timedelta(seconds=max(60, settings.SSO_STATE_TTL_SECONDS))
    cookie_payload = jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "return_to": _safe_return_to(return_to),
            "locale": locale if locale in {"zh-CN", "en-US"} else "zh-CN",
            "exp": expires,
        },
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    query = urlencode({
        "response_type": "code",
        "client_id": settings.SSO_CLIENT_ID,
        "redirect_uri": settings.SSO_REDIRECT_URI,
        "scope": "openid profile email tenant",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    response = RedirectResponse(f"{settings.SSO_ISSUER.rstrip('/')}/oauth/authorize?{query}")
    response.set_cookie(
        SSO_STATE_COOKIE,
        cookie_payload,
        max_age=max(60, settings.SSO_STATE_TTL_SECONDS),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="lax",
        # The public callback is /auth/oidc/callback and is proxied internally.
        path="/",
    )
    return response


@router.get("/sso/callback")
async def sso_callback(request: Request, code: str = "", state: str = ""):
    """Exchange the SSO code and hand the verified access token to the web session."""
    encoded_state = request.cookies.get(SSO_STATE_COOKIE, "")
    try:
        stored = jwt.decode(encoded_state, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO 登录状态无效或已过期")
    if not code or not state or not secrets.compare_digest(str(stored.get("state") or ""), state):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO 登录状态校验失败")

    try:
        async with httpx.AsyncClient(timeout=settings.SSO_TIMEOUT_SECONDS) as client:
            token_response = await client.post(
                f"{settings.SSO_ISSUER.rstrip('/')}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.SSO_REDIRECT_URI,
                    "client_id": settings.SSO_CLIENT_ID,
                    "code_verifier": str(stored.get("verifier") or ""),
                },
                auth=(settings.SSO_CLIENT_ID, settings.SSO_CLIENT_SECRET),
            )
            token_payload = token_response.json() if token_response.status_code < 400 else {}
            access_token = str(token_payload.get("access_token") or "").strip()
            if not access_token:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO 拒绝了登录回调")
            userinfo = await client.get(
                settings.SSO_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            claims = userinfo.json() if userinfo.status_code < 400 else {}
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO 当前不可用") from error
    if not isinstance(claims, dict) or not claims.get("sub") or not claims.get("email"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO 身份响应无效")

    return_to = _safe_return_to(str(stored.get("return_to") or "/"))
    locale = str(stored.get("locale") or "zh-CN")
    completion = _sso_completion_path(locale)
    destination = (
        f"{settings.PUBLIC_BASE_URL.rstrip('/')}{completion}?{urlencode({'return': return_to})}"
        f"#access_token={quote(access_token, safe='')}"
    )
    response = RedirectResponse(destination)
    response.delete_cookie(SSO_STATE_COOKIE, path="/")
    return response

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, db: DbSession):
    """用户注册 — 创建账号并返回 JWT"""
    if settings.SSO_AUTH_REQUIRED:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="用户注册统一由 sso.ixicai.cn 提供")
    phone = _normalize_phone(data.phone) if data.phone else None
    username = data.username
    email = str(data.email) if data.email else None

    if phone:
        result = await db.execute(select(User).where(User.phone == phone))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="手机号已被注册")
        if not username or not email:
            username, email = await _build_phone_identity(db, phone)

    if not username or not email:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="注册信息不完整")

    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已被注册")

    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用")

    user = User(
        email=email,
        username=username,
        phone=phone,
        hashed_password=_hash_password(data.password),
        role=UserRole.USER,
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = _create_access_token(
        str(user.id),
        token_version=user.token_version,
        persistent=data.remember_me,
    )
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, data: LoginRequest, db: DbSession):
    """用户登录 — 支持用户名或邮箱登录（全局限速 200次/分钟/IP 兜底）"""
    if settings.SSO_AUTH_REQUIRED:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="用户登录统一由 sso.ixicai.cn 提供")
    identifier = data.phone or data.account or data.username or ""
    filters = []
    if data.phone:
        normalized_phone = _normalize_phone(data.phone)
        filters.append(User.phone == normalized_phone)
    else:
        filters.extend([User.username == identifier, User.email == identifier])

    result = await db.execute(select(User).where(or_(*filters)))
    user = result.scalar_one_or_none()

    if not user or not _verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码不正确",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")

    token = _create_access_token(
        str(user.id),
        token_version=user.token_version,
        persistent=data.remember_me,
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: CurrentUser):
    """获取当前登录用户信息"""
    return UserOut(
        id=str(current_user.id),
        email=current_user.email,
        username=current_user.username,
        phone=current_user.phone,
        role=current_user.role.value if hasattr(current_user.role, "value") else current_user.role,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
    )


@router.put("/me", response_model=UserOut)
async def update_me(data: UserProfileUpdateRequest, current_user: CurrentUser, db: DbSession):
    """修改当前登录用户资料"""
    if settings.SSO_AUTH_REQUIRED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户资料统一由 sso.ixicai.cn 管理")
    updates = data.model_dump(exclude_unset=True)

    if "username" in updates and updates["username"] is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="用户名不能为空")
    if "email" in updates and updates["email"] is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="邮箱不能为空")

    next_username = updates["username"].strip() if "username" in updates and updates["username"] else None
    if "username" in updates and not next_username:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="用户名不能为空")
    if next_username and next_username != current_user.username:
        result = await db.execute(
            select(User.id).where(User.username == next_username, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用")
        current_user.username = next_username

    next_email = str(updates["email"]).strip() if "email" in updates and updates["email"] else None
    if "email" in updates and not next_email:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="邮箱不能为空")
    if next_email and next_email != current_user.email:
        result = await db.execute(
            select(User.id).where(User.email == next_email, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已被注册")
        current_user.email = next_email

    if "phone" in updates:
        raw_phone = updates["phone"]
        next_phone = _normalize_phone(raw_phone) if raw_phone and str(raw_phone).strip() else None
        if next_phone != current_user.phone:
            if next_phone:
                result = await db.execute(
                    select(User.id).where(User.phone == next_phone, User.id != current_user.id)
                )
                if result.scalar_one_or_none():
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="手机号已被注册")
            current_user.phone = next_phone

    await db.commit()
    await db.refresh(current_user)
    return UserOut(
        id=str(current_user.id),
        email=current_user.email,
        username=current_user.username,
        phone=current_user.phone,
        role=current_user.role.value if hasattr(current_user.role, "value") else current_user.role,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        created_at=current_user.created_at.isoformat() if current_user.created_at else None,
    )


@router.put("/password")
async def change_password(data: PasswordChangeRequest, current_user: CurrentUser, db: DbSession):
    """修改当前登录用户密码"""
    if settings.SSO_AUTH_REQUIRED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户密码统一由 sso.ixicai.cn 管理")
    locked_user = await db.scalar(
        select(User)
        .where(User.id == current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not locked_user or not _verify_password(data.current_password, locked_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码不正确")
    if _verify_password(data.new_password, locked_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码不能与当前密码相同")

    locked_user.hashed_password = _hash_password(data.new_password)
    locked_user.token_version += 1
    await db.commit()
    return {"message": "密码已更新"}
