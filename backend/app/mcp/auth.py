"""GeoFlow-compatible authentication boundary for the GEOrank MCP endpoint."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Awaitable, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request


READ_ABILITIES = (
    "georank:companies:read",
    "georank:diagnostics:read",
    "georank:solutions:read",
    "georank:experts:read",
    "georank:content:read",
    "georank:settings:read",
)


class McpUnauthorized(Exception):
    """The request did not provide an accepted MCP credential."""


@dataclass(frozen=True)
class McpAuthContext:
    credential_type: str
    scope: str
    token_hash: str
    tenant_id: str | None
    subject: str | None
    abilities: tuple[str, ...]

    def allows(self, required: str) -> bool:
        return "*" in self.abilities or required in self.abilities


@dataclass(frozen=True)
class McpAuthConfig:
    enabled: bool
    allow_system_token: bool
    allow_cross_tenant: bool
    write_token: str
    read_token: str
    default_tenant: str
    sso_issuer: str
    sso_client_id: str
    sso_userinfo_url: str
    sso_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "McpAuthConfig":
        return cls(
            enabled=_env_bool("GEORANK_MCP_ENABLED", True),
            allow_system_token=_env_bool("GEORANK_MCP_ALLOW_SYSTEM_TOKEN", True),
            allow_cross_tenant=_env_bool("GEORANK_MCP_ALLOW_CROSS_TENANT", False),
            write_token=os.environ.get("GEORANK_MCP_TOKEN", "").strip(),
            read_token=os.environ.get("GEORANK_MCP_READ_TOKEN", "").strip(),
            default_tenant=os.environ.get("GEORANK_MCP_DEFAULT_TENANT", "").strip(),
            sso_issuer=os.environ.get("SSO_ISSUER", "").strip(),
            sso_client_id=os.environ.get("SSO_CLIENT_ID", "").strip(),
            sso_userinfo_url=_userinfo_url(),
            sso_timeout_seconds=max(
                1.0, float(os.environ.get("SSO_MCP_USERINFO_TIMEOUT_SECONDS", "3"))
            ),
        )


UserInfoFetcher = Callable[[str, str, float], Awaitable[dict]]
_AUTH_CONTEXT: ContextVar[McpAuthContext | None] = ContextVar(
    "georank_mcp_auth", default=None
)


def current_mcp_auth() -> McpAuthContext:
    context = _AUTH_CONTEXT.get()
    if context is None:
        raise McpUnauthorized()
    return context


def require_mcp_ability(required: str):
    """Reject an MCP tool call unless the current credential grants its ability."""

    def decorate(function):
        @wraps(function)
        async def guarded(*args, **kwargs):
            if not current_mcp_auth().allows(required):
                raise PermissionError(f"MCP ability required: {required}")
            return await function(*args, **kwargs)

        return guarded

    return decorate


async def authenticate_bearer(
    authorization: str,
    config: McpAuthConfig,
    *,
    fetch_userinfo: UserInfoFetcher | None = None,
) -> McpAuthContext:
    token = _bearer_token(authorization)
    if not token:
        raise McpUnauthorized()

    if config.allow_system_token:
        static = _static_context(token, config)
        if static is not None:
            return static

    if not _is_sso_candidate(token, config):
        raise McpUnauthorized()

    try:
        claims = await (fetch_userinfo or _fetch_userinfo)(
            config.sso_userinfo_url, token, config.sso_timeout_seconds
        )
    except Exception as exc:
        raise McpUnauthorized() from exc

    subject = str(claims.get("sub") or "").strip()
    tenant_id = str(claims.get("selected_team_id") or "").strip()
    if not subject or not tenant_id:
        raise McpUnauthorized()

    return McpAuthContext(
        credential_type="sso",
        scope="write",
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        tenant_id=tenant_id,
        subject=subject,
        abilities=_scopes_from_claims(claims),
    )


class McpAuthMiddleware:
    def __init__(
        self,
        app,
        config: McpAuthConfig | None = None,
        *,
        fetch_userinfo: UserInfoFetcher | None = None,
    ) -> None:
        self.app = app
        self.config = config or McpAuthConfig.from_env()
        self.fetch_userinfo = fetch_userinfo

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if not self.config.enabled:
            await _send_json(send, 404, {"detail": "Not Found"})
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        try:
            context = await authenticate_bearer(
                headers.get("authorization", ""),
                self.config,
                fetch_userinfo=self.fetch_userinfo,
            )
        except McpUnauthorized:
            await _send_json(
                send,
                401,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32001, "message": "Unauthorized"},
                    "id": None,
                },
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return

        reset_token = _AUTH_CONTEXT.set(context)
        try:
            await self.app(scope, receive, send)
        finally:
            _AUTH_CONTEXT.reset(reset_token)


def _static_context(token: str, config: McpAuthConfig) -> McpAuthContext | None:
    scope = None
    abilities: tuple[str, ...] = ()
    if config.write_token and secrets.compare_digest(config.write_token, token):
        scope = "write"
        abilities = ("*",)
    elif config.read_token and secrets.compare_digest(config.read_token, token):
        scope = "read"
        abilities = READ_ABILITIES
    if scope is None:
        return None
    if not config.default_tenant and not config.allow_cross_tenant:
        raise McpUnauthorized()
    return McpAuthContext(
        credential_type="system",
        scope=scope,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        tenant_id=config.default_tenant or None,
        subject=None,
        abilities=abilities,
    )


def _is_sso_candidate(token: str, config: McpAuthConfig) -> bool:
    if len(token) > 8192 or not config.sso_issuer or not config.sso_client_id:
        return False
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        return False
    try:
        payload = json.loads(_base64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    issuer = str(payload.get("iss") or "").rstrip("/")
    expected_issuer = config.sso_issuer.rstrip("/")
    audience = payload.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    return secrets.compare_digest(issuer, expected_issuer) and config.sso_client_id in audiences


def _scopes_from_claims(claims: dict) -> tuple[str, ...]:
    roles = claims.get("roles", claims.get("role", []))
    if isinstance(roles, str):
        roles = roles.split()
    normalized_roles = {str(role).strip().lower() for role in roles or []}
    if normalized_roles.intersection({"super_admin", "superadmin"}) or claims.get(
        "isAdmin"
    ) is True:
        return ("*",)

    raw = claims.get("scopes", claims.get("scope", claims.get("permissions", [])))
    if isinstance(raw, str):
        raw = raw.split()
    return tuple(dict.fromkeys(str(item).strip() for item in raw or [] if str(item).strip()))


async def _fetch_userinfo(url: str, token: str, timeout: float) -> dict:
    def fetch() -> dict:
        request = urllib_request.Request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise McpUnauthorized()
                payload = json.loads(response.read())
        except (urllib_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise McpUnauthorized() from exc
        if not isinstance(payload, dict):
            raise McpUnauthorized()
        return payload

    return await asyncio.to_thread(fetch)


async def _send_json(send, status: int, payload: dict, extra_headers=None) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    headers.extend(extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _bearer_token(authorization: str) -> str:
    scheme, separator, token = authorization.strip().partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()


def _base64url_decode(value: str) -> str:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def _userinfo_url() -> str:
    explicit = os.environ.get("SSO_USERINFO_URL", "").strip()
    if explicit:
        return explicit
    return os.environ.get("SSO_API_URL", "").strip().rstrip("/") + "/oauth/userinfo"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
