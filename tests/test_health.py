"""API tests for the health endpoints (no database needed)."""


def test_health_reports_ok_when_neo4j_is_up(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["neo4j"] == "up"


def test_health_stays_200_when_neo4j_is_down(client, fake_db):
    fake_db.available = False
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "version": "0.1.0", "neo4j": "down"}


def test_ready_returns_503_when_neo4j_is_down(client, fake_db):
    fake_db.available = False
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "Neo4j is not reachable"


def test_ready_returns_200_when_neo4j_is_up(client):
    assert client.get("/health/ready").status_code == 200


def test_swagger_docs_are_served(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "Retraction Contamination Tracker"
