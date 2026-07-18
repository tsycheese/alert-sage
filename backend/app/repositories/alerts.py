from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.enums import AlertSeverity, AlertStatus


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, alert: Alert) -> Alert:
        self.session.add(alert)
        await self.session.flush()
        return alert

    async def get(self, alert_id: UUID) -> Alert | None:
        return await self.session.get(Alert, alert_id)

    async def get_by_external_id(self, source: str, external_alert_id: str) -> Alert | None:
        statement = select(Alert).where(
            Alert.source == source,
            Alert.external_alert_id == external_alert_id,
        )
        return await self.session.scalar(statement)

    async def list(
        self,
        *,
        status: AlertStatus | None,
        severity: AlertSeverity | None,
        service: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Alert], int]:
        filters = []
        if status is not None:
            filters.append(Alert.status == status)
        if severity is not None:
            filters.append(Alert.severity == severity)
        if service is not None:
            filters.append(Alert.service == service)

        items_statement = (
            select(Alert)
            .where(*filters)
            .order_by(Alert.created_at.desc(), Alert.id.desc())
            .offset(offset)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(Alert).where(*filters)

        items = list((await self.session.scalars(items_statement)).all())
        total = int(await self.session.scalar(count_statement) or 0)
        return items, total
