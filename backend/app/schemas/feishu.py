from uuid import UUID

from pydantic import BaseModel

from app.models.enums import FeishuCardStatus


class FeishuChannelStatusResponse(BaseModel):
    alert_id: UUID
    enabled: bool
    eligible: bool
    eligibility_reason: str
    binding_id: UUID | None = None
    shared_card_status: FeishuCardStatus | None = None
    desired_revision: int | None = None
    delivered_revision: int | None = None
    last_error_code: str | None = None
    retry_available: bool = False


class FeishuRetryResponse(BaseModel):
    alert_id: UUID
    delivery_id: UUID | None
    revision: int | None
    dispatched: bool
