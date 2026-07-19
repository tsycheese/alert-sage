from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.workflows.alert.adapters import ContextProvider, DiagnosticModel
from app.workflows.alert.graph import build_alert_graph
from app.workflows.alert.service import AlertWorkflowService


def checkpoint_database_url(sqlalchemy_database_url: str) -> str:
    """Convert SQLAlchemy's psycopg URL into the URI accepted by psycopg."""
    return sqlalchemy_database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_json_modules=(),
        allowed_msgpack_modules=(),
    )


async def setup_checkpoint_database(database_url: str) -> None:
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_database_url(database_url),
        serde=checkpoint_serializer(),
    ) as checkpointer:
        await checkpointer.setup()


@asynccontextmanager
async def open_alert_workflow_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    database_url: str,
    context_providers: Sequence[ContextProvider] | None = None,
    diagnostic_model: DiagnosticModel | None = None,
    tool_timeout_seconds: float = 1.0,
    tool_max_attempts: int = 2,
) -> AsyncIterator[AlertWorkflowService]:
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_database_url(database_url),
        serde=checkpoint_serializer(),
    ) as checkpointer:
        await checkpointer.setup()
        graph = build_alert_graph(
            checkpointer=checkpointer,
            context_providers=context_providers,
            diagnostic_model=diagnostic_model,
            tool_timeout_seconds=tool_timeout_seconds,
            tool_max_attempts=tool_max_attempts,
        )
        yield AlertWorkflowService(session_factory=session_factory, graph=graph)
