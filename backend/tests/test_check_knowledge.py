from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.knowledge_client import KnowledgeClientError
from app.scripts.check_knowledge import check


class KnowledgeCheckTest(unittest.TestCase):
    def test_check_returns_only_bounded_operational_metadata(self) -> None:
        client = SimpleNamespace(
            configured=True,
            search=AsyncMock(return_value={"list": [{"content": "must-not-leak"}]}),
        )
        with patch("app.scripts.check_knowledge.knowledge_client", client), patch(
            "app.scripts.check_knowledge.settings",
            SimpleNamespace(KNOWLEDGE_TENANT_SLUG="yootun"),
        ):
            result = asyncio.run(check("readiness"))

        self.assertEqual(
            result,
            {
                "status": "ok",
                "source_system": "georank",
                "tenant": "yootun",
                "hit_count": 1,
            },
        )
        client.search.assert_awaited_once_with("readiness", top_k=1, include_memories=False)

    def test_check_rejects_incomplete_configuration(self) -> None:
        with patch(
            "app.scripts.check_knowledge.knowledge_client",
            SimpleNamespace(configured=False),
        ):
            with self.assertRaisesRegex(KnowledgeClientError, "configuration is incomplete"):
                asyncio.run(check("readiness"))


if __name__ == "__main__":
    unittest.main()
