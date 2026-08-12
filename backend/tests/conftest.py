import asyncio
import os
import selectors
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

if os.getenv("ALERT_SAGE_RUN_VENDOR_LIVE_TESTS") == "1":
    os.environ["ALERT_SAGE_COMPONENT_ROLE"] = "test"
    os.environ["ALERT_SAGE_FEISHU_ENABLED"] = "false"
else:
    os.environ.update(
        {
            "ALERT_SAGE_RUNTIME_PROFILE": "test",
            "ALERT_SAGE_COMPONENT_ROLE": "test",
            "ALERT_SAGE_KNOWLEDGE_PROVIDER": "mock",
            "ALERT_SAGE_DIAGNOSTIC_MODEL_PROVIDER": "mock",
            "ALERT_SAGE_FEISHU_ENABLED": "false",
        }
    )
    for secret_name in (
        "ALERT_SAGE_DIFY_API_KEY",
        "ALERT_SAGE_DIAGNOSTIC_MODEL_API_KEY",
        "ALERT_SAGE_FEISHU_APP_SECRET",
        "ALERT_SAGE_FEISHU_VERIFICATION_TOKEN",
        "ALERT_SAGE_FEISHU_ENCRYPT_KEY",
    ):
        os.environ.pop(secret_name, None)

import app.models  # noqa: F401 - register all ORM models before create_all
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db_session, get_session_factory
from app.main import app

if sys.platform == "win32":

    def pytest_asyncio_loop_factories() -> dict[str, object]:
        return {
            "selector": lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        }


@dataclass(frozen=True, slots=True)
class IsolatedTestDatabase:
    session_factory: async_sessionmaker[AsyncSession]
    checkpoint_url: str
    schema_name: str


@pytest_asyncio.fixture
async def isolated_test_database() -> AsyncIterator[IsolatedTestDatabase]:
    settings = get_settings()
    database_url = settings.test_database_url or settings.database_url
    schema_name = f"test_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(database_url)

    async with admin_engine.begin() as connection:
        await connection.execute(CreateSchema(schema_name))

    test_engine = create_async_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    base_url = make_url(database_url)
    checkpoint_url = base_url.set(
        drivername="postgresql+psycopg",
        query={**base_url.query, "options": f"-csearch_path={schema_name}"},
    ).render_as_string(hide_password=False)

    try:
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield IsolatedTestDatabase(
            session_factory=session_factory,
            checkpoint_url=checkpoint_url,
            schema_name=schema_name,
        )
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(DropSchema(schema_name, cascade=True))
        await admin_engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(
    isolated_test_database: IsolatedTestDatabase,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield isolated_test_database.session_factory


@pytest_asyncio.fixture
async def api_client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_session_factory] = lambda: test_session_factory
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_session_factory, None)
