from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.knowledge_client import KnowledgeClient, KnowledgeClientError


class _AsyncClient:
    def __init__(self, responses: list[MagicMock]) -> None:
        self._responses = responses
        self.post = AsyncMock(side_effect=responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _SyncClient:
    def __init__(self, responses: list[MagicMock]) -> None:
        self.post = MagicMock(side_effect=responses[:1])
        self.request = MagicMock(side_effect=responses[1:])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
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

    def test_search_refreshes_a_rejected_token_once(self) -> None:
        http = _AsyncClient([
            self._response({"access_token": "stale-token", "expires_in": 300}),
            self._response({"detail": "unauthorized"}, 401),
            self._response({"access_token": "fresh-token", "expires_in": 300}),
            self._response({"data": {"list": [], "total": 0, "page": 1, "limit": 5}}),
        ])
        client = KnowledgeClient()

        with patch("app.services.knowledge_client.httpx.AsyncClient", return_value=http), patch(
            "app.services.knowledge_client.settings", self._settings()
        ):
            result = asyncio.run(client.search("query"))

        self.assertEqual(result["total"], 0)
        self.assertEqual(http.post.await_count, 4)
        self.assertEqual(http.post.await_args_list[1].kwargs["headers"]["Authorization"], "Bearer stale-token")
        self.assertEqual(http.post.await_args_list[3].kwargs["headers"]["Authorization"], "Bearer fresh-token")

    def test_search_does_not_retry_an_acl_rejection(self) -> None:
        http = _AsyncClient([
            self._response({"access_token": "token-1", "expires_in": 300}),
            self._response({"detail": "forbidden"}, 403),
        ])
        client = KnowledgeClient()

        with patch("app.services.knowledge_client.httpx.AsyncClient", return_value=http), patch(
            "app.services.knowledge_client.settings", self._settings()
        ):
            with self.assertRaisesRegex(KnowledgeClientError, "HTTP 403"):
                asyncio.run(client.search("query"))

        self.assertEqual(http.post.await_count, 2)

    def test_token_failure_reports_only_the_oauth_error_code(self) -> None:
        http = _AsyncClient([
            self._response(
                {
                    "error": "unauthorized_client",
                    "error_description": "upstream-sensitive-detail",
                },
                400,
            )
        ])
        client = KnowledgeClient()

        with patch("app.services.knowledge_client.httpx.AsyncClient", return_value=http), patch(
            "app.services.knowledge_client.settings", self._settings()
        ):
            with self.assertRaisesRegex(
                KnowledgeClientError,
                r"HTTP 400 \(unauthorized_client\)$",
            ) as error:
                asyncio.run(client.search("query"))

        self.assertNotIn("upstream-sensitive-detail", str(error.exception))

    def test_transport_failure_is_reported_without_network_details(self) -> None:
        http = _AsyncClient([httpx.ConnectError("private-network-detail")])
        client = KnowledgeClient()

        with patch("app.services.knowledge_client.httpx.AsyncClient", return_value=http), patch(
            "app.services.knowledge_client.settings", self._settings()
        ):
            with self.assertRaisesRegex(
                KnowledgeClientError,
                r"knowledge SSO token request failed$",
            ) as error:
                asyncio.run(client.search("query"))

        self.assertNotIn("private-network-detail", str(error.exception))

    def test_object_write_uses_knowledge_path_without_duplicate_api_prefix(self) -> None:
        token_response = self._response({"access_token": "token-1", "expires_in": 300})
        object_response = self._response(
            {"data": {"key": "companies/1/raw.html", "sha256": "a" * 64}}
        )
        http = _SyncClient([token_response, object_response])
        client = KnowledgeClient()

        with patch("app.services.knowledge_client.httpx.Client", return_value=http), patch(
            "app.services.knowledge_client.settings", self._settings()
        ):
            result = client.put_object("companies/1/raw.html", b"test", "text/html")

        self.assertEqual(result["key"], "companies/1/raw.html")
        request = http.request.call_args
        self.assertEqual(request.args[0], "PUT")
        self.assertEqual(
            request.args[1],
            "https://knowledge.local.dofe.ai/api/knowledge/integrations/objects",
        )
        self.assertNotIn("/api/api/", request.args[1])
        self.assertEqual(request.kwargs["headers"]["X-Knowledge-Source-System"], "georank")


if __name__ == "__main__":
    unittest.main()
