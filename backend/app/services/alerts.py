import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.enums import AlertSeverity, AlertStatus
from app.repositories.alerts import AlertRepository
from app.schemas.alert import AlertCreate


class AlertNotFoundError(Exception):
    pass


class AlertIdempotencyConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CreateAlertResult:
    alert: Alert
    created: bool


@dataclass(frozen=True, slots=True)
class AlertPage:
    items: list[Alert]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        if self.total == 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


class AlertService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = AlertRepository(session)

    async def create(self, command: AlertCreate) -> CreateAlertResult:
        payload = self._build_payload(command)
        existing = await self.repository.get_by_external_id(
            command.source, command.external_alert_id
        )
        if existing is not None:
            return self._resolve_replay(existing, command, payload)

        alert = Alert(
            source=command.source,
            external_alert_id=command.external_alert_id,
            fingerprint=self._fingerprint(command),
            alert_name=command.alert_name,
            service=command.service,
            instance=command.instance,
            severity=command.severity,
            status=AlertStatus.RECEIVED,
            payload=payload,
            started_at=command.started_at,
        )

        try:
            await self.repository.add(alert)
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            winner = await self.repository.get_by_external_id(
                command.source, command.external_alert_id
            )
            if winner is None:
                raise
            return self._resolve_replay(winner, command, payload)

        await self.session.refresh(alert)
        return CreateAlertResult(alert=alert, created=True)

    async def get(self, alert_id: UUID) -> Alert:
        alert = await self.repository.get(alert_id)
        if alert is None:
            raise AlertNotFoundError
        return alert

    async def list(
        self,
        *,
        status: AlertStatus | None,
        severity: AlertSeverity | None,
        service: str | None,
        page: int,
        page_size: int,
    ) -> AlertPage:
        items, total = await self.repository.list(
            status=status,
            severity=severity,
            service=service,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return AlertPage(items=items, total=total, page=page, page_size=page_size)

    @staticmethod
    def _build_payload(command: AlertCreate) -> dict[str, object]:
        return {
            **command.payload,
            "value": command.value,
            "threshold": command.threshold,
        }

    @staticmethod
    def _fingerprint(command: AlertCreate) -> str:
        identity = "\x1f".join(
            (
                command.alert_name.casefold(),
                command.service.casefold(),
                (command.instance or "").casefold(),
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    @staticmethod
    def _resolve_replay(
        existing: Alert,
        command: AlertCreate,
        payload: dict[str, object],
    ) -> CreateAlertResult:
        same_content = (
            existing.alert_name == command.alert_name
            and existing.service == command.service
            and existing.instance == command.instance
            and existing.severity == command.severity
            and existing.started_at == command.started_at
            and existing.payload == payload
        )
        if not same_content:
            raise AlertIdempotencyConflictError
        return CreateAlertResult(alert=existing, created=False)
