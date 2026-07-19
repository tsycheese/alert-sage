from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid5


@dataclass(frozen=True, slots=True)
class CaseDocument:
    case_id: UUID
    title: str
    symptom: str
    root_cause: str
    resolution: str
    evidence: list[dict[str, object]]
    tags: list[str]


class CasePublisher(Protocol):
    async def publish(self, document: CaseDocument, *, idempotency_key: str) -> str: ...


class MockCasePublisher:
    async def publish(self, document: CaseDocument, *, idempotency_key: str) -> str:
        return f"mock-doc-{uuid5(document.case_id, idempotency_key)}"
