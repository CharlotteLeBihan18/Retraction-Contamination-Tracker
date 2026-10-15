"""Shared pytest fixtures."""

import os
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Neo4jClient
from app.dependencies import get_db
from app.main import app


class FakeNeo4jClient:
    """Stands in for Neo4jClient so API tests run without a database."""

    def __init__(self, available: bool = True, rows: list[dict] | None = None) -> None:
        self.available = available
        self.rows = rows or []
        self.responses: list[list[dict]] = []
        self.queries: list[tuple[str, dict]] = []

    def is_available(self) -> bool:
        """Return the configured availability."""
        return self.available

    def read(self, query: str, parameters: dict | None = None) -> list[dict]:
        """Record the query and return the next queued answer, or the canned rows.

        Put one list per expected query in ``responses`` when an endpoint runs
        several queries (for example a count query, then a page query).
        """
        self.queries.append((query, parameters or {}))
        if self.responses:
            return self.responses.pop(0)
        return self.rows

    def close(self) -> None:
        """Nothing to close."""


@pytest.fixture
def fake_db() -> FakeNeo4jClient:
    """A fake database that reports itself as available."""
    return FakeNeo4jClient()


@pytest.fixture
def client(fake_db: FakeNeo4jClient):
    """A TestClient whose database dependency is the fake client.

    The app's startup is skipped (no ``with`` block), so no real driver is built.
    """
    app.dependency_overrides[get_db] = lambda: fake_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def neo4j_client():
    """A real Neo4j client; tests using it are skipped when Neo4j is unreachable."""
    db = Neo4jClient.from_settings(Settings())
    if not db.is_available():
        db.close()
        pytest.skip("Neo4j is not reachable (start it with: make docker-run)")
    yield db
    db.close()


@pytest.fixture(scope="session")
def stack_url() -> str:
    """Base URL of the running Docker stack; skip when it is not up."""
    url = os.environ.get("STACK_URL", "http://localhost")
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=3):
            pass
    except OSError:
        pytest.skip(f"Docker stack not reachable at {url} (start it with: make docker-run)")
    return url
