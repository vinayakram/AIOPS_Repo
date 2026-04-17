import asyncio
import os

import pytest

# Force in-memory DB and disable poller for tests
os.environ["DB_PATH"] = ":memory:"
os.environ["AIOPS_POLL_ENABLED"] = "false"


@pytest.fixture(autouse=True, scope="session")
def setup_db():
    """Initialize the SQLite database before any test runs."""
    from app.core.database import init_db, close_db

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    yield
    loop.run_until_complete(close_db())
    loop.close()
