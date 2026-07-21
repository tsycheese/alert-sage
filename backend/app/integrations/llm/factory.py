from app.core.config import Settings, get_settings
from app.integrations.llm.openai_compatible import (
    OpenAICompatibleDiagnosticModel,
    OpenAICompatibleModelConfig,
)
from app.workflows.alert.adapters import DiagnosticModel, MockDiagnosticModel


def create_diagnostic_model(settings: Settings | None = None) -> DiagnosticModel:
    resolved = settings or get_settings()
    if resolved.diagnostic_model_provider == "mock":
        return MockDiagnosticModel()
    if resolved.diagnostic_model_api_key is None:
        raise ValueError("Diagnostic model settings are incomplete")
    return OpenAICompatibleDiagnosticModel(
        OpenAICompatibleModelConfig(
            base_url=resolved.diagnostic_model_base_url,
            api_key=resolved.diagnostic_model_api_key.get_secret_value(),
            model=resolved.diagnostic_model_name,
            provider=resolved.diagnostic_model_provider,
            timeout_seconds=resolved.diagnostic_model_timeout_seconds,
            max_retries=resolved.diagnostic_model_max_retries,
            max_tokens=resolved.diagnostic_model_max_tokens,
            temperature=resolved.diagnostic_model_temperature,
            thinking_enabled=resolved.diagnostic_model_thinking_enabled,
        )
    )
