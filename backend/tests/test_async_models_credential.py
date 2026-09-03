from __future__ import annotations

import os
import unittest
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from app.mcp.async_models_credential import (
    AsyncModelsCredentialError,
    open_async_models_credential,
    seal_async_models_credential,
)
from app.mcp.auth import McpModelsProviderOverride


class AsyncModelsCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {
                "INTERNAL_API_SECRET": "test-only-internal-secret-with-32-characters",
                "GEORANK_ASYNC_MODELS_CREDENTIAL_TTL_SECONDS": "600",
            },
            clear=False,
        )
        self.environment.start()
        self.provider = McpModelsProviderOverride(
            api_key="test-models-key",
            base_url="https://models.example.test/api/v1",
            model="test-chat-model",
        )

    def tearDown(self) -> None:
        self.environment.stop()

    def test_round_trip_is_bound_to_report_and_hides_plaintext(self) -> None:
        token = seal_async_models_credential(
            self.provider,
            report_id="report-1",
            now=1000,
        )

        self.assertNotIn("test-models-key", token)
        opened = open_async_models_credential(
            token,
            report_id="report-1",
            now=1100,
        )
        self.assertEqual(opened.api_key, "test-models-key")
        self.assertEqual(opened.model, "test-chat-model")
        self.assertEqual(opened.source, "mcp_async_models_credential")

    def test_rejects_wrong_report_and_expired_credential(self) -> None:
        token = seal_async_models_credential(
            self.provider,
            report_id="report-1",
            now=1000,
        )

        with self.assertRaises(AsyncModelsCredentialError):
            open_async_models_credential(token, report_id="report-2", now=1100)
        with self.assertRaises(AsyncModelsCredentialError):
            open_async_models_credential(token, report_id="report-1", now=1601)

    def test_rejects_tampering_and_insecure_base_url(self) -> None:
        token = seal_async_models_credential(
            self.provider,
            report_id="report-1",
            now=1000,
        )

        with self.assertRaises(AsyncModelsCredentialError):
            open_async_models_credential(token[:-1] + "A", report_id="report-1", now=1100)
        with self.assertRaises(AsyncModelsCredentialError):
            seal_async_models_credential(
                McpModelsProviderOverride(
                    api_key="test-models-key",
                    base_url="http://models.example.test/api/v1",
                    model="test-chat-model",
                ),
                report_id="report-1",
                now=1000,
            )


class ModelsMcpDiagnosticDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_key_dispatches_encrypted_job_without_system_reservation(self) -> None:
        from app.mcp import auth, server

        context = auth.McpAuthContext(
            credential_type="models_api_key",
            scope="write",
            token_hash="fingerprint",
            tenant_id="tenant-1",
            subject="key-1",
            abilities=("*",),
            api_key_id="key-1",
            models_api_key="test-models-key",
            models_base_url="https://models.example.test/api/v1",
            models_chat_model="test-chat-model",
        )
        queued = []

        class FakeSession:
            report = None

            def add(self, report):
                self.report = report

            async def commit(self):
                return None

            async def refresh(self, report):
                if report.id is None:
                    report.id = uuid.uuid4()

        session = FakeSession()

        @asynccontextmanager
        async def fake_session():
            yield session

        def send_task(name, args):
            queued.append((name, args))

        reset = auth._AUTH_CONTEXT.set(context)
        try:
            with patch.dict(os.environ, {
                "INTERNAL_API_SECRET": "test-only-internal-secret-with-32-characters",
            }, clear=False), patch.object(server, "open_session", fake_session), patch.object(
                server,
                "resolve_system_async_ai_access",
                new_callable=AsyncMock,
            ) as system_access, patch(
                "app.core.celery_app.celery_app.send_task",
                side_effect=send_task,
            ):
                result = await server.georank_diagnose_url("https://example.test")
        finally:
            auth._AUTH_CONTEXT.reset(reset)

        system_access.assert_not_called()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(queued), 1)
        task_name, args = queued[0]
        self.assertEqual(task_name, "app.tasks.crawl.crawl_diagnostic_page")
        self.assertIsNone(args[2])
        self.assertNotIn("test-models-key", args[3])
        with patch.dict(os.environ, {
            "INTERNAL_API_SECRET": "test-only-internal-secret-with-32-characters",
        }, clear=False):
            opened = open_async_models_credential(args[3], report_id=result["report_id"])
        self.assertEqual(opened.api_key, "test-models-key")


if __name__ == "__main__":
    unittest.main()
