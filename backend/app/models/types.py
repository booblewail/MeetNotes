from __future__ import annotations

from typing import Any
from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


JSONVariant = JSON().with_variant(JSONB(), "postgresql")


class EmbeddingVector(TypeDecorator[list[float] | None]):
    """Cross-dialect 768-dim vector column: pgvector Vector on Postgres, JSON on SQLite."""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = 768) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(
        self, value: list[float] | None, dialect: Dialect
    ) -> list[float] | None:
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(
        self, value: Any, dialect: Dialect
    ) -> list[float] | None:
        if value is None:
            return None
        return [float(x) for x in value]
