"""MCP 工具运行时的通用辅助：DB 会话、JSON 序列化。

这些辅助让 MCP tools 能直接从后端服务/模型取数，而无需依赖 FastAPI 的
Request / Depends，从而在独立 MCP 进程里也能复用同一套业务逻辑。
"""
from __future__ import annotations

import enum as _std_enum
import json
import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session


def open_session() -> AsyncSession:
    """创建一个后端使用的异步会话（由调用方负责关闭）。"""
    return async_session()


def json_safe(value):
    """把 ORM/枚举/日期/可选值递归转换为可 JSON 序列化的结构。

    MCP 返回值必须是可序列化的纯数据；绝不直接返回 ORM 实例或 SQLAlchemy 对象。
    """
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (set, list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, _std_enum.Enum):
        return value.value
    if hasattr(value, "value") and not callable(value.value):
        # pydantic / 其他单值枚举
        try:
            return json_safe(value.value)
        except Exception:  # pragma: no cover - 防御性回退
            pass
    if hasattr(value, "__dict__") and not hasattr(value, "__table__"):
        # 普通对象（非 SQLAlchemy ORM）转 dict
        return json_safe({k: v for k, v in vars(value).items() if not k.startswith("_")})
    raise TypeError(f"cannot serialize {type(value)!r}")


def dumps(value) -> str:
    """把任意返回值转成紧凑 JSON 字符串，供 MCP 文本输出。"""
    return json.dumps(json_safe(value), ensure_ascii=False, default=str)
