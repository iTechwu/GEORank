"""GEOrank MCP server — FastMCP 实现。

每个工具是 `georank_<operation>`（DSH 侧调用名带 `mcp__georank__` 前缀）。
工具优先复用后端 services / models，避免复制业务逻辑；结构化工具当前为
确定性 v1 实现（纯函数、可测试），后续可升级为 AI 驱动。

涉及 AI 预算的操作（诊断、拓词、方案问答）使用系统级配额（system AI access），
不依赖登录用户。返回值为纯 JSON 数据（经 json_safe 归一化）。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from app.mcp.auth import current_mcp_auth, require_mcp_ability
from app.mcp.runtime import json_safe, open_session

# ---- models ----
from app.models.company import Company, PublishStatus
from app.models.content import Content, ContentStatus, ContentType
from app.models.diagnostic import DiagnosticReport, DiagnosticStatus
from app.models.expert import ExpertProfile
from app.models.settings import Setting

# ---- services ----
from app.services.ai_client import chat_completion
from app.services.ai_usage import resolve_system_async_ai_access
from app.services.company_ingest import normalize_company_url
from app.services.company_lookup import get_company_by_identifier
from app.services.keyword_expansion import expand_keywords
from app.services.runtime_settings import get_solution_channel_config

mcp = FastMCP("GEOrank")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


# =============================================================================
# 系统能力
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:system:read")
async def georank_system_status() -> dict:
    """返回当前 MCP 调用者的租户和权限范围，用于自动化执行前置检查。"""
    auth = current_mcp_auth()
    return {
        "service": "georank",
        "status": "ok",
        "tenant_id": auth.tenant_id,
        "mcp": {
            "credential_type": auth.credential_type,
            "scope": auth.scope,
            "tenant_mode": "tenant_scoped" if auth.tenant_id else "cross_tenant",
            "cross_tenant": auth.tenant_id is None,
            "abilities": sorted(auth.abilities),
        },
    }


# =============================================================================
# 公司目录
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:companies:read")
async def georank_list_companies(
    query: str | None = None, category: str | None = None,
    page: int = 1, size: int = 20, sort: str = "newest",
) -> dict:
    """按分类/关键词分页列出已发布的 GEO 公司与品牌。

    - query: 名称或简介关键词；category: 分类；sort: newest|geo_score|views|upvotes。
    """
    page = max(1, int(page)); size = min(100, max(1, int(size)))
    stmt = select(Company).where(Company.publish_status == PublishStatus.PUBLISHED)
    if category:
        stmt = stmt.where(Company.category == category)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            Company.name.ilike(like) | Company.short_description.ilike(like)
        )
    if sort == "geo_score":
        stmt = stmt.order_by(Company.geo_score.desc().nullslast())
    elif sort == "views":
        stmt = stmt.order_by(Company.view_count.desc(), Company.created_at.desc())
    elif sort == "upvotes":
        stmt = stmt.order_by(Company.upvotes.desc())
    else:
        stmt = stmt.order_by(Company.created_at.desc())

    async with open_session() as db:
        total = (await db.execute(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )).scalar_one()
        rows = (await db.execute(
            stmt.offset((page - 1) * size).limit(size)
        )).scalars().all()
        items = [{
            "id": str(c.id), "path_key": c.path_key, "name": c.name, "url": c.url,
            "logo_url": c.logo_url, "short_description": c.short_description,
            "category": c.category, "tags": c.tags if isinstance(c.tags, list) else [],
            "geo_score": c.geo_score, "is_geo_certified": c.is_geo_certified,
            "tech_level": c.tech_level, "headquarters": c.headquarters,
            "pipeline_status": _enum(c.pipeline_status),
            "publish_status": _enum(c.publish_status),
            "upvotes": c.upvotes, "view_count": c.view_count,
        } for c in rows]
        return {"items": json_safe(items), "total": total, "page": page, "size": size}


@mcp.tool()
@require_mcp_ability("georank:companies:read")
async def georank_get_company(identifier: str) -> dict:
    """按 path_key / slug / url 获取单个公司的详细资料。"""
    async with open_session() as db:
        company = await get_company_by_identifier(db, identifier)
        if not company:
            return {"found": False, "identifier": identifier}
        return json_safe({
            "found": True, "identifier": identifier,
            "company": {
                "id": str(company.id), "path_key": company.path_key,
                "name": company.name, "url": company.url,
                "logo_url": company.logo_url, "description": company.description,
                "short_description": company.short_description,
                "category": company.category,
                "tags": company.tags if isinstance(company.tags, list) else [],
                "geo_score": company.geo_score,
                "geo_details": company.geo_details,
                "tech_level": company.tech_level, "tech_stack": company.tech_stack,
                "headquarters": company.headquarters, "founded_date": _date(company.founded_date),
                "employee_count": company.employee_count, "funding_stage": company.funding_stage,
                "pipeline_status": _enum(company.pipeline_status),
                "publish_status": _enum(company.publish_status),
                "upvotes": company.upvotes, "view_count": company.view_count,
            },
        })


@mcp.tool()
@require_mcp_ability("georank:companies:read")
async def georank_company_similar(identifier: str, limit: int = 6) -> dict:
    """按同一分类/标签近似推荐同领域的公司。"""
    limit = min(20, max(1, int(limit)))
    async with open_session() as db:
        company = await get_company_by_identifier(db, identifier)
        if not company:
            return {"found": False, "identifier": identifier, "items": []}
        stmt = (
            select(Company)
            .where(Company.publish_status == PublishStatus.PUBLISHED)
            .where(Company.id != company.id)
            .order_by(Company.geo_score.desc().nullslast())
            .limit(limit)
        )
        if company.category:
            stmt = stmt.where(Company.category == company.category)
        rows = (await db.execute(stmt)).scalars().all()
        return json_safe({
            "found": True, "identifier": identifier, "items": [
                {"id": str(c.id), "name": c.name, "url": c.url,
                 "category": c.category, "geo_score": c.geo_score,
                 "short_description": c.short_description} for c in rows
            ],
        })


@mcp.tool()
@require_mcp_ability("georank:companies:read")
async def georank_company_pipeline_status(company_id: str) -> dict:
    """查询某公司的采集/入库流水线状态。"""
    async with open_session() as db:
        company = (
            await db.execute(
                select(Company).where(Company.id == uuid.UUID(company_id))
            )
        ).scalar_one_or_none()
    if not company:
        return {"found": False, "company_id": company_id}
    return json_safe({
        "found": True, "company_id": company_id,
        "pipeline_status": _enum(company.pipeline_status),
        "pipeline_error": company.pipeline_error,
        "publish_status": _enum(company.publish_status),
    })


# =============================================================================
# GEO 诊断
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:diagnostics:write")
async def georank_diagnose_url(
    url: str,
    company_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """提交一个网站 URL 进行 GEO 诊断，返回 report_id（异步，前端轮询状态）。

    仅 URL 必须合法；诊断结果通过 georank_get_diagnostic_report 获取。
    """
    try:
        normalized = normalize_company_url(url)
    except ValueError as exc:
        raise ValueError(f"无效 URL: {exc}") from exc

    tenant_id = _required_tenant_id()
    key = (idempotency_key or "").strip() or None
    if key is not None and _IDEMPOTENCY_KEY.fullmatch(key) is None:
        raise ValueError("idempotency_key format is invalid")
    company_uuid = uuid.UUID(company_id) if company_id else None
    fingerprint = _diagnostic_fingerprint(normalized, company_id)

    async with open_session() as db:
        if key is not None:
            existing = (
                await db.execute(
                    select(DiagnosticReport).where(
                        DiagnosticReport.tenant_id == tenant_id,
                        DiagnosticReport.idempotency_key == key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise ValueError("idempotency_key conflict")
                return _diagnostic_submission(existing, reused=True)

        access = await resolve_system_async_ai_access(
            db=db, module="diagnostics", prompt_text=f"{normalized}\n{company_id or ''}"
        )
        report = DiagnosticReport(
            tenant_id=tenant_id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            url=normalized,
            company_id=company_uuid,
            status=DiagnosticStatus.PENDING,
            ai_reservation_id=access.reservation_id,
        )
        db.add(report)
        await db.commit()
        await db.refresh(report)

        queue_failed = False
        try:
            from app.core.celery_app import celery_app
            celery_app.send_task(
                "app.tasks.crawl.crawl_diagnostic_page",
                args=[str(report.id), normalized, str(access.reservation_id)],
            )
        except Exception:  # pragma: no cover - 取决于 broker 可用性
            queue_failed = True
            report.status = DiagnosticStatus.FAILED
            report.error_message = "queue_dispatch_failed"
            await db.commit()
        return _diagnostic_submission(report, reused=False)


@mcp.tool()
@require_mcp_ability("georank:diagnostics:read")
async def georank_get_diagnostic_report(report_id: str) -> dict:
    """根据 report_id 获取诊断报告（含 Schema/内容/Meta/引用分析与建议）。"""
    tenant_id = _required_tenant_id()
    async with open_session() as db:
        report = (
            await db.execute(
                select(DiagnosticReport).where(
                    DiagnosticReport.id == uuid.UUID(report_id),
                    DiagnosticReport.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
    if not report:
        return {"found": False, "report_id": report_id}
    return json_safe({
        "found": True, "report_id": report_id, "url": report.url,
        "company_id": str(report.company_id) if report.company_id else None,
        "status": _enum(report.status), "overall_score": report.overall_score,
        "schema_analysis": report.schema_analysis, "content_analysis": report.content_analysis,
        "meta_analysis": report.meta_analysis, "citation_analysis": report.citation_analysis,
        "recommendations": report.recommendations, "error_message": report.error_message,
        "created_at": _date(report.created_at),
    })


@mcp.tool()
@require_mcp_ability("georank:diagnostics:read")
async def georank_diagnostic_history(limit: int = 20) -> dict:
    """列出当前租户最近创建的诊断报告。"""
    limit = min(100, max(1, int(limit)))
    tenant_id = _required_tenant_id()
    async with open_session() as db:
        rows = (await db.execute(
            select(DiagnosticReport)
            .where(DiagnosticReport.tenant_id == tenant_id)
            .order_by(DiagnosticReport.created_at.desc())
            .limit(limit)
        )).scalars().all()
    return json_safe([{
        "report_id": str(r.id), "url": r.url, "status": _enum(r.status),
        "overall_score": r.overall_score, "created_at": _date(r.created_at),
    } for r in rows])


# =============================================================================
# 关键词拓词
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:keywords:expand")
async def georank_expand_keywords(seeds: list[str]) -> dict:
    """从业务词扩展为问题词/场景词/商业意图词/推荐型关键词资产。"""
    if not seeds:
        raise ValueError("请至少提供一个业务词种子")
    result = await expand_keywords(seeds)
    return json_safe(result)


# =============================================================================
# GEO 方案 / 问答
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:solutions:read")
async def georank_solution_channels() -> dict:
    """列出可用的 GEO 问答频道及示例问题。"""
    config = await get_solution_channel_config()
    channels = [{
        "key": ch.get("key"), "name": ch.get("name"), "description": ch.get("description"),
        "icon": ch.get("icon") or "forum", "enabled": ch.get("enabled", True),
        "sample_questions": ch.get("sample_questions") or [],
    } for ch in config.get("channels", []) if ch.get("enabled", True)]
    default_key = config.get("default_channel_key")
    if default_key not in {ch["key"] for ch in channels} and channels:
        default_key = channels[0]["key"]
    return {"default_channel_key": default_key, "channels": json_safe(channels)}


@mcp.tool()
@require_mcp_ability("georank:solutions:chat")
async def georank_solution_chat(question: str, channel: str | None = None) -> dict:
    """就某个 GEO 频道语境回答问题（系统级 AI 调用，无用户上下文）。"""
    if not question.strip():
        raise ValueError("question 不能为空")
    system = (
        "你是 GEOrank 的生成式引擎优化（GEO）顾问，用中文给出结构化、可执行的回答。"
        "围绕 AI 搜索（ChatGPT/Perplexity/Gemini/Claude）的可见性、内容结构、Schema、"
        "引用信号与品牌建设展开。"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    try:
        answer = await chat_completion(messages, temperature=0.3, max_tokens=4096)
    except Exception as exc:  # pragma: no cover - 取决于模型服务配置
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "answer": answer, "channel": channel}


# =============================================================================
# 专家
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:experts:read")
async def georank_list_experts(category: str | None = None) -> list:
    """列出已发布的 GEO/AI 专家资料。category 可选：strategy/..."""
    stmt = select(ExpertProfile).where(ExpertProfile.is_published.is_(True))
    if category:
        stmt = stmt.where(ExpertProfile.category == category)
    stmt = stmt.order_by(ExpertProfile.sort_order.asc())
    async with open_session() as db:
        rows = (await db.execute(stmt)).scalars().all()
    return json_safe([{
        "slug": e.slug, "display_name": e.display_name, "title": e.title,
        "category": e.category, "specialty_label": e.specialty_label,
        "summary": e.summary, "expertise": e.expertise, "keywords": e.keywords,
        "is_featured": e.is_featured,
    } for e in rows])


@mcp.tool()
@require_mcp_ability("georank:experts:read")
async def georank_get_expert(slug: str) -> dict:
    """按 slug 获取专家详情。"""
    async with open_session() as db:
        expert = (
            await db.execute(
                select(ExpertProfile).where(ExpertProfile.slug == slug,
                                            ExpertProfile.is_published.is_(True))
            )
        ).scalar_one_or_none()
    if not expert:
        return {"found": False, "slug": slug}
    return json_safe({
        "found": True, "slug": slug, "display_name": expert.display_name,
        "title": expert.title, "category": expert.category,
        "specialty_label": expert.specialty_label, "summary": expert.summary,
        "expertise": expert.expertise, "consultation": expert.consultation,
        "keywords": expert.keywords, "is_featured": expert.is_featured,
    })


# =============================================================================
# 内容（教程）
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:content:read")
async def georank_list_content(content_type: str | None = None, limit: int = 20) -> list:
    """列出已发布的内容/教程。content_type 可选（如 tutorial）。"""
    limit = min(100, max(1, int(limit)))
    stmt = select(Content).where(Content.status == ContentStatus.PUBLISHED)
    if content_type:
        try:
            ctype = ContentType(content_type)
            stmt = stmt.where(Content.content_type == ctype)
        except ValueError:
            pass
    stmt = stmt.order_by(Content.created_at.desc()).limit(limit)
    async with open_session() as db:
        rows = (await db.execute(stmt)).scalars().all()
    return json_safe([{
        "slug": c.slug, "path_key": c.path_key, "title": c.title,
        "content_type": _enum(c.content_type), "status": _enum(c.status),
        "cover_image": c.cover_image, "reading_time_minutes": c.reading_time_minutes,
        "tags": c.tags if isinstance(c.tags, list) else [],
        "created_at": _date(c.created_at),
    } for c in rows])


@mcp.tool()
@require_mcp_ability("georank:content:read")
async def georank_get_content(slug: str) -> dict:
    """按 slug 获取已发布内容/教程正文。"""
    async with open_session() as db:
        article = (
            await db.execute(
                select(Content).where(Content.slug == slug,
                                      Content.status == ContentStatus.PUBLISHED)
            )
        ).scalar_one_or_none()
    if not article:
        return {"found": False, "slug": slug}
    return json_safe({
        "found": True, "slug": slug, "title": article.title,
        "content_type": _enum(article.content_type),
        "markdown_body": article.markdown_body,
        "reading_time_minutes": article.reading_time_minutes,
    })


# =============================================================================
# 站点设置 / 用量
# =============================================================================
@mcp.tool()
@require_mcp_ability("georank:settings:read")
async def georank_get_public_settings() -> dict:
    """读取公开的站点配置（站点名、描述、默认语言等）。"""
    async with open_session() as db:
        rows = (await db.execute(
            select(Setting).where(Setting.category == "basic")
        )).scalars().all()
    public = {}
    for s in rows:
        if isinstance(s.value, dict) and s.value.get("is_public"):
            public[s.key] = s.value.get("value")
    return json_safe(public)


# =============================================================================
# 结构化工具（确定性 v1，可用 AI 升级）
# =============================================================================
def _brief_parts(brief: str) -> dict:
    """从一段简短描述中尽量提取 name/url/description。"""
    import re
    name = ""
    url = ""
    desc = brief.strip()
    m = re.search(r"https?://[^\s，,]+", brief)
    if m:
        url = m.group(0)
    # 第一行或第一句作为名称
    first_line = desc.splitlines()[0].strip() if desc.splitlines() else desc
    first_line = re.split(r"[，,。;；]", first_line)[0].strip()
    if first_line and len(first_line) <= 60:
        name = first_line
    return {"name": name, "url": url, "description": desc}


@mcp.tool()
@require_mcp_ability("georank:content:generate")
async def georank_generate_jsonld(brief: str) -> dict:
    """由一段简短描述生成 Schema.org JSON-LD（Organization + WebSite）。"""
    p = _brief_parts(brief)
    org = {"@type": "Organization"}
    if p["name"]:
        org["name"] = p["name"]
    if p["url"]:
        org["url"] = p["url"]
    if p["description"]:
        org["description"] = p["description"]
    site = {"@type": "WebSite"}
    if p["url"]:
        site["url"] = p["url"]
    if p["name"]:
        site["name"] = p["name"]
    graph = [org, site]
    return json_safe({
        "@context": "https://schema.org",
        "@graph": graph,
        "note": "确定性 v1 生成；可后续接入 AI 以补齐字段。",
    })


@mcp.tool()
@require_mcp_ability("georank:content:generate")
async def georank_generate_llms_txt(brief: str) -> str:
    """由一段简短描述生成 llms.txt 草稿（站点摘要 + 重要页面 + AI Reading Notes）。"""
    p = _brief_parts(brief)
    lines = [f"# {p['name'] or 'GEOrank site'}"]
    if p["url"]:
        lines.append(f"> {p['url']}")
    lines.append("")
    lines.append("## 站点摘要")
    lines.append(p["description"] or "AI 搜索可见性改善平台。")
    lines.append("")
    lines.append("## 重要页面")
    lines.append(f"- [首页]({p['url'] or '#'}): 站点入口")
    if p["url"]:
        lines.append(f"- [AI Reading Notes]({p['url']}/about): 面向 AI 的系统介绍")
    lines.append("")
    lines.append("## AI Reading Notes")
    lines.append("- 提供公司/网站在生成式引擎中的可见性诊断与优化建议。")
    lines.append("- 支持 Q&A、30/60/90 天行动方案、关键词与结构化内容资产沉淀。")
    return "\n".join(lines)


@mcp.tool()
@require_mcp_ability("georank:content:generate")
async def georank_generate_title(brief: str) -> dict:
    """为品牌/页面生成一个聚焦 AI 搜索可见性的 GEO 标题。"""
    p = _brief_parts(brief)
    base = p["name"] or p["description"] or "GEO 品牌"
    title = f"{base} — 提升 AI 搜索可见性（GEO）"
    return json_safe({"title": title, "alternatives": [
        f"{base} 的生成式引擎优化指南",
        f"让 {base} 被 ChatGPT / Perplexity 推荐",
        f"{base} · GEO 可见性诊断与行动方案",
    ]})


@mcp.tool()
@require_mcp_ability("georank:content:generate")
async def georank_generate_knowledge_base(brief: str) -> dict:
    """生成一份知识库草稿大纲（FAQ / 术语 / 资源 / 工具）。"""
    p = _brief_parts(brief)
    base = p["name"] or "GEOrank"
    outline = {
        "title": f"{base} GEO 知识库",
        "intro": p["description"] or "",
        "sections": [
            {"heading": "核心概念", "items": ["什么是生成式引擎优化（GEO）", "AI 搜索与品牌可见性"]},
            {"heading": "诊断", "items": ["Schema / 页面结构 / Meta", "引用信号与内容可读性"]},
            {"heading": "行动方案", "items": ["30/60/90 天优化计划", "关键词与选题资产"]},
            {"heading": "工具", "items": ["JSON-LD 生成器", "llms.txt 生成器", "AI 友好度评分"]},
        ],
    }
    return json_safe(outline)


@mcp.tool()
@require_mcp_ability("georank:content:generate")
async def georank_score_ai_friendliness(brief: str) -> dict:
    """对一段品牌/站点描述做 AI 友好度启发式评分（0-100），并给出改进点。"""
    p = _brief_parts(brief)
    score = 40
    reasons = []
    if p["url"]:
        score += 15; reasons.append("检测到站点 URL")
    if p["name"]:
        score += 10; reasons.append("检测到品牌名称")
    if len(p["description"]) > 40:
        score += 15; reasons.append("描述较充分")
    if any(k in p["description"].lower() for k in ("ai", "schema", "json-ld", "llms", "geo", "搜索可见性", "人工智能")):
        score += 10; reasons.append("内容命中 AI 相关关键词")
    score = min(100, score)
    suggestions = [
        "补充 Organization/WebSite 的 Schema.org JSON-LD 标记",
        "在站点根目录发布 llms.txt 供 AI 系统阅读",
        "为关键页面增加清晰的 Meta 与结构化 FAQ",
        "提供可被引用的事实与作者署名以增强引用信号",
    ]
    return json_safe({"score": score, "reasons": reasons, "suggestions": suggestions})


# =============================================================================
# 序列化辅助
# =============================================================================
def _enum(value):
    """枚举值取 .value，其它原样。"""
    return getattr(value, "value", value)


def _date(value):
    """日期/时间对象转 ISO 字符串，否则原样。"""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _required_tenant_id() -> str:
    tenant_id = current_mcp_auth().tenant_id
    if not tenant_id:
        raise PermissionError("tenant-scoped MCP credential required")
    return tenant_id


def _diagnostic_fingerprint(url: str, company_id: str | None) -> str:
    payload = json.dumps(
        {"company_id": company_id, "url": url},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _diagnostic_submission(report: DiagnosticReport, *, reused: bool) -> dict:
    return {
        "report_id": str(report.id),
        "status": _enum(report.status),
        "url": report.url,
        "reused": reused,
    }
