"""Read-only deployment check for the canonical Knowledge infrastructure."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.config import settings
from app.services.knowledge_client import KnowledgeClientError, knowledge_client


async def check(query: str) -> dict[str, object]:
    normalized = query.strip()
    if not normalized:
        raise KnowledgeClientError("knowledge check query must not be empty")
    if not knowledge_client.configured:
        raise KnowledgeClientError("knowledge deployment configuration is incomplete")

    result = await knowledge_client.search(normalized, top_k=1, include_memories=False)
    return {
        "status": "ok",
        "source_system": "georank",
        "tenant": settings.KNOWLEDGE_TENANT_SLUG,
        "hit_count": len(result.get("list", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="knowledge infrastructure readiness")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(check(args.query)), ensure_ascii=False, sort_keys=True))
        return 0
    except (KnowledgeClientError, OSError) as error:
        print(f"knowledge check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
