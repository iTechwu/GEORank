import importlib
import sys
import types
import unittest
from unittest.mock import patch

from app.mcp.auth import McpAuthConfig, McpAuthMiddleware


class FakeMcp:
    def __init__(self, app) -> None:
        self.app = app

    def streamable_http_app(self):
        return self.app


class McpAppAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.modules.pop("app.mcp.app", None)

    def tearDown(self) -> None:
        sys.modules.pop("app.mcp.app", None)

    def load_app_module(self, raw_app):
        fake_server = types.ModuleType("app.mcp.server")
        fake_server.mcp = FakeMcp(raw_app)
        with patch.dict(sys.modules, {"app.mcp.server": fake_server}):
            return importlib.import_module("app.mcp.app")

    def test_standalone_app_is_wrapped_by_auth_middleware(self) -> None:
        raw_app = object()
        module = self.load_app_module(raw_app)

        protected = module.mcp_asgi_app(
            McpAuthConfig(
                enabled=True,
                allow_system_token=True,
                allow_cross_tenant=False,
                write_token="secret",
                read_token="",
                default_tenant="team-a",
                sso_issuer="",
                sso_client_id="",
                sso_userinfo_url="",
                sso_timeout_seconds=3,
            )
        )

        self.assertIsInstance(protected, McpAuthMiddleware)
        self.assertIs(protected.app, raw_app)

    def test_embedded_mount_uses_the_same_protected_app(self) -> None:
        raw_app = object()
        module = self.load_app_module(raw_app)

        class FakeFastApi:
            def __init__(self) -> None:
                self.mounts = []

            def mount(self, path, app) -> None:
                self.mounts.append((path, app))

        fastapi = FakeFastApi()
        result = module.mount_mcp_app(fastapi, path="/mcp")

        self.assertIs(result, fastapi)
        self.assertEqual(fastapi.mounts[0][0], "/mcp")
        self.assertIsInstance(fastapi.mounts[0][1], McpAuthMiddleware)


if __name__ == "__main__":
    unittest.main()
