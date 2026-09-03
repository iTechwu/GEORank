import unittest
from unittest.mock import patch

from app.tasks.crawl import (
    MAX_DIAGNOSTIC_HTML_CHARS,
    bounded_diagnostic_html,
    persist_crawl_html,
)
from app.services.storage import StorageService
from app.services.knowledge_client import KnowledgeClientError


class _StorageProbe:
    def __init__(self, *, put_ok: bool, stored: bytes | None):
        self.put_ok = put_ok
        self.stored = stored

    def put(self, _key: str, _data: bytes, content_type: str = "text/html") -> bool:
        return self.put_ok

    def get(self, _key: str) -> bytes | None:
        return self.stored


class CompanyPipelineContractTests(unittest.TestCase):
    def test_diagnostic_html_is_bounded_for_database_handoff(self):
        html = "x" * (MAX_DIAGNOSTIC_HTML_CHARS + 10)

        self.assertEqual(
            len(bounded_diagnostic_html(html)),
            MAX_DIAGNOSTIC_HTML_CHARS,
        )

    def test_crawl_html_requires_durable_storage(self):
        with self.assertRaisesRegex(RuntimeError, "对象存储"):
            persist_crawl_html(
                _StorageProbe(put_ok=False, stored=b"<html>fallback only</html>"),
                "companies/example/raw.html",
                "<html>example</html>",
            )

    def test_storage_writes_and_reads_only_through_knowledge(self):
        storage = StorageService()
        with patch(
            "app.services.storage.knowledge_client.put_object",
            return_value={"key": "companies/example/raw.html"},
        ) as put_object, patch(
            "app.services.storage.knowledge_client.get_object",
            return_value=b"fresh",
        ) as get_object:
            self.assertTrue(storage.put("companies/example/raw.html", b"fresh"))
            self.assertEqual(storage.get("companies/example/raw.html"), b"fresh")

        put_object.assert_called_once_with("companies/example/raw.html", b"fresh", "text/html")
        get_object.assert_called_once_with("companies/example/raw.html")

    def test_storage_does_not_fall_back_when_knowledge_is_unavailable(self):
        storage = StorageService()
        with patch(
            "app.services.storage.knowledge_client.put_object",
            side_effect=KnowledgeClientError("unavailable"),
        ):
            self.assertFalse(storage.put("companies/example/raw.html", b"fresh"))


if __name__ == "__main__":
    unittest.main()
