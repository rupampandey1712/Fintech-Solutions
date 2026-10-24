"""Integration test fixtures, gated behind FINCORE_TEST_DATABASE_URL."""

import os
import tempfile
from collections.abc import AsyncIterator

# Settings has no env prefix, so these must use the plain variable names it reads.
os.environ.setdefault("BENEFICIARY_COOLING_SECONDS", "0")
os.environ.setdefault("OUTBOX_WORKER_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("MESSAGE_PROVIDER", "inline")
os.environ.setdefault("STORAGE_PROVIDER", "filesystem")
os.environ.setdefault(
    "KYC_STORAGE_PATH", os.path.join(tempfile.gettempdir(), "fincore-test-kyc"),
)

TEST_DATABASE_URL = os.environ.get("FINCORE_TEST_DATABASE_URL", "")

# In-memory telemetry, installed when app.main configures the providers (see test_telemetry.py).
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.infrastructure import telemetry

SPANS = InMemorySpanExporter()
METRICS = InMemoryMetricReader()
telemetry.extra_span_processors.append(SimpleSpanProcessor(SPANS))
telemetry.extra_metric_readers.append(METRICS)

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.models import Base

SKIP_INTEGRATION = not TEST_DATABASE_URL
requires_database = pytest.mark.skipif(
    SKIP_INTEGRATION, reason="FINCORE_TEST_DATABASE_URL is not set",
)


@pytest_asyncio.fixture
async def test_engine() -> AsyncIterator:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_engine) -> AsyncIterator[httpx.AsyncClient]:
    from app.infrastructure.database import get_session
    from app.main import app

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def session(test_engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as database_session:
        yield database_session
