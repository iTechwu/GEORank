"""Short-lived encrypted Models credentials for MCP-triggered Celery work."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.mcp.auth import McpModelsProviderOverride

_VERSION = "v1"
_AAD = b"georank:mcp:async-models:v1"
_DEFAULT_TTL_SECONDS = 6 * 60 * 60


class AsyncModelsCredentialError(ValueError):
    """The delegated credential is missing, invalid, expired, or misbound."""


def seal_async_models_credential(
    provider: McpModelsProviderOverride,
    *,
    report_id: str,
    now: int | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "api_key": provider.api_key,
        "base_url": _validated_base_url(provider.base_url),
        "model": provider.model,
        "report_id": str(report_id),
        "issued_at": issued_at,
        "expires_at": issued_at + _ttl_seconds(),
    }
    nonce = os.urandom(12)
    encrypted = AESGCM(_encryption_key()).encrypt(
        nonce,
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        _AAD,
    )
    return f"{_VERSION}.{_base64url_encode(nonce + encrypted)}"


def open_async_models_credential(
    token: str,
    *,
    report_id: str,
    now: int | None = None,
) -> McpModelsProviderOverride:
    version, separator, encoded = str(token or "").partition(".")
    if not separator or version != _VERSION or not encoded:
        raise AsyncModelsCredentialError("异步 Models 凭据格式无效")
    try:
        sealed = _base64url_decode(encoded)
        if len(sealed) <= 12:
            raise ValueError("truncated")
        raw = AESGCM(_encryption_key()).decrypt(sealed[:12], sealed[12:], _AAD)
        payload = json.loads(raw)
    except Exception as exc:
        raise AsyncModelsCredentialError("异步 Models 凭据无法验证") from exc

    expected_report_id = str(report_id)
    if not hmac.compare_digest(str(payload.get("report_id") or ""), expected_report_id):
        raise AsyncModelsCredentialError("异步 Models 凭据与诊断任务不匹配")
    current_time = int(time.time() if now is None else now)
    if int(payload.get("expires_at") or 0) < current_time:
        raise AsyncModelsCredentialError("异步 Models 凭据已过期，请重新发起诊断")
    api_key = str(payload.get("api_key") or "")
    model = str(payload.get("model") or "").strip()
    if not api_key or not model:
        raise AsyncModelsCredentialError("异步 Models 凭据缺少模型访问上下文")
    return McpModelsProviderOverride(
        api_key=api_key,
        base_url=_validated_base_url(str(payload.get("base_url") or "")),
        model=model,
        source="mcp_async_models_credential",
    )


def _encryption_key() -> bytes:
    secret = os.environ.get("INTERNAL_API_SECRET", "").strip()
    if len(secret) < 32:
        raise AsyncModelsCredentialError("INTERNAL_API_SECRET 未配置或长度不足")
    return hmac.new(secret.encode("utf-8"), _AAD, hashlib.sha256).digest()


def _ttl_seconds() -> int:
    try:
        configured = int(
            os.environ.get(
                "GEORANK_ASYNC_MODELS_CREDENTIAL_TTL_SECONDS",
                str(_DEFAULT_TTL_SECONDS),
            )
        )
    except ValueError as exc:
        raise AsyncModelsCredentialError("异步 Models 凭据有效期配置无效") from exc
    return min(24 * 60 * 60, max(5 * 60, configured))


def _validated_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AsyncModelsCredentialError("Models Base URL 必须是可信 HTTPS 地址")
    return normalized


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)
