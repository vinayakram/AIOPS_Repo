"""
Shared pytest fixtures for AIops Telemetry test suite.

Fixture tiers
─────────────
db_session      → in-memory SQLite session (unit tests)
app             → FastAPI app wired to in-memory DB (integration tests)
client          → async httpx TestClient (integration tests)

The real `aiops.db` file is NEVER touched during tests.
"""
import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from httpx import AsyncClient, ASGITransport

# ── In-memory database ────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db_engine():
    """Create a fresh in-memory SQLite engine for each test."""
    from server.database.engine import Base
    import server.database.models  # noqa: F401 — registers all ORM models with Base
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # share one connection so in-memory tables are visible everywhere
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    """Provide a SQLAlchemy session bound to the in-memory engine."""
    SessionLocal = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── FastAPI app (integration tests) ──────────────────────────────────────────

@pytest.fixture(scope="function")
def app(db_engine):
    """
    Return the FastAPI app with its database dependency overridden
    to use the in-memory SQLite engine.  The escalation background task
    is NOT started — route tests should not depend on it.
    """
    from sqlalchemy.orm import sessionmaker
    from server.main import app as fastapi_app
    from server.database.engine import get_db

    SessionLocal = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client(app):
    """Async httpx test client wrapping the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Helper factories ──────────────────────────────────────────────────────────

@pytest.fixture
def make_trace(db_session):
    """Factory fixture: create and persist a Trace row."""
    from datetime import datetime
    from server.database.models import Trace

    def _make(
        trace_id: str = "trace-001",
        app_name: str = "test-agent",
        status: str = "ok",
        total_duration_ms: float = 500.0,
    ) -> Trace:
        trace = Trace(
            id=trace_id,
            app_name=app_name,
            status=status,
            started_at=datetime.utcnow(),
            ended_at=datetime.utcnow(),
            total_duration_ms=total_duration_ms,
        )
        db_session.add(trace)
        db_session.commit()
        db_session.refresh(trace)
        return trace

    return _make


@pytest.fixture
def make_span(db_session):
    """Factory fixture: create and persist a Span row."""
    from datetime import datetime
    from server.database.models import Span

    def _make(
        span_id: str = "span-001",
        trace_id: str = "trace-001",
        name: str = "llm_call",
        span_type: str = "llm",
        status: str = "ok",
        duration_ms: float = 200.0,
    ) -> Span:
        span = Span(
            id=span_id,
            trace_id=trace_id,
            name=name,
            span_type=span_type,
            status=status,
            started_at=datetime.utcnow(),
            ended_at=datetime.utcnow(),
            duration_ms=duration_ms,
        )
        db_session.add(span)
        db_session.commit()
        db_session.refresh(span)
        return span

    return _make


@pytest.fixture
def make_issue(db_session):
    """Factory fixture: create and persist an Issue row."""
    from server.database.models import Issue

    _counter = {"n": 0}

    def _make(
        app_name: str = "test-agent",
        issue_type: str = "high_latency",
        severity: str = "medium",
        status: str = "OPEN",
        title: str = "Test issue",
    ) -> Issue:
        _counter["n"] += 1
        issue = Issue(
            app_name=app_name,
            issue_type=issue_type,
            severity=severity,
            status=status,
            fingerprint=f"fp-{_counter['n']:04d}",
            title=title,
        )
        db_session.add(issue)
        db_session.commit()
        db_session.refresh(issue)
        return issue

    return _make
