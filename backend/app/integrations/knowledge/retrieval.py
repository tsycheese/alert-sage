from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: str
    document_id: str
    document_name: str
    content: str
    score: float
    source: str
    metadata: dict[str, object]


class KnowledgeRetriever(Protocol):
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[DocumentChunk]: ...


class MockKnowledgeRetriever:
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[DocumentChunk]:
        del top_k
        score = 0.82
        if score_threshold is not None and score < score_threshold:
            return []
        return [
            DocumentChunk(
                id="mock-cpu-runbook-chunk",
                document_id="mock-cpu-runbook",
                document_name="CPU saturation troubleshooting playbook",
                content=(
                    "Inspect slow database queries and compare the deployment timeline. "
                    f"The offline query was: {query}"
                ),
                score=score,
                source="mock://knowledge/cpu-saturation#database",
                metadata={"provider": "mock"},
            )
        ]
