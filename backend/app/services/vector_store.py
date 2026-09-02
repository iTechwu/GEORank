"""Compatibility facade for vector operations owned by Knowledge."""
from __future__ import annotations

from typing import Optional


class VectorStore:
    """Deprecated local-vector facade; canonical storage lives in Knowledge."""

    def ensure_collection(self) -> None:
        return None

    def upsert_company_vectors(self, company_id: str, chunks: list[dict]) -> None:
        return None

    def search_companies(
        self, query_vector: list[float], top_k: int = 5, category: Optional[str] = None
    ) -> list[dict]:
        # Vector retrieval is performed by KnowledgeClient.search in the RAG path.
        return []

    async def get_similar_company_ids(self, company_id: str, top_k: int = 3) -> list[str]:
        return []

    def delete_company_vectors(self, company_id: str) -> None:
        return None


vector_store = VectorStore()
