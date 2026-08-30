"""Company graph adapter backed exclusively by knowledge.dofe.ai."""
from app.services.knowledge_client import knowledge_client

_ALLOWED_ENTITY_LABELS = frozenset({"Person", "Product", "Technology", "Company"})
_ALLOWED_RELATIONSHIP_TYPES = frozenset(
    {"FOUNDED_BY", "HAS_PRODUCT", "USES_TECH", "COMPETES_WITH"}
)


def _validate_graph_payload(entities: list[dict], relations: list[dict]) -> None:
    entity_names: set[str] = set()
    for entity in entities:
        entity_type = entity.get("type")
        if entity_type not in _ALLOWED_ENTITY_LABELS:
            raise ValueError(f"不支持的实体类型：{entity_type}")
        if not str(entity.get("name") or "").strip():
            raise ValueError("图谱实体名称不能为空")
        entity_name = str(entity["name"]).strip()
        if entity_name in entity_names:
            raise ValueError(f"图谱实体名称重复：{entity_name}")
        entity_names.add(entity_name)
    for relation in relations:
        relation_type = relation.get("type")
        if relation_type not in _ALLOWED_RELATIONSHIP_TYPES:
            raise ValueError(f"不支持的关系类型：{relation_type}")
        if not str(relation.get("from") or "").strip() or not str(relation.get("to") or "").strip():
            raise ValueError("图谱关系端点不能为空")


async def upsert_company_graph(
    company_id: str,
    properties: dict,
    entities: list[dict],
    relations: list[dict],
) -> dict:
    """Replace the complete company graph through the Knowledge authority."""
    _validate_graph_payload(entities, relations)
    nodes = [
        {
            "name": str(entity["name"]).strip(),
            "type": entity["type"],
            "properties": entity.get("props", {}),
        }
        for entity in entities
    ]
    return await knowledge_client.put_graph_snapshot(company_id, properties, nodes, relations)


async def get_company_graph(company_id: str) -> dict:
    """Read the authoritative PostgreSQL-backed snapshot through Knowledge."""
    snapshot = await knowledge_client.get_graph_snapshot(company_id)
    return {
        "nodes": snapshot.get("nodes", []),
        "relationships": snapshot.get("relationships", []),
    }
