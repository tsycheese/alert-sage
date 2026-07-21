from app.core.config import Settings, get_settings
from app.integrations.knowledge.cases import CasePublisher, MockCasePublisher
from app.integrations.knowledge.dify import DifyKnowledgeAdapter, DifyKnowledgeConfig
from app.integrations.knowledge.retrieval import KnowledgeRetriever, MockKnowledgeRetriever
from app.observability.adapters import InstrumentedCasePublisher, InstrumentedKnowledgeRetriever


def _dify_adapter(settings: Settings) -> DifyKnowledgeAdapter:
    if settings.dify_api_key is None or settings.dify_dataset_id is None:
        raise ValueError("Dify settings are incomplete")
    return DifyKnowledgeAdapter(
        DifyKnowledgeConfig(
            base_url=settings.dify_base_url,
            api_key=settings.dify_api_key.get_secret_value(),
            dataset_id=settings.dify_dataset_id,
            http_timeout_seconds=settings.dify_http_timeout_seconds,
            poll_interval_seconds=settings.dify_poll_interval_seconds,
            max_retries=settings.dify_max_retries,
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
