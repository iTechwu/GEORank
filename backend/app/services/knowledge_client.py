"""Client for the canonical knowledge.dofe.ai service."""
from __future__ import annotations

import asyncio
import base64
import re
import threading
import time
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class KnowledgeClientError(RuntimeError):
    pass


class KnowledgeClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._sync_token: str | None = None
        self._sync_expires_at = 0.0
        self._sync_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(
            settings.KNOWLEDGE_API_URL.strip()
            and settings.KNOWLEDGE_SSO_ISSUER.strip()
            and settings.KNOWLEDGE_SSO_CLIENT_ID.strip()
            and settings.KNOWLEDGE_SSO_CLIENT_SECRET.strip()
        )

    @staticmethod
    def _allowlisted(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in {
            "knowledge.dofe.ai",
            "knowledge.local.dofe.ai",
            "sso.ixicai.cn",
        }

    async def _access_token(self) -> str:
        if self._token and self._expires_at > time.time() + 30:
            return self._token
        async with self._lock:
            if self._token and self._expires_at > time.time() + 30:
                return self._token
            issuer = settings.KNOWLEDGE_SSO_ISSUER.rstrip("/")
            if not self._allowlisted(issuer):
                raise KnowledgeClientError("knowledge SSO issuer is not allow-listed")
            try:
                async with httpx.AsyncClient(timeout=settings.KNOWLEDGE_TIMEOUT_SECONDS, verify=settings.KNOWLEDGE_VERIFY_TLS) as client:
                    response = await client.post(
                        f"{issuer}/oauth/token",
                        data={"grant_type": "client_credentials", "scope": settings.KNOWLEDGE_SSO_SCOPE},
                        auth=(settings.KNOWLEDGE_SSO_CLIENT_ID, settings.KNOWLEDGE_SSO_CLIENT_SECRET),
                    )
            except httpx.HTTPError as error:
                raise KnowledgeClientError("knowledge SSO token request failed") from error
            if response.status_code >= 400:
                oauth_error = ""
                try:
                    candidate = response.json().get("error", "")
                    if isinstance(candidate, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate):
                        oauth_error = candidate
                except (AttributeError, TypeError, ValueError):
                    pass
                suffix = f" ({oauth_error})" if oauth_error else ""
                raise KnowledgeClientError(
                    f"knowledge SSO token request returned HTTP {response.status_code}{suffix}"
                )
            payload = response.json()
            token = str(payload.get("access_token", "")).strip()
            if not token:
                raise KnowledgeClientError("knowledge SSO token response is invalid")
            self._token = token
            try:
                expires_in = int(payload.get("expires_in", 300))
            except (TypeError, ValueError):
                expires_in = 300
            self._expires_at = time.time() + max(1, expires_in - 30)
            return token

    async def search(self, query: str, *, top_k: int = 5, include_memories: bool = True) -> dict:
        if not self.configured:
            raise KnowledgeClientError("knowledge client is not configured")
        base_url = settings.KNOWLEDGE_API_URL.rstrip("/")
        if not self._allowlisted(base_url):
            raise KnowledgeClientError("knowledge API URL is not allow-listed")
        payload = {
            "query": query.strip(),
            "topK": max(1, min(50, top_k)),
            "includeMemories": include_memories,
        }
        # Knowledge 根据租户 ACL 解析 canonical role space（tenant.all）；
        # 客户端不硬编码空间 UUID。仅为兼容显式筛选场景时透传。
        if settings.knowledge_space_ids:
            payload["spaceIds"] = settings.knowledge_space_ids
        response = await self._search_request(base_url, payload)
        if response.status_code == 401:
            self._token = None
            self._expires_at = 0.0
            response = await self._search_request(base_url, payload)
        if response.status_code >= 400:
            raise KnowledgeClientError(f"knowledge search returned HTTP {response.status_code}")
        payload = response.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise KnowledgeClientError("knowledge search response is invalid")
        return data

    @property
    def infrastructure_space_id(self) -> str:
        if not self.configured or not settings.knowledge_space_ids:
            raise KnowledgeClientError("knowledge infrastructure space is not configured")
        return settings.knowledge_space_ids[0]

    def put_object(self, key: str, data: bytes, content_type: str) -> dict:
        payload = {
            "spaceId": self.infrastructure_space_id,
            "key": key,
            "mimeType": content_type,
            "dataBase64": base64.b64encode(data).decode("ascii"),
        }
        return self._sync_api_request("PUT", "/knowledge/integrations/objects", payload)

    def get_object(self, key: str) -> bytes:
        payload = {"spaceId": self.infrastructure_space_id, "key": key}
        data = self._sync_api_request("POST", "/knowledge/integrations/objects/read", payload)
        encoded = data.get("dataBase64")
        if not isinstance(encoded, str):
            raise KnowledgeClientError("knowledge object response is invalid")
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise KnowledgeClientError("knowledge object response is invalid") from error

    def delete_object(self, key: str) -> bool:
        payload = {"spaceId": self.infrastructure_space_id, "key": key}
        data = self._sync_api_request("POST", "/knowledge/integrations/objects/delete", payload)
        return data.get("deleted") is True

    async def put_graph_snapshot(
        self,
        external_entity_id: str,
        properties: dict,
        nodes: list[dict],
        relationships: list[dict],
    ) -> dict:
        return await self._async_api_request(
            "PUT",
            "/knowledge/integrations/graph-snapshots",
            {
                "spaceId": self.infrastructure_space_id,
                "externalEntityId": external_entity_id,
                "properties": properties,
                "nodes": nodes,
                "relationships": relationships,
            },
        )

    async def get_graph_snapshot(self, external_entity_id: str) -> dict:
        return await self._async_api_request(
            "POST",
            "/knowledge/integrations/graph-snapshots/read",
            {"spaceId": self.infrastructure_space_id, "externalEntityId": external_entity_id},
        )

    async def _search_request(self, base_url: str, payload: dict) -> httpx.Response:
        token = await self._access_token()
        try:
            async with httpx.AsyncClient(timeout=settings.KNOWLEDGE_TIMEOUT_SECONDS, verify=settings.KNOWLEDGE_VERIFY_TLS) as client:
                return await client.post(
                    f"{base_url}/yootun/v1/search",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Knowledge-Source-System": "georank",
                        "X-Knowledge-Tenant": settings.KNOWLEDGE_TENANT_SLUG,
                        "X-API-Version": "1",
                    },
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise KnowledgeClientError("knowledge search request failed") from error

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Knowledge-Source-System": "georank",
            "X-Knowledge-Tenant": settings.KNOWLEDGE_TENANT_SLUG,
            "X-API-Version": "1",
        }

    def _sync_access_token(self) -> str:
        if self._sync_token and self._sync_expires_at > time.time() + 30:
            return self._sync_token
        with self._sync_lock:
            if self._sync_token and self._sync_expires_at > time.time() + 30:
                return self._sync_token
            issuer = settings.KNOWLEDGE_SSO_ISSUER.rstrip("/")
            if not self._allowlisted(issuer):
                raise KnowledgeClientError("knowledge SSO issuer is not allow-listed")
            try:
                with httpx.Client(
                    timeout=settings.KNOWLEDGE_TIMEOUT_SECONDS,
                    verify=settings.KNOWLEDGE_VERIFY_TLS,
                ) as client:
                    response = client.post(
                        f"{issuer}/oauth/token",
                        data={"grant_type": "client_credentials", "scope": settings.KNOWLEDGE_SSO_SCOPE},
                        auth=(settings.KNOWLEDGE_SSO_CLIENT_ID, settings.KNOWLEDGE_SSO_CLIENT_SECRET),
                    )
            except httpx.HTTPError as error:
                raise KnowledgeClientError("knowledge SSO token request failed") from error
            if response.status_code >= 400:
                raise KnowledgeClientError(
                    f"knowledge SSO token request returned HTTP {response.status_code}"
                )
            payload = response.json()
            token = str(payload.get("access_token", "")).strip()
            if not token:
                raise KnowledgeClientError("knowledge SSO token response is invalid")
            self._sync_token = token
            try:
                expires_in = int(payload.get("expires_in", 300))
            except (TypeError, ValueError):
                expires_in = 300
            self._sync_expires_at = time.time() + max(1, expires_in - 30)
            return token

    def _sync_api_request(self, method: str, path: str, payload: dict) -> dict:
        base_url = settings.KNOWLEDGE_API_URL.rstrip("/")
        if not self._allowlisted(base_url):
            raise KnowledgeClientError("knowledge API URL is not allow-listed")
        response = self._send_sync(method, f"{base_url}{path}", payload)
        if response.status_code == 401:
            self._sync_token = None
            self._sync_expires_at = 0.0
            response = self._send_sync(method, f"{base_url}{path}", payload)
        return self._response_data(response, "knowledge infrastructure")

    def _send_sync(self, method: str, url: str, payload: dict) -> httpx.Response:
        try:
            with httpx.Client(
                timeout=settings.KNOWLEDGE_TIMEOUT_SECONDS,
                verify=settings.KNOWLEDGE_VERIFY_TLS,
            ) as client:
                return client.request(
                    method,
                    url,
                    headers=self._headers(self._sync_access_token()),
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise KnowledgeClientError("knowledge infrastructure request failed") from error

    async def _async_api_request(self, method: str, path: str, payload: dict) -> dict:
        base_url = settings.KNOWLEDGE_API_URL.rstrip("/")
        if not self._allowlisted(base_url):
            raise KnowledgeClientError("knowledge API URL is not allow-listed")
        response = await self._send_async(method, f"{base_url}{path}", payload)
        if response.status_code == 401:
            self._token = None
            self._expires_at = 0.0
            response = await self._send_async(method, f"{base_url}{path}", payload)
        return self._response_data(response, "knowledge infrastructure")

    async def _send_async(self, method: str, url: str, payload: dict) -> httpx.Response:
        token = await self._access_token()
        try:
            async with httpx.AsyncClient(
                timeout=settings.KNOWLEDGE_TIMEOUT_SECONDS,
                verify=settings.KNOWLEDGE_VERIFY_TLS,
            ) as client:
                return await client.request(method, url, headers=self._headers(token), json=payload)
        except httpx.HTTPError as error:
            raise KnowledgeClientError("knowledge infrastructure request failed") from error

    @staticmethod
    def _response_data(response: httpx.Response, operation: str) -> dict:
        if response.status_code >= 400:
            raise KnowledgeClientError(f"{operation} returned HTTP {response.status_code}")
        payload = response.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise KnowledgeClientError(f"{operation} response is invalid")
        return data


knowledge_client = KnowledgeClient()
