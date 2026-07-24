from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ALERT_SAGE_",
        extra="ignore",
    )

    app_name: str = "Alert Sage API"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://alert_sage:alert_sage@localhost:5432/alert_sage"
    test_database_url: str | None = None
    redis_url: str = "redis://localhost:6379/1"
    celery_broker_url: str = "redis://localhost:6379/0"
    workflow_lock_ttl_seconds: int = 300
    workflow_event_poll_seconds: float = 2.0
    metrics_enabled: bool = True
    worker_metrics_port: int = Field(default=9101, ge=1, le=65_535)
    workflow_tool_timeout_seconds: float = Field(default=10.0, gt=0)
    case_sync_timeout_seconds: float = Field(default=180.0, gt=0)
    knowledge_retrieval_timeout_seconds: float = Field(default=15.0, gt=0)
    knowledge_provider: Literal["mock", "dify"] = "mock"
    dify_base_url: str = "https://api.dify.ai/v1"
    dify_api_key: SecretStr | None = None
    dify_dataset_id: str | None = None
    dify_http_timeout_seconds: float = Field(default=15.0, gt=0)
    dify_poll_interval_seconds: float = Field(default=2.0, gt=0)
    dify_max_retries: int = Field(default=2, ge=0, le=5)
    diagnostic_model_provider: Literal["mock", "deepseek"] = "mock"
    diagnostic_model_base_url: str = "https://api.deepseek.com"
    diagnostic_model_api_key: SecretStr | None = None
    diagnostic_model_name: str = Field(default="deepseek-v4-flash", min_length=1, max_length=128)
    diagnostic_model_timeout_seconds: float = Field(default=60.0, gt=0)
    diagnostic_model_max_retries: int = Field(default=2, ge=0, le=5)
    diagnostic_model_max_tokens: int = Field(default=3000, ge=512, le=32_768)
    diagnostic_model_temperature: float = Field(default=0.1, ge=0, le=2)
    diagnostic_model_thinking_enabled: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @model_validator(mode="after")
    def validate_integrations(self) -> "Settings":
        self.dify_base_url = self.dify_base_url.rstrip("/")
        self.diagnostic_model_base_url = self.diagnostic_model_base_url.rstrip("/")
        if self.diagnostic_model_provider == "deepseek":
            api_key = (
                self.diagnostic_model_api_key.get_secret_value().strip()
                if self.diagnostic_model_api_key
                else ""
            )
            if not api_key:
                raise ValueError(
                    "DeepSeek diagnostic model requires ALERT_SAGE_DIAGNOSTIC_MODEL_API_KEY"
                )
        if self.knowledge_provider != "dify":
            return self
        api_key = self.dify_api_key.get_secret_value().strip() if self.dify_api_key else ""
        if not api_key:
            raise ValueError("Dify knowledge provider requires ALERT_SAGE_DIFY_API_KEY")
        if not self.dify_dataset_id:
            raise ValueError("Dify knowledge provider requires ALERT_SAGE_DIFY_DATASET_ID")
        try:
            UUID(self.dify_dataset_id)
        except ValueError as exc:
            raise ValueError("ALERT_SAGE_DIFY_DATASET_ID must be a UUID") from exc
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
