import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.vector_store import VectorStore  # noqa: E402


class VectorStoreTests(unittest.TestCase):
    def test_legacy_hooks_are_noops_owned_by_knowledge(self):
        store = VectorStore()
        self.assertIsNone(store.ensure_collection())
        self.assertEqual(store.search_companies([0.1]), [])


if __name__ == "__main__":
    unittest.main()
