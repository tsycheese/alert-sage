from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models.enums import AlertSeverity, AlertStatus

Source = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        to_lower=True,
    ),
]
ExternalAlertId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
RequiredName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
OptionalName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class AlertCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source = "web"
    external_alert_id: ExternalAlertId
    alert_name: RequiredName
    service: RequiredName
    instance: OptionalName | None = None
    severity: AlertSeverity
    value: float = Field(allow_inf_nan=False)
    threshold: float = Field(allow_inf_nan=False)
    started_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("started_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must include a timezone offset")
        return value


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    external_alert_id: str
    fingerprint: str | None
    alert_name: str
    service: str
    instance: str | None
    severity: AlertSeverity
    status: AlertStatus
    payload: dict[str, Any]
    started_at: datetime
    created_at: datetime
    updated_at: datetime


class AlertListResponse(BaseModel):
    items: list[AlertResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    pages: int = Field(ge=0)
