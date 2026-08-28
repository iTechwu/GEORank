import base64
import json
import unittest

from app.mcp import server
from app.mcp.auth import McpAuthConfig, McpAuthMiddleware


def jwt(payload: dict) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'RS256'})}.{encode(payload)}.signature"


AUTH_CONFIG = McpAuthConfig(
    enabled=True,
    allow_system_token=False,
    allow_cross_tenant=False,
    write_token="",
    read_token="",
    default_tenant="",
    sso_issuer="https://sso.test.dofe.ai",
    sso_client_id="georank",
    sso_userinfo_url="https://sso-internal/oauth/userinfo",
    sso_timeout_seconds=3,
)


TOOL_CALLS = [
    (server.georank_system_status, ()),
    (server.georank_score_ai_friendliness, ("优惠豚：一个优惠信息平台",)),
    (server.georank_generate_jsonld, ("优惠豚：一个优惠信息平台",)),
    (server.georank_generate_llms_txt, ("优惠豚：一个优惠信息平台",)),
    (server.georank_generate_title, ("优惠豚：一个优惠信息平台",)),
    (server.georank_generate_knowledge_base, ("优惠豚：一个优惠信息平台",)),
    (server.georank_list_companies, ()),
    (server.georank_get_company, ("youhuitun",)),
    (server.georank_company_similar, ("youhuitun",)),
    (server.georank_company_pipeline_status, ("00000000-0000-0000-0000-000000000001",)),
    (server.georank_diagnose_url, ("https://www.youhuitun.com",)),
    (server.georank_get_diagnostic_report, ("00000000-0000-0000-0000-000000000001",)),
    (server.georank_diagnostic_history, ()),
    (server.georank_expand_keywords, (["优惠豚"],)),
    (server.georank_solution_channels, ()),
    (server.georank_solution_chat, ("优惠豚如何做 GEO？",)),
    (server.georank_list_experts, ()),
    (server.georank_get_expert, ("geo-expert",)),
    (server.georank_list_content, ()),
    (server.georank_get_content, ("geo-guide",)),
    (server.georank_get_public_settings, ()),
]


class McpToolAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def call_as_sso(self, scopes: list[str], operation) -> None:
        token = jwt({
            "iss": "https://sso.test.dofe.ai",
            "aud": "georank",
            "sub": "user-42",
        })

        async def fetch_userinfo(_url: str, _provided: str, _timeout: float) -> dict:
            return {
                "sub": "user-42",
                "selected_team_id": "team-youhuitun",
                "scopes": scopes,
            }

        async def inner(_scope, _receive, send):
            await operation()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = McpAuthMiddleware(
            inner, AUTH_CONFIG, fetch_userinfo=fetch_userinfo
        )
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"authorization", f"Bearer {token}".encode("utf-8"))],
            },
            receive,
            send,
        )

    async def test_every_mcp_tool_rejects_an_authenticated_user_without_abilities(self) -> None:
        async def operation():
            for function, arguments in TOOL_CALLS:
                with self.subTest(tool=function.__name__):
                    with self.assertRaises(PermissionError):
                        await function(*arguments)

        await self.call_as_sso([], operation)

    async def test_content_generator_accepts_its_specific_ability(self) -> None:
        result = None

        async def operation():
            nonlocal result
            result = await server.georank_score_ai_friendliness(
                "优惠豚：https://www.youhuitun.com，提供优惠信息和 GEO 内容"
            )

        await self.call_as_sso(["georank:content:generate"], operation)
        self.assertIsInstance(result["score"], int)

    async def test_system_status_exposes_tenant_scope_without_credential_material(self) -> None:
        result = None

        async def operation():
            nonlocal result
            result = await server.georank_system_status()

        await self.call_as_sso(["georank:system:read"], operation)
        self.assertEqual(result["service"], "georank")
        self.assertEqual(result["tenant_id"], "team-youhuitun")
        self.assertEqual(result["mcp"]["credential_type"], "sso")
        self.assertEqual(result["mcp"]["scope"], "write")
        self.assertEqual(result["mcp"]["tenant_mode"], "tenant_scoped")
        self.assertEqual(result["mcp"]["abilities"], ["georank:system:read"])
        self.assertNotIn("token", json.dumps(result))
        self.assertNotIn("hash", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
