from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.integrations.knowledge.cases import CaseDocument, CasePublisher
from app.integrations.knowledge.retrieval import DocumentChunk, KnowledgeRetriever


class KnowledgeProviderError(Exception):
    pass


class DifyAuthenticationError(KnowledgeProviderError):
    pass


class DifyRequestError(KnowledgeProviderError):
    pass


class DifyResponseError(KnowledgeProviderError):
    pass


class DifyIndexingError(KnowledgeProviderError):
    pass


@dataclass(frozen=True, slots=True)
class DifyKnowledgeConfig:
    base_url: str
    api_key: str
    dataset_id: str
    http_timeout_seconds: float = 15.0
    poll_interval_seconds: float = 2.0
    max_retries: int = 2
    refresh_completed_documents: bool = False


class _DifyModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _DifyDocument(_DifyModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    indexing_status: str = "waiting"
    error: str | None = None


class _DifyCreateDocumentResponse(_DifyModel):
    document: _DifyDocument
    batch: str = Field(min_length=1)


class _DifyDocumentListResponse(_DifyModel):
    data: list[_DifyDocument]


class _DifyRetrievedDocument(_DifyModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    doc_metadata: Any = None


class _DifyRetrievedSegment(_DifyModel):
    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    content: str
    document: _DifyRetrievedDocument


class _DifyRetrievalRecord(_DifyModel):
    segment: _DifyRetrievedSegment
    score: float = Field(ge=0)


class _DifyRetrievalResponse(_DifyModel):
    records: list[_DifyRetrievalRecord]


class DifyKnowledgeAdapter(CasePublisher, KnowledgeRetriever):
    _terminal_failures = {"error", "paused"}

    def __init__(
        self,
        config: DifyKnowledgeConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    async def publish(self, document: CaseDocument, *, idempotency_key: str) -> str:
        document_name = f"alert-sage-case-{document.case_id}.md"
        document_text = self._render_case(document, idempotency_key=idempotency_key)
        async with self._client() as client:
            existing = await self._find_document(client, document_name)
            if existing is not None:
                if existing.indexing_status == "completed":
                    if not self.config.refresh_completed_documents:
                        return existing.id
                    await self._update_document(
                        client,
                        existing.id,
                        document_name,
                        document_text,
                    )
                    return await self._wait_until_indexed(client, existing.id)
                if existing.indexing_status in self._terminal_failures:
                    await self._update_document(client, existing.id, document_name, document_text)
                return await self._wait_until_indexed(client, existing.id)
            created = await self._create_document(client, document_name, document_text)
            return await self._wait_until_indexed(client, created.document.id)

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[DocumentChunk]:
        retrieval_model: dict[str, object] = {
            "search_method": "semantic_search",
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": score_threshold is not None,
            "score_threshold": score_threshold,
        }
        async with self._client() as client:
            payload = await self._request_json(
                client,
                "POST",
                f"/datasets/{self.config.dataset_id}/retrieve",
                json={"query": query, "retrieval_model": retrieval_model},
            )
        response = self._validate(_DifyRetrievalResponse, payload, "retrieval")
        return [
            DocumentChunk(
                id=record.segment.id,
                document_id=record.segment.document_id,
                document_name=record.segment.document.name,
                content=record.segment.content,
                score=record.score,
                source=(
                    f"dify://datasets/{self.config.dataset_id}/documents/"
                    f"{record.segment.document_id}/segments/{record.segment.id}"
                ),
                metadata=self._normalize_metadata(record.segment.document.doc_metadata),
            )
            for record in response.records
            if score_threshold is None or record.score >= score_threshold
        ][:top_k]

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.base_url,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.config.http_timeout_seconds,
            transport=self.transport,
        )

    async def _find_document(
        self,
        client: httpx.AsyncClient,
        document_name: str,
    ) -> _DifyDocument | None:
        payload = await self._request_json(
            client,
            "GET",
            f"/datasets/{self.config.dataset_id}/documents",
            params={"page": 1, "limit": 20, "keyword": document_name},
        )
        response = self._validate(_DifyDocumentListResponse, payload, "document list")
        return next((item for item in response.data if item.name == document_name), None)

    async def _create_document(
        self,
        client: httpx.AsyncClient,
        name: str,
        text: str,
    ) -> _DifyCreateDocumentResponse:
        payload = await self._request_json(
            client,
            "POST",
            f"/datasets/{self.config.dataset_id}/document/create-by-text",
            json=self._document_payload(name, text),
        )
        return self._validate(_DifyCreateDocumentResponse, payload, "document creation")

    async def _update_document(
        self,
        client: httpx.AsyncClient,
        document_id: str,
        name: str,
        text: str,
    ) -> None:
        payload = await self._request_json(
            client,
            "POST",
            f"/datasets/{self.config.dataset_id}/documents/{document_id}/update-by-text",
            json=self._document_payload(name, text),
        )
        self._validate(_DifyCreateDocumentResponse, payload, "document update")

    async def _wait_until_indexed(
        self,
        client: httpx.AsyncClient,
        document_id: str,
    ) -> str:
        while True:
            payload = await self._request_json(
                client,
                "GET",
                f"/datasets/{self.config.dataset_id}/documents/{document_id}",
            )
            document = self._validate(_DifyDocument, payload, "document status")
            if document.indexing_status == "completed":
                return document.id
            if document.indexing_status in self._terminal_failures:
                raise DifyIndexingError(
                    f"Dify document indexing ended with status {document.indexing_status}"
                )
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: object,
    ) -> object:
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self.config.max_retries:
                    raise DifyRequestError("Dify request failed after retries") from exc
                await self._retry_delay(attempt)
                continue
            if response.status_code in {401, 403}:
                raise DifyAuthenticationError("Dify rejected the knowledge API credentials")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.config.max_retries:
                    await self._retry_delay(attempt)
                    continue
            if response.is_error:
                raise DifyRequestError(f"Dify request failed with HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                raise DifyResponseError("Dify returned a non-JSON response") from exc
        raise DifyRequestError("Dify request failed after retries")

    async def _retry_delay(self, attempt: int) -> None:
        await asyncio.sleep(min(0.25 * (2**attempt), self.config.poll_interval_seconds))

    @staticmethod
    def _document_payload(name: str, text: str) -> dict[str, object]:
        return {
            "name": name,
            "text": text,
            "indexing_technique": "high_quality",
            "doc_form": "text_model",
            "doc_language": "Chinese",
            "process_rule": {"mode": "automatic"},
        }

    @staticmethod
    def _render_case(document: CaseDocument, *, idempotency_key: str) -> str:
        return "\n\n".join(
            [
                f"# {document.title}",
                f"- Case ID: `{document.case_id}`\n- Idempotency Key: `{idempotency_key}`",
                f"## 故障现象\n\n{document.symptom}",
                f"## 根因\n\n{document.root_cause}",
                f"## 处置方案\n\n{document.resolution}",
                f"## 标签\n\n{', '.join(document.tags)}",
                "## 证据\n\n```json\n"
                f"{json.dumps(document.evidence, ensure_ascii=False, indent=2)}\n```",
            ]
        )

    @staticmethod
    def _normalize_metadata(value: Any) -> dict[str, object]:
        if isinstance(value, dict):
            return {str(key): item for key, item in value.items()}
        if isinstance(value, list):
            return {"fields": value}
        return {}

    @staticmethod
    def _validate[T: BaseModel](model: type[T], payload: object, operation: str) -> T:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise DifyResponseError(f"Dify {operation} response failed schema validation") from exc
