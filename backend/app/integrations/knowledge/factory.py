from app.core.config import Settings, get_settings
from app.integrations.knowledge.cases import CasePublisher, MockCasePublisher
from app.integrations.knowledge.dify import DifyKnowledgeAdapter, DifyKnowledgeConfig
from app.integrations.knowledge.retrieval import KnowledgeRetriever, MockKnowledgeRetriever
from app.observability.adapters import InstrumentedCasePublisher, InstrumentedKnowledgeRetriever


def _dify_adapter(
    settings: Settings,
    *,
    dataset_id: str | None = None,
    refresh_completed_documents: bool = False,
) -> DifyKnowledgeAdapter:
    resolved_dataset_id = dataset_id or settings.dify_dataset_id
    if settings.dify_api_key is None or resolved_dataset_id is None:
        raise ValueError("Dify settings are incomplete")
    return DifyKnowledgeAdapter(
        DifyKnowledgeConfig(
            base_url=settings.dify_base_url,
            api_key=settings.dify_api_key.get_secret_value(),
            dataset_id=resolved_dataset_id,
            http_timeout_seconds=settings.dify_http_timeout_seconds,
            poll_interval_seconds=settings.dify_poll_interval_seconds,
            max_retries=settings.dify_max_retries,
            refresh_completed_documents=refresh_completed_documents,
        )
    )


def create_case_publisher(settings: Settings | None = None) -> CasePublisher:
    resolved = settings or get_settings()
    publisher: CasePublisher = (
        _dify_adapter(resolved) if resolved.knowledge_provider == "dify" else MockCasePublisher()
    )
    return InstrumentedCasePublisher(publisher, provider=resolved.knowledge_provider)


def create_knowledge_retriever(settings: Settings | None = None) -> KnowledgeRetriever:
    resolved = settings or get_settings()
    retriever: KnowledgeRetriever = (
        _dify_adapter(resolved)
        if resolved.knowledge_provider == "dify"
        else MockKnowledgeRetriever()
    )
    return InstrumentedKnowledgeRetriever(retriever, provider=resolved.knowledge_provider)


def get_knowledge_retriever() -> KnowledgeRetriever:
    return create_knowledge_retriever()


def create_evaluation_case_publisher(settings: Settings | None = None) -> CasePublisher:
    resolved = settings or get_settings()
    if resolved.knowledge_provider != "dify":
        return MockCasePublisher()
    if not resolved.dify_evaluation_dataset_id:
        raise ValueError("Dify evaluation dataset settings are incomplete")
    return InstrumentedCasePublisher(
        _dify_adapter(
            resolved,
            dataset_id=resolved.dify_evaluation_dataset_id,
            refresh_completed_documents=True,
        ),
        provider=resolved.knowledge_provider,
    )


def create_evaluation_retriever(settings: Settings | None = None) -> KnowledgeRetriever:
    resolved = settings or get_settings()
    if resolved.knowledge_provider != "dify":
        return InstrumentedKnowledgeRetriever(
            MockKnowledgeRetriever(),
            provider=resolved.knowledge_provider,
        )
    if not resolved.dify_evaluation_dataset_id:
        raise ValueError("Dify evaluation dataset settings are incomplete")
    return InstrumentedKnowledgeRetriever(
        _dify_adapter(
            resolved,
            dataset_id=resolved.dify_evaluation_dataset_id,
        ),
        provider=resolved.knowledge_provider,
    )
