import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.seed import seed_providers

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
    Base.metadata.create_all(bind=TEST_ENGINE)
    db = TestSession()
    try:
        seed_providers(db)
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


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
