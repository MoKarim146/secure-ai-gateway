"""API tests run against a real PostgreSQL (start it with `docker compose up -d db`)."""

import asyncio
import os
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gateway.config import Settings
from gateway.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway_test"
)
MAX_REQUEST_BYTES = 10_000


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Applies the real migrations once per test run, which also tests the migrations."""
    with pytest.MonkeyPatch.context() as env:
        env.setenv("DATABASE_URL", TEST_DATABASE_URL)
        command.upgrade(Config("alembic.ini"), "head")
    return TEST_DATABASE_URL


async def empty_tables(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE usage_records"))
    await engine.dispose()


def make_settings(database_url: str, **overrides: Any) -> Settings:
    """Settings for tests: only the offline mock provider unless a test switches more on."""
    # Explicit values beat environment variables, so a developer's own .env or exported
    # OLLAMA_BASE_URL or API keys can't change what the tests see or call.
    values: dict[str, Any] = {
        "database_url": database_url,
        "mock_provider_enabled": True,
        "ollama_base_url": "",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "pii_detector": "regex",
        "pii_trusted_providers": frozenset(),
        "max_request_bytes": MAX_REQUEST_BYTES,
    }
    return Settings(_env_file=None, **(values | overrides))


@pytest.fixture
def client(migrated_database: str) -> Iterator[TestClient]:
    asyncio.run(empty_tables(migrated_database))
    with TestClient(create_app(make_settings(migrated_database))) as test_client:
        yield test_client
