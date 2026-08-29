"""Client for the canonical knowledge.dofe.ai service."""
from __future__ import annotations

import asyncio
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

    @property
    def configured(self) -> bool:
        return bool(
            settings.KNOWLEDGE_API_URL.strip()
            and settings.KNOWLEDGE_SSO_ISSUER.strip()
            and settings.KNOWLEDGE_SSO_CLIENT_ID.strip()
            and settings.KNOWLEDGE_SSO_CLIENT_SECRET.strip()
            and settings.knowledge_space_ids
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
            async with httpx.AsyncClient(timeout=settings.KNOWLEDGE_TIMEOUT_SECONDS, verify=settings.KNOWLEDGE_VERIFY_TLS) as client:
                response = await client.post(
                    f"{issuer}/oauth/token",
                    data={"grant_type": "client_credentials", "scope": settings.KNOWLEDGE_SSO_SCOPE},
                    auth=(settings.KNOWLEDGE_SSO_CLIENT_ID, settings.KNOWLEDGE_SSO_CLIENT_SECRET),
                )
            if response.status_code >= 400:
                raise KnowledgeClientError(f"knowledge SSO token request returned HTTP {response.status_code}")
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
            "spaceIds": settings.knowledge_space_ids,
            "topK": max(1, min(50, top_k)),
            "includeMemories": include_memories,
        }
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

    async def _search_request(self, base_url: str, payload: dict) -> httpx.Response:
        token = await self._access_token()
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


knowledge_client = KnowledgeClient()
