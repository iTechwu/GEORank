"""Models-key authenticated, public-data-only Yootun API routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from app.mcp.auth import McpAuthConfig, _gateway_context
from app.mcp.runtime import json_safe, open_session
from app.models.company import Company, PublishStatus

router = APIRouter()


@router.get("/georank/overview")
async def georank_overview(request: Request, response: Response) -> dict:
    headers = {key.lower(): value for key, value in request.headers.items()}
    context = _gateway_context(headers, McpAuthConfig.from_env())
    if context is None or not context.allows("georank:companies:read"):
        raise HTTPException(status_code=401, detail="A valid tenant-scoped Models API key is required")

    published = Company.publish_status == PublishStatus.PUBLISHED
    async with open_session() as db:
        total = (await db.execute(select(func.count(Company.id)).where(published))).scalar_one()
        scored = (await db.execute(
            select(func.count(Company.id)).where(published, Company.geo_score.isnot(None))
        )).scalar_one()
        average = (await db.execute(
            select(func.avg(Company.geo_score)).where(published, Company.geo_score.isnot(None))
        )).scalar_one()
        rows = (await db.execute(
            select(Company.category, func.count(Company.id))
            .where(published)
            .group_by(Company.category)
            .order_by(func.count(Company.id).desc())
            .limit(20)
        )).all()
        distribution = {}
        for name, lower, upper in (
            ("excellent", 80, None), ("good", 60, 80), ("average", 40, 60), ("poor", None, 40)
        ):
            conditions = [published]
            if lower is not None:
                conditions.append(Company.geo_score >= lower)
            if upper is not None:
                conditions.append(Company.geo_score < upper)
            distribution[name] = (await db.execute(
                select(func.count(Company.id)).where(*conditions)
            )).scalar_one()
        recent = (await db.execute(
            select(Company).where(published).order_by(Company.created_at.desc()).limit(10)
        )).scalars().all()

    response.headers["Cache-Control"] = "no-store"
    return {
        "data": json_safe({
            "scope": "public_directory",
            "totals": {"publishedCompanies": total, "scoredCompanies": scored},
            "averageGeoScore": round(float(average), 1) if average is not None else None,
            "scoreDistribution": distribution,
            "categories": [{"name": category or "uncategorized", "count": count} for category, count in rows],
            "recentCompanies": [
                {"id": str(company.id), "pathKey": company.path_key, "name": company.name,
                 "url": company.url, "category": company.category, "geoScore": company.geo_score,
                 "isGeoCertified": company.is_geo_certified}
                for company in recent
            ],
        }),
        "meta": {"source": "georank", "requestId": request.headers.get("x-request-id", "")},
    }
