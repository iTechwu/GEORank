"""Object storage adapter backed exclusively by knowledge.dofe.ai."""
from typing import Optional
from app.services.knowledge_client import KnowledgeClientError, knowledge_client


class StorageService:
    """Preserves the pipeline interface while keeping storage credentials in Knowledge."""

    def put(self, key: str, data: bytes, content_type: str = "text/html") -> bool:
        """Store an object through Knowledge; failures remain explicit to the pipeline."""
        try:
            knowledge_client.put_object(key, data, content_type)
            return True
        except KnowledgeClientError:
            return False

    def get(self, key: str) -> Optional[bytes]:
        """Read an object through Knowledge."""
        try:
            return knowledge_client.get_object(key)
        except KnowledgeClientError:
            return None

    def delete(self, key: str):
        """Delete an object through Knowledge."""
        try:
            knowledge_client.delete_object(key)
        except KnowledgeClientError:
            pass


# 全局单例
storage = StorageService()
