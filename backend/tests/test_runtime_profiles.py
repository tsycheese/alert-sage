import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.knowledge.cases import MockCasePublisher
from app.integrations.knowledge.factory import create_case_publisher, create_knowledge_retriever
from app.integrations.knowledge.retrieval import MockKnowledgeRetriever
from app.integrations.llm.factory import create_diagnostic_model
from app.workflows.alert.adapters import MockDiagnosticModel

DATASET_ID = "8dc8a66d-8202-4099-b0ee-6d42e0bf57d1"


def test_runtime_profile_has_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALERT_SAGE_RUNTIME_PROFILE", raising=False)
    with pytest.raises(ValidationError, match="runtime_profile"):
        Settings(
            _env_file=None,
            component_role="test",
            knowledge_provider="mock",
            diagnostic_model_provider="mock",
        )


def test_real_profile_rejects_mock_or_mixed_providers() -> None:
    with pytest.raises(ValidationError, match="mock or mixed"):
        Settings(
            _env_file=None,
            runtime_profile="real",
            component_role="worker",
            knowledge_provider="mock",
            diagnostic_model_provider="deepseek",
        )


def test_offline_profiles_reject_cloud_credentials_and_feishu() -> None:
    with pytest.raises(ValidationError, match="cloud credentials"):
        Settings(
            _env_file=None,
            runtime_profile="test",
            component_role="test",
            knowledge_provider="mock",
            diagnostic_model_provider="mock",
            dify_api_key="must-not-enter-tests",
        )
    with pytest.raises(ValidationError, match="cannot enable Feishu"):
        Settings(
            _env_file=None,
            runtime_profile="demo",
            component_role="test",
            knowledge_provider="mock",
            diagnostic_model_provider="mock",
            feishu_enabled=True,
        )


def test_test_profile_factories_are_always_mock() -> None:
    settings = Settings(
        _env_file=None,
        runtime_profile="test",
        component_role="test",
        knowledge_provider="mock",
        diagnostic_model_provider="mock",
    )
    assert isinstance(create_diagnostic_model(settings), MockDiagnosticModel)
    assert isinstance(create_knowledge_retriever(settings).delegate, MockKnowledgeRetriever)
    assert isinstance(create_case_publisher(settings).delegate, MockCasePublisher)


def test_relay_real_profile_needs_no_vendor_credentials() -> None:
    settings = Settings(
        _env_file=None,
        runtime_profile="real",
        component_role="relay",
    )
    assert settings.dify_api_key is None
    assert settings.diagnostic_model_api_key is None
    assert settings.feishu_app_secret is None


def test_relay_demo_profile_needs_no_provider_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALERT_SAGE_KNOWLEDGE_PROVIDER", raising=False)
    monkeypatch.delenv("ALERT_SAGE_DIAGNOSTIC_MODEL_PROVIDER", raising=False)
    settings = Settings(
        _env_file=None,
        runtime_profile="demo",
        component_role="relay",
    )
    assert settings.knowledge_provider is None
    assert settings.diagnostic_model_provider is None


def test_feishu_role_specific_configuration_does_not_cross_secret_boundaries() -> None:
    api = Settings(
        _env_file=None,
        runtime_profile="real",
        component_role="api",
        knowledge_provider="dify",
        dify_api_key="dify-secret",
        dify_dataset_id=DATASET_ID,
        diagnostic_model_provider="deepseek",
        feishu_enabled=True,
        feishu_app_id="cli_test",
        feishu_verification_token="verification-secret",
        feishu_encrypt_key="encrypt-secret",
        feishu_expected_tenant_key="tenant-test",
        feishu_target_chat_id="oc_test",
        feishu_source_allowlist=["synthetic-monitor"],
        feishu_approvers={"ou_approver": "Primary on-call"},
        feishu_web_base_url="https://alerts.example.test",
    )
    assert api.feishu_app_secret is None
    assert api.diagnostic_model_api_key is None

    worker = Settings(
        _env_file=None,
        runtime_profile="real",
        component_role="worker",
        knowledge_provider="dify",
        dify_api_key="dify-secret",
        dify_dataset_id=DATASET_ID,
        diagnostic_model_provider="deepseek",
        diagnostic_model_api_key="deepseek-secret",
        feishu_enabled=True,
        feishu_app_id="cli_test",
        feishu_app_secret="app-secret",
        feishu_target_chat_id="oc_test",
        feishu_approvers={"ou_approver": "Primary on-call"},
        feishu_web_base_url="https://alerts.example.test",
    )
    assert worker.feishu_verification_token is None
    assert worker.feishu_encrypt_key is None


def test_configuration_errors_hide_supplied_secret_values() -> None:
    with pytest.raises(ValidationError) as captured:
        Settings(
            _env_file=None,
            runtime_profile="real",
            component_role="worker",
            knowledge_provider="dify",
            dify_api_key="dify-sensitive-value",
            dify_dataset_id=DATASET_ID,
            diagnostic_model_provider="deepseek",
            diagnostic_model_api_key="deepseek-sensitive-value",
            feishu_enabled=True,
            feishu_app_id="cli_test",
            feishu_app_secret="feishu-sensitive-value",
        )
    message = str(captured.value)
    assert "dify-sensitive-value" not in message
    assert "deepseek-sensitive-value" not in message
    assert "feishu-sensitive-value" not in message
