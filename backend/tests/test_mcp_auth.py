import base64
import json
import unittest

from app.mcp.auth import (
    McpAuthConfig,
    McpAuthMiddleware,
    McpUnauthorized,
    authenticate_bearer,
    current_mcp_auth,
    require_mcp_ability,
)


def jwt(payload: dict) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'RS256', 'typ': 'JWT'})}.{encode(payload)}.signature"


def config(**overrides) -> McpAuthConfig:
    values = {
        "enabled": True,
        "allow_system_token": True,
        "allow_cross_tenant": False,
        "write_token": "write-secret",
        "read_token": "read-secret",
        "default_tenant": "team-youhuitun",
        "sso_issuer": "https://sso.test.dofe.ai",
        "sso_client_id": "georank",
        "sso_userinfo_url": "https://sso-internal/oauth/userinfo",
        "sso_timeout_seconds": 3.0,
    }
    values.update(overrides)
    return McpAuthConfig(**values)


class McpAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_system_token_is_tenant_scoped(self) -> None:
        context = await authenticate_bearer(
            "Bearer write-secret",
            config(),
        )

        self.assertEqual(context.credential_type, "system")
        self.assertEqual(context.scope, "write")
        self.assertEqual(context.tenant_id, "team-youhuitun")
        self.assertEqual(context.abilities, ("*",))
        self.assertEqual(len(context.token_hash), 64)

    async def test_system_token_without_tenant_is_rejected_by_default(self) -> None:
        with self.assertRaises(McpUnauthorized):
            await authenticate_bearer(
                "Bearer write-secret",
                config(default_tenant=""),
            )

    async def test_read_system_token_has_bounded_abilities(self) -> None:
        context = await authenticate_bearer("Bearer read-secret", config())

        self.assertEqual(context.scope, "read")
        self.assertIn("georank:companies:read", context.abilities)
        self.assertIn("georank:diagnostics:read", context.abilities)
        self.assertIn("georank:experts:read", context.abilities)
        self.assertIn("georank:settings:read", context.abilities)
        self.assertNotIn("georank:diagnostics:write", context.abilities)

    async def test_sso_token_uses_selected_team_and_claim_scopes(self) -> None:
        token = jwt({
            "iss": "https://sso.test.dofe.ai/",
            "aud": ["georank", "account"],
            "sub": "user-from-jwt",
        })
        calls = []

        async def fetch_userinfo(url: str, provided: str, timeout: float) -> dict:
            calls.append((url, provided, timeout))
            return {
                "sub": "user-42",
                "selected_team_id": "team-youhuitun",
                "scope": "georank:companies:read georank:keywords:expand",
            }

        context = await authenticate_bearer(
            f"Bearer {token}",
            config(),
            fetch_userinfo=fetch_userinfo,
        )

        self.assertEqual(context.credential_type, "sso")
        self.assertEqual(context.subject, "user-42")
        self.assertEqual(context.tenant_id, "team-youhuitun")
        self.assertEqual(
            context.abilities,
            ("georank:companies:read", "georank:keywords:expand"),
        )
        self.assertEqual(calls, [("https://sso-internal/oauth/userinfo", token, 3.0)])

    async def test_sso_admin_role_grants_all_abilities(self) -> None:
        token = jwt({
            "iss": "https://sso.test.dofe.ai",
            "aud": "georank",
            "sub": "admin",
        })

        async def fetch_userinfo(_url: str, _provided: str, _timeout: float) -> dict:
            return {
                "sub": "admin",
                "selected_team_id": "team-youhuitun",
                "roles": ["super_admin"],
            }

        context = await authenticate_bearer(
            f"Bearer {token}", config(), fetch_userinfo=fetch_userinfo
        )

        self.assertEqual(context.abilities, ("*",))

    async def test_sso_token_without_selected_team_is_rejected(self) -> None:
        token = jwt({
            "iss": "https://sso.test.dofe.ai",
            "aud": "georank",
            "sub": "user-42",
        })

        async def fetch_userinfo(_url: str, _provided: str, _timeout: float) -> dict:
            return {"sub": "user-42"}

        with self.assertRaises(McpUnauthorized):
            await authenticate_bearer(
                f"Bearer {token}", config(), fetch_userinfo=fetch_userinfo
            )

    async def test_wrong_issuer_is_rejected_without_userinfo_call(self) -> None:
        token = jwt({"iss": "https://attacker.invalid", "aud": "georank", "sub": "x"})
        called = False

        async def fetch_userinfo(_url: str, _provided: str, _timeout: float) -> dict:
            nonlocal called
            called = True
            return {}

        with self.assertRaises(McpUnauthorized):
            await authenticate_bearer(
                f"Bearer {token}", config(), fetch_userinfo=fetch_userinfo
            )
        self.assertFalse(called)


class McpAuthMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, authorization: str | None = None):
        headers = []
        if authorization is not None:
            headers.append((b"authorization", authorization.encode("utf-8")))
        messages = []
        received = False

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": headers,
            },
            receive,
            send,
        )
        return messages

    async def test_missing_bearer_returns_jsonrpc_401(self) -> None:
        async def inner(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        messages = await self.request(McpAuthMiddleware(inner, config()))

        self.assertEqual(messages[0]["status"], 401)
        self.assertIn((b"www-authenticate", b"Bearer"), messages[0]["headers"])
        payload = json.loads(messages[1]["body"])
        self.assertEqual(payload["error"], {"code": -32001, "message": "Unauthorized"})

    async def test_context_is_available_only_during_authenticated_request(self) -> None:
        observed = []

        async def inner(_scope, _receive, send):
            observed.append(current_mcp_auth().tenant_id)
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = McpAuthMiddleware(inner, config())
        messages = await self.request(middleware, "Bearer write-secret")

        self.assertEqual(messages[0]["status"], 204)
        self.assertEqual(observed, ["team-youhuitun"])
        with self.assertRaises(McpUnauthorized):
            current_mcp_auth()

    async def test_tool_ability_is_enforced_inside_authenticated_request(self) -> None:
        called = False

        @require_mcp_ability("georank:diagnostics:write")
        async def write_tool():
            nonlocal called
            called = True

        async def inner(_scope, _receive, send):
            await write_tool()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = McpAuthMiddleware(inner, config())
        with self.assertRaises(PermissionError):
            await self.request(middleware, "Bearer read-secret")
        self.assertFalse(called)

        messages = await self.request(middleware, "Bearer write-secret")
        self.assertEqual(messages[0]["status"], 204)
        self.assertTrue(called)


if __name__ == "__main__":
    unittest.main()
