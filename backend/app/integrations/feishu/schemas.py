from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FeishuEncryptedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encrypt: str = Field(min_length=1, max_length=400_000)


class FeishuChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge: str = Field(min_length=1, max_length=512)
    token: str = Field(min_length=1, max_length=512)
    type: Literal["url_verification"]


class FeishuCallbackHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: Literal["card.action.trigger"]
    create_time: str = Field(min_length=1, max_length=32)
    token: str = Field(min_length=1, max_length=512)
    app_id: str = Field(min_length=1, max_length=64)
    tenant_key: str = Field(min_length=1, max_length=128)


class FeishuOperator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_id: str = Field(min_length=1, max_length=128)
    tenant_key: str | None = Field(default=None, max_length=128)
    union_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)


class FeishuActionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_message_id: str = Field(min_length=1, max_length=128)
    open_chat_id: str = Field(min_length=1, max_length=128)
    url: str | None = Field(default=None, max_length=2048)
    preview_token: str | None = Field(default=None, max_length=512)


class FeishuCardAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=64)
    value: dict[str, Any]
    option: str | None = Field(default=None, max_length=1000)
    input_value: str | None = Field(default=None, max_length=1000)
    form_value: dict[str, Any] | None = None
    name: str | None = Field(default=None, max_length=128)
    timezone: str | None = Field(default=None, max_length=64)


class FeishuActionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: FeishuOperator
    token: str = Field(min_length=1, max_length=512)
    context: FeishuActionContext
    action: FeishuCardAction
    host: str | None = Field(default=None, max_length=64)


class FeishuCardCallback(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["2.0"] = Field(alias="schema")
    header: FeishuCallbackHeader
    event: FeishuActionEvent


class FeishuActionValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: UUID
    action: Literal["start", "retry", "approve", "reject", "reanalyze"]
    revision: int = Field(ge=1)
    nonce: str = Field(min_length=32, max_length=128)


class FeishuActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback: FeishuCardCallback
    value: FeishuActionValue
    feedback: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_feedback(self) -> "FeishuActionCommand":
        if self.value.action == "reanalyze" and not (self.feedback or "").strip():
            raise ValueError("reanalyze feedback is required")
        return self


class FeishuCardSyncMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: UUID


class FeishuReminderSendMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: UUID
