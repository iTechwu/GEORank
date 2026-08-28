import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.mcp import server
from app.mcp.auth import McpAuthConfig, McpAuthMiddleware
from app.models.diagnostic import DiagnosticStatus


AUTH_CONFIG = McpAuthConfig(
    enabled=True,
    allow_system_token=True,
    allow_cross_tenant=False,
    write_token="write-secret",
    read_token="",
    default_tenant="team-youhuitun",
    sso_issuer="",
    sso_client_id="",
    sso_userinfo_url="",
    sso_timeout_seconds=3,
)


class FakeResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return list(self.value)


class FakeSession:
    def __init__(self, reports=()) -> None:
        self.reports = list(reports)
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return None

    async def execute(self, statement):
        query = str(statement)
        params = statement.compile().params
        visible = self.reports
        if "diagnostic_reports.tenant_id =" in query:
            tenant = next(
                (value for key, value in params.items() if key.startswith("tenant_id")),
                None,
            )
            visible = [report for report in visible if report.tenant_id == tenant]
        if "diagnostic_reports.id =" in query:
            report_id = next(
                (value for key, value in params.items() if key.startswith("id_")),
                None,
            )
            visible = [report for report in visible if report.id == report_id]
        if "diagnostic_reports.idempotency_key =" in query:
            key = next(
                (
                    value
                    for name, value in params.items()
                    if name.startswith("idempotency_key")
                ),
                None,
            )
            visible = [report for report in visible if report.idempotency_key == key]
        if "ORDER BY" in query:
            return FakeResult(visible)
        return FakeResult(visible[0] if visible else None)

    def add(self, report) -> None:
        if report.id is None:
            report.id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        self.added.append(report)
        self.reports.append(report)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _report) -> None:
        return None


def report(**overrides):
    values = {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "tenant_id": "team-youhuitun",
        "idempotency_key": "daily:2026-08-28:article:3",
        "request_fingerprint": "4ee7812cd5afa6d7fefcca3e695b931353fb0e41e7b79e78018cd252a177bbd8",
        "url": "https://www.youhuitun.com",
        "company_id": None,
        "status": DiagnosticStatus.PENDING,
        "overall_score": None,
        "schema_analysis": None,
        "content_analysis": None,
        "meta_analysis": None,
        "citation_analysis": None,
        "recommendations": None,
        "error_message": None,
        "created_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class McpDiagnosticScopeTests(unittest.IsolatedAsyncioTestCase):
    async def call_as_system(self, operation):
        result = None

        async def inner(_scope, _receive, send):
            nonlocal result
            result = await operation()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = McpAuthMiddleware(inner, AUTH_CONFIG)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"authorization", b"Bearer write-secret")],
            },
            receive,
            send,
        )
        return result

    async def test_repeated_matching_idempotency_key_reuses_report(self) -> None:
        session = FakeSession([report()])

        with patch.object(server, "open_session", return_value=session):
            result = await self.call_as_system(
                lambda: server.georank_diagnose_url(
                    "https://www.youhuitun.com",
                    idempotency_key="daily:2026-08-28:article:3",
                )
            )

        self.assertTrue(result["reused"])
        self.assertEqual(result["report_id"], "00000000-0000-0000-0000-000000000001")
        self.assertEqual(session.added, [])

    async def test_reused_key_with_different_request_is_rejected(self) -> None:
        session = FakeSession([report(request_fingerprint="different")])

        with patch.object(server, "open_session", return_value=session):
            with self.assertRaisesRegex(ValueError, "idempotency_key conflict"):
                await self.call_as_system(
                    lambda: server.georank_diagnose_url(
                        "https://www.youhuitun.com",
                        idempotency_key="daily:2026-08-28:article:3",
                    )
                )

    async def test_new_report_is_tenant_scoped_and_dispatched_once(self) -> None:
        session = FakeSession()
        access = SimpleNamespace(
            reservation_id=uuid.UUID("00000000-0000-0000-0000-000000000088")
        )
        reserve = AsyncMock(return_value=access)

        with (
            patch.object(server, "open_session", return_value=session),
            patch.object(server, "resolve_system_async_ai_access", reserve),
            patch("app.core.celery_app.celery_app.send_task") as send_task,
        ):
            result = await self.call_as_system(
                lambda: server.georank_diagnose_url(
                    "https://www.youhuitun.com",
                    idempotency_key="daily:2026-08-28:article:4",
                )
            )

        self.assertFalse(result["reused"])
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].tenant_id, "team-youhuitun")
        self.assertEqual(session.added[0].idempotency_key, "daily:2026-08-28:article:4")
        self.assertEqual(len(session.added[0].request_fingerprint), 64)
        send_task.assert_called_once()

    async def test_report_lookup_does_not_reveal_another_tenant(self) -> None:
        other = report(tenant_id="team-other")
        session = FakeSession([other])

        with patch.object(server, "open_session", return_value=session):
            result = await self.call_as_system(
                lambda: server.georank_get_diagnostic_report(str(other.id))
            )

        self.assertEqual(result, {"found": False, "report_id": str(other.id)})

    async def test_history_only_returns_current_tenant(self) -> None:
        own = report()
        other = report(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            tenant_id="team-other",
        )
        session = FakeSession([own, other])

        with patch.object(server, "open_session", return_value=session):
            result = await self.call_as_system(server.georank_diagnostic_history)

        self.assertEqual([item["report_id"] for item in result], [str(own.id)])


if __name__ == "__main__":
    unittest.main()
