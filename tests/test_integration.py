"""Integration tests against real services.

Skipped automatically when Neo4j or the Docker stack is not running,
so ``make test`` still passes on a machine without Docker.
"""

import json
import urllib.request

import pytest


@pytest.mark.neo4j
def test_neo4j_answers_a_cypher_query(neo4j_client):
    assert neo4j_client.read("RETURN 1 AS one") == [{"one": 1}]


@pytest.mark.neo4j
def test_neo4j_parameterized_query(neo4j_client):
    rows = neo4j_client.read("UNWIND $values AS v RETURN sum(v) AS total", {"values": [1, 2, 3]})
    assert rows == [{"total": 6}]


@pytest.mark.containers
def test_api_is_healthy_through_nginx(stack_url):
    with urllib.request.urlopen(f"{stack_url}/health/ready", timeout=5) as response:
        body = json.load(response)
    assert response.status == 200
    assert body["neo4j"] == "up"


@pytest.mark.containers
def test_nginx_hides_its_version(stack_url):
    with urllib.request.urlopen(f"{stack_url}/health", timeout=5) as response:
        assert response.headers.get("Server") == "nginx"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
