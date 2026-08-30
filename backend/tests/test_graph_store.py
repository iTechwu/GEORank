import unittest

from unittest.mock import AsyncMock, patch

from app.services.graph_store import upsert_company_graph


class GraphStoreContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unapproved_entity_label_before_opening_session(self):
        with self.assertRaisesRegex(ValueError, "实体类型"):
            await upsert_company_graph(
                "company-id",
                {},
                [{"name": "Injected", "type": "Person`) MATCH (n) DETACH DELETE n //"}],
                [],
            )

    async def test_rejects_unapproved_relationship_type_before_opening_session(self):
        with self.assertRaisesRegex(ValueError, "关系类型"):
            await upsert_company_graph(
                "company-id",
                {},
                [{"name": "Product", "type": "Product"}],
                [{"from": "Company", "to": "Product", "type": "OWNS`] DELETE n //"}],
            )

    async def test_writes_the_complete_snapshot_through_knowledge(self):
        with patch(
            "app.services.graph_store.knowledge_client.put_graph_snapshot",
            new=AsyncMock(return_value={"projection": {"status": "projected"}}),
        ) as put_snapshot:
            result = await upsert_company_graph(
                "company-id",
                {"name": "Example"},
                [{"name": "Product", "type": "Product", "props": {"version": "1"}}],
                [],
            )

        self.assertEqual(result["projection"]["status"], "projected")
        put_snapshot.assert_awaited_once_with(
            "company-id",
            {"name": "Example"},
            [{"name": "Product", "type": "Product", "properties": {"version": "1"}}],
            [],
        )


if __name__ == "__main__":
    unittest.main()
