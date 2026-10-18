"""The endpoints agreed in the proposal must exist (this is the API contract)."""

import pytest

EXPECTED = [
    ("get", "/health"),
    ("get", "/health/ready"),
    ("get", "/api/papers/{paper_id}/contamination"),
    ("get", "/api/papers/{paper_id}/exposure"),
    ("get", "/api/papers/{paper_id}/risk"),
    ("get", "/api/journals/zombie-citations"),
    ("get", "/api/retractions/top-spreaders"),
    ("get", "/api/communities"),
    ("get", "/api/ask"),
    ("post", "/api/audit"),
]


@pytest.mark.parametrize(("method", "path"), EXPECTED)
def test_endpoint_is_registered(client, method, path):
    paths = client.get("/openapi.json").json()["paths"]
    assert path in paths, f"{path} is missing"
    assert method in paths[path], f"{method.upper()} {path} is missing"


@pytest.mark.parametrize(
    "path", ["/api/audit", "/api/papers/{paper_id}/contamination", "/api/papers/{paper_id}/risk"]
)
def test_placeholders_document_their_501(client, path):
    operation = next(iter(client.get("/openapi.json").json()["paths"][path].values()))
    assert "501" in operation["responses"]


def test_ask_rejects_an_empty_question(client):
    assert client.get("/api/ask", params={"q": ""}).status_code == 422
