import os

# Isolate the app engine to a throwaway DB and disable the background loop BEFORE
# importing the app — schema is now Alembic-managed, so app startup no longer
# creates tables; the app lifespan still seeds providers against this engine.
# (setdefault keeps an explicit CMA_DATABASE_URL override working.)
os.environ.setdefault("CMA_DATABASE_URL", "sqlite:///./data/test_suite.db")
os.environ.setdefault("CMA_ENABLE_MAINTENANCE", "false")
# Isolate the vector store too: the /metrics backlog gauge traverses
# _count_vector_events into ChromaDB — without this, test scrapes would
# initialize a client against the developer's live ./data/chroma store.
os.environ.setdefault("CMA_CHROMA_PERSIST_DIR", "./data/test_chroma")
os.makedirs("data", exist_ok=True)  # data/ is gitignored — absent on fresh CI checkouts

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_providers  # noqa: E402
from app.services.rate_limit import reset as reset_rate_limit  # noqa: E402

# In-memory SQLite for tests
TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=TEST_ENGINE)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    """Create fresh tables for each test and seed provider pricing."""
    # Test request DB (in-memory, via the get_db override) ...
    Base.metadata.create_all(bind=TEST_ENGINE)
    # ... and the app's own engine, which the lifespan seeds on startup.
    Base.metadata.create_all(bind=engine)
    reset_rate_limit()  # in-process limiter is global; isolate per test
    db = TestSession()
    try:
        seed_providers(db)
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    """TestClient with overridden DB."""
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def authed_client(client):
    """Client that has completed setup and includes API key in headers."""
    resp = client.post(
        "/api/v1/setup",
        json={
            "admin_email": "test@example.com",
            "admin_password": "testpass123",
            "deployment_name": "Test",
        },
    )
    assert resp.status_code == 200
    api_key = resp.json()["api_key"]
    client.headers["Authorization"] = f"Bearer {api_key}"
    return client
