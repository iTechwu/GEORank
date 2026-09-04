"""Resolve company recommendations exclusively from Knowledge citations."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.company import Company, PublishStatus
from app.services.knowledge_client import KnowledgeClientError, knowledge_client


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify(item) for item in value if item)
    if isinstance(value, dict):
        return " ".join(_stringify(v) for v in value.values() if v)
    return str(value)


async def _get_published_companies_by_ids(db, company_ids: list[str]) -> list[Company]:
    valid_ids: list[uuid.UUID] = []
    for company_id in company_ids:
        try:
            valid_ids.append(uuid.UUID(company_id))
        except (TypeError, ValueError, AttributeError):
            continue
    if not valid_ids:
        return []
    result = await db.execute(
        select(Company).where(
            Company.id.in_(valid_ids),
            Company.publish_status == PublishStatus.PUBLISHED,
        )
    )
    return result.scalars().all()


async def rank_similar_companies(db, company: Company, *, limit: int = 3) -> list[Company]:
    """仅返回 Knowledge 授权检索结果；没有权威结果时不读取本地公司字段回退。"""
    query = " ".join(
        item for item in [
            _stringify(company.name),
            _stringify(company.category),
            _stringify(company.tags),
            _stringify(company.tech_stack),
        ] if item
    )
    try:
        result = await knowledge_client.search(query, top_k=max(1, min(20, limit * 4)))
    except KnowledgeClientError:
        return []
    company_ids: list[str] = []
    for hit in result.get("list", []):
        citation = hit.get("citation") if isinstance(hit, dict) else None
        locator = citation.get("locator") if isinstance(citation, dict) else None
        if not isinstance(locator, dict):
            continue
        candidate = next(
            (locator.get(key) for key in ("companyId", "company_id", "sourceId", "externalEntityId") if locator.get(key)),
            None,
        )
        try:
            normalized = str(uuid.UUID(str(candidate)))
        except (TypeError, ValueError, AttributeError):
            continue
        if normalized != str(company.id) and normalized not in company_ids:
            company_ids.append(normalized)
        if len(company_ids) >= limit:
            break
    if not company_ids:
        return []
    companies = await _get_published_companies_by_ids(db, company_ids)
    by_id = {str(item.id): item for item in companies}
    return [by_id[company_id] for company_id in company_ids if company_id in by_id][:limit]
