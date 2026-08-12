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
        hide_input_in_errors=True,
    )

    app_name: str = "Alert Sage API"
    app_version: str = "0.1.0"
    runtime_profile: Literal["real", "demo", "test"]
    component_role: Literal["api", "worker", "relay", "migration", "test"]
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://alert_sage:alert_sage@localhost:5432/alert_sage"
    test_database_url: str | None = None
    redis_url: str = "redis://localhost:6379/1"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_log_service: str = Field(default="alert-sage-worker", min_length=1, max_length=64)
    outbox_poll_interval_seconds: float = Field(default=5.0, gt=0, le=60)
    outbox_batch_size: int = Field(default=50, ge=1, le=500)
    outbox_retry_base_seconds: float = Field(default=2.0, gt=0, le=300)
    outbox_retry_max_seconds: float = Field(default=60.0, gt=0, le=3600)
    workflow_lock_ttl_seconds: int = 300
    rag_evaluation_lock_ttl_seconds: int = Field(default=1800, ge=60, le=7200)
    rag_evaluation_query_interval_seconds: float = Field(default=6.5, ge=0, le=60)
    workflow_event_poll_seconds: float = 2.0
    metrics_enabled: bool = True
    worker_metrics_port: int = Field(default=9101, ge=1, le=65_535)
    workflow_tool_timeout_seconds: float = Field(default=10.0, gt=0)
    demo_tool_failure_provider: Literal["none", "metrics", "logs", "cmdb", "knowledge"] = "none"
    case_sync_timeout_seconds: float = Field(default=180.0, gt=0)
    knowledge_retrieval_timeout_seconds: float = Field(default=15.0, gt=0)
    rag_evaluation_set_path: str = "evaluation_sets/rag-v2.6-baseline.json"
    knowledge_provider: Literal["mock", "dify"] | None = None
    dify_base_url: str = "https://api.dify.ai/v1"
    dify_api_key: SecretStr | None = None
    dify_dataset_id: str | None = None
    dify_evaluation_dataset_id: str | None = None
    dify_http_timeout_seconds: float = Field(default=15.0, gt=0)
    dify_poll_interval_seconds: float = Field(default=2.0, gt=0)
    dify_max_retries: int = Field(default=2, ge=0, le=5)
    diagnostic_model_provider: Literal["mock", "deepseek"] | None = None
    diagnostic_model_base_url: str = "https://api.deepseek.com"
    diagnostic_model_api_key: SecretStr | None = None
    diagnostic_model_name: str = Field(default="deepseek-v4-flash", min_length=1, max_length=128)
    diagnostic_model_timeout_seconds: float = Field(default=60.0, gt=0)
    diagnostic_model_max_retries: int = Field(default=2, ge=0, le=5)
    diagnostic_model_max_tokens: int = Field(default=3000, ge=512, le=32_768)
    diagnostic_model_temperature: float = Field(default=0.1, ge=0, le=2)
    diagnostic_model_thinking_enabled: bool = False
    feishu_enabled: bool = False
    feishu_app_id: str | None = None
    feishu_app_secret: SecretStr | None = None
    feishu_verification_token: SecretStr | None = None
    feishu_encrypt_key: SecretStr | None = None
    feishu_expected_tenant_key: str | None = None
    feishu_target_chat_id: str | None = None
    feishu_source_allowlist: list[str] = Field(default_factory=list)
    feishu_approvers: dict[str, str] = Field(default_factory=dict)
    feishu_web_base_url: str | None = None
    feishu_api_base_url: str = "https://open.feishu.cn/open-apis"
    feishu_http_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    feishu_max_retries: int = Field(default=2, ge=0, le=5)
    feishu_callback_max_bytes: int = Field(default=256 * 1024, ge=1024, le=1024 * 1024)
    feishu_callback_max_age_seconds: int = Field(default=300, ge=30, le=900)
    feishu_token_refresh_margin_seconds: int = Field(default=300, ge=30, le=900)
    build_revision: str | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @model_validator(mode="after")
    def validate_integrations(self) -> "Settings":
        if self.outbox_retry_max_seconds < self.outbox_retry_base_seconds:
            raise ValueError(
                "ALERT_SAGE_OUTBOX_RETRY_MAX_SECONDS must be greater than or equal to "
                "ALERT_SAGE_OUTBOX_RETRY_BASE_SECONDS"
            )
        self.dify_base_url = self.dify_base_url.rstrip("/")
        self.diagnostic_model_base_url = self.diagnostic_model_base_url.rstrip("/")
        self.feishu_api_base_url = self.feishu_api_base_url.rstrip("/")
        if self.feishu_web_base_url:
            self.feishu_web_base_url = self.feishu_web_base_url.rstrip("/")

        if self.runtime_profile in {"demo", "test"}:
            self._validate_offline_profile()
            return self

        self._validate_real_profile()
        return self

    def _validate_offline_profile(self) -> None:
        if self.component_role != "relay" and (
            self.knowledge_provider != "mock" or self.diagnostic_model_provider != "mock"
        ):
            raise ValueError(
                "demo/test runtime profiles require mock knowledge and diagnostic providers"
            )
        if self.feishu_enabled:
            raise ValueError("demo/test runtime profiles cannot enable Feishu")
        if any(
            (
                self._secret_value(self.dify_api_key),
                self._secret_value(self.diagnostic_model_api_key),
                self._secret_value(self.feishu_app_secret),
                self._secret_value(self.feishu_verification_token),
                self._secret_value(self.feishu_encrypt_key),
            )
        ):
            raise ValueError("demo/test runtime profiles cannot receive cloud credentials")
        if self.demo_tool_failure_provider != "none" and self.runtime_profile != "demo":
            raise ValueError(
                "ALERT_SAGE_DEMO_TOOL_FAILURE_PROVIDER is only allowed in the demo profile"
            )

    def _validate_real_profile(self) -> None:
        if self.component_role == "relay":
            if self.feishu_enabled:
                raise ValueError(
                    "the relay component cannot enable or receive Feishu configuration"
                )
            return
        if self.knowledge_provider != "dify" or self.diagnostic_model_provider != "deepseek":
            raise ValueError(
                "real runtime profile requires DeepSeek and Dify; "
                "mock or mixed providers are invalid"
            )
        if self.component_role in {"api", "worker", "migration"}:
            self._validate_dify()
        if self.component_role == "worker" and not self._secret_value(
            self.diagnostic_model_api_key
        ):
            raise ValueError(
                "DeepSeek diagnostic model requires ALERT_SAGE_DIAGNOSTIC_MODEL_API_KEY"
            )
        if self.feishu_enabled:
            self._validate_feishu_for_component()

    def _validate_dify(self) -> None:
        api_key = self._secret_value(self.dify_api_key)
        if not api_key:
            raise ValueError("Dify knowledge provider requires ALERT_SAGE_DIFY_API_KEY")
        if not self.dify_dataset_id:
            raise ValueError("Dify knowledge provider requires ALERT_SAGE_DIFY_DATASET_ID")
        self._validate_uuid(self.dify_dataset_id, "ALERT_SAGE_DIFY_DATASET_ID")
        if self.dify_evaluation_dataset_id:
            self._validate_uuid(
                self.dify_evaluation_dataset_id,
                "ALERT_SAGE_DIFY_EVALUATION_DATASET_ID",
            )

    def _validate_feishu_for_component(self) -> None:
        common = {
            "ALERT_SAGE_FEISHU_APP_ID": self.feishu_app_id,
            "ALERT_SAGE_FEISHU_TARGET_CHAT_ID": self.feishu_target_chat_id,
            "ALERT_SAGE_FEISHU_WEB_BASE_URL": self.feishu_web_base_url,
        }
        required: dict[str, object | None] = dict(common)
        if self.component_role == "api":
            required.update(
                {
                    "ALERT_SAGE_FEISHU_VERIFICATION_TOKEN": self._secret_value(
                        self.feishu_verification_token
                    ),
                    "ALERT_SAGE_FEISHU_ENCRYPT_KEY": self._secret_value(self.feishu_encrypt_key),
                    "ALERT_SAGE_FEISHU_EXPECTED_TENANT_KEY": self.feishu_expected_tenant_key,
                }
            )
            if not self.feishu_source_allowlist:
                required["ALERT_SAGE_FEISHU_SOURCE_ALLOWLIST"] = None
        if self.component_role == "worker":
            required["ALERT_SAGE_FEISHU_APP_SECRET"] = self._secret_value(self.feishu_app_secret)
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                "Feishu is enabled but required configuration is missing: " + ", ".join(missing)
            )
        if not self.feishu_approvers:
            raise ValueError("Feishu is enabled but ALERT_SAGE_FEISHU_APPROVERS is empty")
        if not self.feishu_web_base_url or not self.feishu_web_base_url.startswith("https://"):
            raise ValueError("ALERT_SAGE_FEISHU_WEB_BASE_URL must use HTTPS")
        invalid_approver = any(
            not key.strip() or not label.strip() for key, label in self.feishu_approvers.items()
        )
        if invalid_approver:
            raise ValueError(
                "ALERT_SAGE_FEISHU_APPROVERS must map non-empty open_id values to labels"
            )

    @staticmethod
    def _secret_value(value: SecretStr | None) -> str:
        return value.get_secret_value().strip() if value else ""

    @staticmethod
    def _validate_uuid(value: str, setting_name: str) -> None:
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError(f"{setting_name} must be a UUID") from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
