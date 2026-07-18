import asyncio
import selectors
import sys
import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

import app.models  # noqa: F401 - register all ORM models before create_all
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app

if sys.platform == "win32":

    def pytest_asyncio_loop_factories() -> dict[str, object]:
        return {
            "selector": lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        }


@pytest_asyncio.fixture
async def test_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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

    try:
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(DropSchema(schema_name, cascade=True))
        await admin_engine.dispose()


@pytest_asyncio.fixture
async def api_client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db_session, None)
