import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.company_retrieval import rank_similar_companies
from app.services.knowledge_client import KnowledgeClientError


def _company(
    name: str,
    *,
    category: str,
    tags: list[str],
    tech_stack: list[str] | None = None,
    geo_score: float = 80,
    upvotes: int = 0,
    is_geo_certified: bool = False,
    short_description: str = "",
    description: str = "",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        category=category,
        tags=tags,
        tech_stack=tech_stack or [],
        geo_score=geo_score,
        upvotes=upvotes,
        is_geo_certified=is_geo_certified,
        short_description=short_description,
        description=description,
        tech_level=None,
        funding_stage=None,
    )


class CompanyRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_rank_similar_companies_does_not_fallback_to_local_company_fields(self):
        target = _company("Target", category="教育培训", tags=["教育"])

        with patch(
            "app.services.knowledge_client.knowledge_client.search",
            new=AsyncMock(return_value={"list": []}),
        ):
            results = await rank_similar_companies(None, target, limit=3)

        self.assertEqual(results, [])

    async def test_rank_similar_companies_degrades_to_empty_when_knowledge_is_unavailable(self):
        target = _company("Target", category="教育培训", tags=["教育"])

        with patch(
            "app.services.knowledge_client.knowledge_client.search",
            new=AsyncMock(side_effect=KnowledgeClientError("not configured")),
        ):
            results = await rank_similar_companies(None, target, limit=3)

        self.assertEqual(results, [])

    async def test_rank_similar_companies_maps_only_knowledge_citation_ids(self):
        target = _company("Target", category="教育培训", tags=["教育"])
        authoritative_match = _company("Knowledge Match", category="企业服务", tags=["软件"])
        knowledge_result = {
            "list": [{
                "title": "企业档案",
                "content": "经审核的企业资料",
                "citation": {"source": "企业知识库", "locator": {"companyId": str(authoritative_match.id)}},
            }]
        }

        with patch(
            "app.services.knowledge_client.knowledge_client.search",
            new=AsyncMock(return_value=knowledge_result),
        ), patch(
            "app.services.company_retrieval._get_published_companies_by_ids",
            new=AsyncMock(return_value=[authoritative_match]),
        ):
            results = await rank_similar_companies(None, target, limit=3)

        self.assertEqual([company.name for company in results], ["Knowledge Match"])
