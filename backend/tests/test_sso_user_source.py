import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from starlette.requests import Request

from app.core.deps import _get_user_from_token
from app.api.routes.auth import SSO_STATE_COOKIE, sso_callback, sso_start
from app.api.routes.admin import (
    AdminPasswordResetRequest,
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
    create_user_admin,
    delete_user_admin,
    reset_user_password_admin,
    update_user_admin,
)
from fastapi import HTTPException


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response: _Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return self.response


class _OidcClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return _Response(200, {"access_token": "verified-sso-token"})

    async def get(self, *_args, **_kwargs):
        return _Response(200, {"sub": "sso-user-1", "email": "member@example.com"})


class SsoUserSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_start_uses_sso_authorization_code_with_pkce(self):
        with patch("app.api.routes.auth.settings.SSO_AUTH_REQUIRED", True), patch(
            "app.api.routes.auth.settings.SSO_CLIENT_SECRET", "test-secret"
        ), patch(
            "app.api.routes.auth.settings.SSO_REDIRECT_URI",
            "https://georank.dofe.ai/auth/oidc/callback",
        ):
            response = await sso_start(return_to="/profile", locale="zh-CN")

        location = response.headers["location"]
        self.assertTrue(location.startswith("https://sso.ixicai.cn/api/oauth/authorize?"))
        self.assertIn("code_challenge_method=S256", location)
        self.assertIn("redirect_uri=https%3A%2F%2Fgeorank.dofe.ai%2Fauth%2Foidc%2Fcallback", location)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Path=/", response.headers["set-cookie"])

    async def test_login_callback_returns_verified_sso_token_to_web_session(self):
        state = "state-1"
        encoded = jwt.encode(
            {
                "state": state,
                "verifier": "verifier-1",
                "return_to": "/profile",
                "locale": "zh-CN",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            "test-secret-key",
            algorithm="HS256",
        )
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/api/auth/sso/callback",
            "headers": [(b"cookie", f"{SSO_STATE_COOKIE}={encoded}".encode())],
        })
        with patch("app.api.routes.auth.settings.SECRET_KEY", "test-secret-key"), patch(
            "app.api.routes.auth.settings.SSO_CLIENT_SECRET", "test-client-secret"
        ), patch(
            "app.api.routes.auth.settings.SSO_REDIRECT_URI",
            "https://georank.dofe.ai/auth/oidc/callback",
        ), patch(
            "app.api.routes.auth.settings.PUBLIC_BASE_URL", "https://georank.dofe.ai"
        ), patch("app.api.routes.auth.httpx.AsyncClient", return_value=_OidcClient()):
            response = await sso_callback(request, code="code-1", state=state)

        self.assertEqual(
            response.headers["location"],
            "https://georank.dofe.ai/login?return=%2Fprofile#access_token=verified-sso-token",
        )
        self.assertIn("Path=/", response.headers["set-cookie"])
        self.assertIn("Max-Age=0", response.headers["set-cookie"])

    async def test_sso_userinfo_is_the_identity_authority(self):
        expected_user = SimpleNamespace(
            id="user-1",
            email="member@example.com",
            sso_subject="sso-user-1",
            is_active=True,
            is_verified=True,
        )
        result = SimpleNamespace(scalar_one_or_none=lambda: expected_user)
        db = SimpleNamespace(execute=AsyncMock(return_value=result))
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="sso-token")

        with patch("app.core.deps.settings.SSO_AUTH_REQUIRED", True), patch(
            "app.core.deps.httpx.AsyncClient",
            return_value=_Client(_Response(200, {"sub": "sso-user-1", "email": "member@example.com"})),
        ):
            user = await _get_user_from_token(credentials, db)

        self.assertIs(user, expected_user)
        db.execute.assert_awaited_once()

    async def test_existing_projection_refreshes_sso_owned_identity_fields(self):
        expected_user = SimpleNamespace(
            id="user-1",
            email="old@example.com",
            sso_subject="sso-user-1",
            is_active=True,
            is_verified=False,
        )
        found = SimpleNamespace(scalar_one_or_none=lambda: expected_user)
        no_duplicate = SimpleNamespace(scalar_one_or_none=lambda: None)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[found, no_duplicate]),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="sso-token")

        with patch("app.core.deps.settings.SSO_AUTH_REQUIRED", True), patch(
            "app.core.deps.httpx.AsyncClient",
            return_value=_Client(_Response(200, {
                "sub": "sso-user-1",
                "email": "new@example.com",
                "email_verified": True,
            })),
        ):
            user = await _get_user_from_token(credentials, db)

        self.assertIs(user, expected_user)
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(user.is_verified)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(expected_user)

    async def test_sso_mode_rejects_all_local_identity_mutations(self):
        db = SimpleNamespace(execute=AsyncMock())
        admin = SimpleNamespace(id="admin-1")
        operations = (
            create_user_admin(
                AdminUserCreateRequest(
                    email="new@example.com",
                    username="new-user",
                    password="password-1",
                ),
                db,
                admin,
            ),
            update_user_admin(
                "00000000-0000-0000-0000-000000000001",
                AdminUserUpdateRequest(email="changed@example.com"),
                db,
                admin,
            ),
            reset_user_password_admin(
                "00000000-0000-0000-0000-000000000001",
                AdminPasswordResetRequest(password="password-2"),
                db,
                admin,
            ),
            delete_user_admin(
                "00000000-0000-0000-0000-000000000001",
                db,
                admin,
            ),
        )

        with patch("app.api.routes.admin.settings.SSO_AUTH_REQUIRED", True):
            for operation in operations:
                with self.subTest(operation=operation.cr_code.co_name), self.assertRaises(HTTPException) as raised:
                    await operation
                self.assertEqual(raised.exception.status_code, 409)
        db.execute.assert_not_awaited()

    async def test_first_sso_login_creates_only_a_local_authorization_projection(self):
        missing = SimpleNamespace(scalar_one_or_none=lambda: None)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[missing, missing]),
            add=MagicMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="sso-token")

        with patch("app.core.deps.settings.SSO_AUTH_REQUIRED", True), patch(
            "app.core.deps.httpx.AsyncClient",
            return_value=_Client(_Response(200, {"sub": "sso-user-2", "email": "new@example.com"})),
        ):
            user = await _get_user_from_token(credentials, db)

        self.assertEqual(user.sso_subject, "sso-user-2")
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(user.hashed_password.startswith("!sso-managed:"))
        db.add.assert_called_once_with(user)
        db.commit.assert_awaited_once()

    async def test_rejected_sso_token_never_falls_back_to_local_jwt(self):
        db = SimpleNamespace(execute=AsyncMock())
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="local-or-invalid-token")

        with patch("app.core.deps.settings.SSO_AUTH_REQUIRED", True), patch(
            "app.core.deps.httpx.AsyncClient",
            return_value=_Client(_Response(401, {"detail": "unauthorized"})),
        ):
            user = await _get_user_from_token(credentials, db)

        self.assertIsNone(user)
        db.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
