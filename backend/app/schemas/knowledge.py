from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=250)
    top_k: int = Field(default=3, ge=1, le=10)
    score_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized


class KnowledgeChunkResponse(BaseModel):
    id: str
    document_id: str
    document_name: str
    content: str
    score: float
    source: str
    metadata: dict[str, Any]


class KnowledgeSearchResponse(BaseModel):
    query: str
    provider: Literal["mock", "dify"]
    items: list[KnowledgeChunkResponse]
