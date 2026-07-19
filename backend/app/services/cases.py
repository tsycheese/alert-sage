import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.knowledge.cases import CaseDocument, CasePublisher
from app.models.case import Case
from app.models.diagnosis import DiagnosisReport
from app.models.enums import KnowledgeSyncStatus, WorkflowEventType
from app.models.workflow import WorkflowRun
from app.services.workflow_events import append_workflow_event


class CaseNotFoundError(Exception):
    pass


class CaseStateConflictError(Exception):
    pass


class CaseSyncExecutionError(Exception):
    def __init__(self, case_id: UUID, cause: Exception) -> None:
        super().__init__(f"case {case_id} sync failed: {cause}")
        self.case_id = case_id
        self.cause = cause


@dataclass(frozen=True, slots=True)
class CaseSyncResult:
    case_id: UUID
    workflow_run_id: UUID
    status: KnowledgeSyncStatus
    external_document_id: str | None


class CaseQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_alert(self, alert_id: UUID) -> Case:
        case = await self.session.scalar(
            select(Case)
            .join(DiagnosisReport, Case.diagnosis_report_id == DiagnosisReport.id)
            .join(WorkflowRun, DiagnosisReport.workflow_run_id == WorkflowRun.id)
            .where(WorkflowRun.alert_id == alert_id)
            .order_by(Case.created_at.desc())
            .limit(1)
        )
        if case is None:
            raise CaseNotFoundError
        return case


class CaseSyncService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: CasePublisher,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.session_factory = session_factory
        self.publisher = publisher
        self.timeout_seconds = timeout_seconds

    async def prepare_retry(self, case_id: UUID) -> bool:
        async with self.session_factory() as session:
            case = await self._locked_case(session, case_id)
            if case.knowledge_sync_status == KnowledgeSyncStatus.SYNCED:
                return False
            if case.knowledge_sync_status == KnowledgeSyncStatus.SYNCING:
                raise CaseStateConflictError("case sync is already running")
            case.knowledge_sync_status = KnowledgeSyncStatus.PENDING
            case.sync_error_code = None
            case.sync_error_message = None
            await session.commit()
            return True

    async def execute(self, case_id: UUID) -> CaseSyncResult:
        document, workflow_run_id, attempt = await self._mark_syncing(case_id)
        if document is None:
            return await self.get_result(case_id)
        try:
            external_document_id = await asyncio.wait_for(
                self.publisher.publish(
                    document,
                    idempotency_key=f"case-sync:{case_id}",
                ),
                timeout=self.timeout_seconds,
            )
            external_document_id = external_document_id.strip()
            if not external_document_id:
                raise ValueError("case publisher returned an empty document id")
        except Exception as exc:
            await self._mark_failed(case_id, workflow_run_id, attempt, exc)
            raise CaseSyncExecutionError(case_id, exc) from exc
        await self._mark_succeeded(
            case_id,
            workflow_run_id,
            attempt,
            external_document_id,
        )
        return await self.get_result(case_id)

    async def get_result(self, case_id: UUID) -> CaseSyncResult:
        async with self.session_factory() as session:
            case = await session.get(Case, case_id)
            if case is None:
                raise CaseNotFoundError
            workflow_run_id = await self._workflow_run_id(session, case.diagnosis_report_id)
            return CaseSyncResult(
                case_id=case.id,
                workflow_run_id=workflow_run_id,
                status=case.knowledge_sync_status,
                external_document_id=case.external_document_id,
            )

    async def _mark_syncing(self, case_id: UUID) -> tuple[CaseDocument | None, UUID, int]:
        async with self.session_factory() as session:
            case = await self._locked_case(session, case_id)
            workflow_run_id = await self._workflow_run_id(session, case.diagnosis_report_id)
            if case.knowledge_sync_status == KnowledgeSyncStatus.SYNCED:
                return None, workflow_run_id, case.knowledge_sync_attempt
            case.knowledge_sync_status = KnowledgeSyncStatus.SYNCING
            case.knowledge_sync_attempt += 1
            case.sync_error_code = None
            case.sync_error_message = None
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise CaseStateConflictError("case workflow run does not exist")
            await append_workflow_event(
                session,
                run,
                idempotency_key=f"case:sync-started:attempt-{case.knowledge_sync_attempt}",
                event_type=WorkflowEventType.CASE_SYNC_STARTED,
                status=KnowledgeSyncStatus.SYNCING,
                payload={"case_id": str(case.id), "attempt": case.knowledge_sync_attempt},
            )
            document = CaseDocument(
                case_id=case.id,
                title=case.title,
                symptom=case.symptom,
                root_cause=case.root_cause,
                resolution=case.resolution,
                evidence=case.evidence,
                tags=case.tags,
            )
            attempt = case.knowledge_sync_attempt
            await session.commit()
            return document, workflow_run_id, attempt

    async def _mark_succeeded(
        self,
        case_id: UUID,
        workflow_run_id: UUID,
        attempt: int,
        external_document_id: str,
    ) -> None:
        async with self.session_factory() as session:
            case = await self._locked_case(session, case_id)
            case.knowledge_sync_status = KnowledgeSyncStatus.SYNCED
            case.external_document_id = external_document_id
            case.sync_error_code = None
            case.sync_error_message = None
            case.synced_at = datetime.now(UTC)
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise CaseStateConflictError("case workflow run does not exist")
            await append_workflow_event(
                session,
                run,
                idempotency_key=f"case:sync-succeeded:attempt-{attempt}",
                event_type=WorkflowEventType.CASE_SYNC_SUCCEEDED,
                status=KnowledgeSyncStatus.SYNCED,
                payload={
                    "case_id": str(case.id),
                    "attempt": attempt,
                    "external_document_id": external_document_id,
                },
            )
            await session.commit()

    async def _mark_failed(
        self,
        case_id: UUID,
        workflow_run_id: UUID,
        attempt: int,
        error: Exception,
    ) -> None:
        async with self.session_factory() as session:
            case = await self._locked_case(session, case_id)
            case.knowledge_sync_status = KnowledgeSyncStatus.FAILED
            case.sync_error_code = type(error).__name__[:64]
            case.sync_error_message = str(error)[:2000]
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise CaseStateConflictError("case workflow run does not exist")
            await append_workflow_event(
                session,
                run,
                idempotency_key=f"case:sync-failed:attempt-{attempt}",
                event_type=WorkflowEventType.CASE_SYNC_FAILED,
                status=KnowledgeSyncStatus.FAILED,
                payload={
                    "case_id": str(case.id),
                    "attempt": attempt,
                    "error_code": case.sync_error_code,
                },
            )
            await session.commit()

    @staticmethod
    async def _locked_case(session: AsyncSession, case_id: UUID) -> Case:
        case = await session.scalar(select(Case).where(Case.id == case_id).with_for_update())
        if case is None:
            raise CaseNotFoundError
        return case

    @staticmethod
    async def _workflow_run_id(session: AsyncSession, report_id: UUID) -> UUID:
        run_id = await session.scalar(
            select(DiagnosisReport.workflow_run_id).where(DiagnosisReport.id == report_id)
        )
        if run_id is None:
            raise CaseStateConflictError("case diagnosis report does not exist")
        return run_id
