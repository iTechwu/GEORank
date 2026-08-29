from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.knowledge_client import KnowledgeClient, KnowledgeClientError


class _AsyncClient:
    def __init__(self, responses: list[MagicMock]) -> None:
        self._responses = responses
        self.post = AsyncMock(side_effect=responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class KnowledgeClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = {
            "KNOWLEDGE_API_URL": "https://knowledge.local.dofe.ai/api",
            "KNOWLEDGE_SSO_ISSUER": "https://sso.ixicai.cn/api",
            "KNOWLEDGE_SSO_CLIENT_ID": "georank-dofe-ai",
            "KNOWLEDGE_SSO_CLIENT_SECRET": "test-secret",
            "KNOWLEDGE_SSO_SCOPE": "service:access",
            "KNOWLEDGE_TENANT_SLUG": "yootun",
            "KNOWLEDGE_TIMEOUT_SECONDS": 15.0,
            "KNOWLEDGE_VERIFY_TLS": True,
            "knowledge_space_ids": ["space-1"],
        }

    def _settings(self, **overrides):
        return SimpleNamespace(**{**self.settings, **overrides})

    def _response(self, payload: dict, status_code: int = 200) -> MagicMock:
        response = MagicMock(status_code=status_code)
        response.json.return_value = payload
        return response

    def test_search_uses_m2m_identity_and_caches_token(self) -> None:
        token_response = self._response({"access_token": "token-1", "expires_in": 300})
        search_response = self._response(
            {"data": {"list": [{"title": "Evidence"}], "total": 1, "page": 1, "limit": 5}}
        )
        http = _AsyncClient([token_response, search_response, search_response])
        client = KnowledgeClient()

        with patch("app.services.knowledge_client.httpx.AsyncClient", return_value=http), patch(
            "app.services.knowledge_client.settings", self._settings()
        ):
            first = asyncio.run(client.search("query"))
            second = asyncio.run(client.search("query"))

        self.assertEqual(first["total"], 1)
        self.assertEqual(second["list"][0]["title"], "Evidence")
        self.assertEqual(http.post.await_count, 3)
        token_call = http.post.await_args_list[0]
        self.assertEqual(token_call.args[0], "https://sso.ixicai.cn/api/oauth/token")
        self.assertEqual(token_call.kwargs["data"]["scope"], "service:access")
        search_call = http.post.await_args_list[1]
        self.assertEqual(search_call.args[0], "https://knowledge.local.dofe.ai/api/yootun/v1/search")
        self.assertEqual(search_call.kwargs["headers"]["X-Knowledge-Source-System"], "georank")
        self.assertEqual(search_call.kwargs["headers"]["X-Knowledge-Tenant"], "yootun")
        self.assertEqual(search_call.kwargs["json"]["spaceIds"], ["space-1"])

    def test_rejects_non_allowlisted_endpoint_before_request(self) -> None:
        client = KnowledgeClient()
        with patch(
            "app.services.knowledge_client.settings",
            self._settings(KNOWLEDGE_API_URL="https://attacker.example/api"),
        ):
            with self.assertRaisesRegex(KnowledgeClientError, "not allow-listed"):
                asyncio.run(client.search("query"))

    def test_short_token_is_not_cached_past_its_expiry(self) -> None:
        token_response = self._response({"access_token": "short-token", "expires_in": 10})
        http = _AsyncClient([token_response])
        client = KnowledgeClient()

        with patch("app.services.knowledge_client.httpx.AsyncClient", return_value=http), patch(
            "app.services.knowledge_client.settings", self._settings()
        ), patch("app.services.knowledge_client.time.time", return_value=100.0):
            self.assertEqual(asyncio.run(client._access_token()), "short-token")

        self.assertEqual(client._expires_at, 101.0)


if __name__ == "__main__":
    unittest.main()
