"""Tests for GET /api/journals/zombie-citations.

API tests use the fake database. Cypher tests load a tiny graph into the real
Neo4j (marked ``neo4j``, skipped when it is not running) and delete it afterwards.
"""

import pytest
from neo4j.exceptions import ServiceUnavailable

from app.services.journals import zombie_journals

URL = "/api/journals/zombie-citations"

EXAMPLE_ROW = {
    "journal": "Journal A",
    "zombie_citations": 2,
    "citations_to_retracted": 3,
    "zombie_share": 0.667,
    "examples": [{"citing_title": "P2", "citing_year": 2022, "retracted_title": "R1"}],
}


# ---------- API tests (fake database) ----------


def test_returns_a_page_of_journals(client, fake_db):
    fake_db.responses = [[{"total": 1}], [EXAMPLE_ROW]]
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["journal"] == "Journal A"
    assert body["items"][0]["zombie_share"] == 0.667


def test_passes_pagination_as_query_parameters(client, fake_db):
    fake_db.responses = [[{"total": 50}], []]
    client.get(URL, params={"limit": 5, "offset": 10, "min_zombie": 3})
    _, params = fake_db.queries[-1]
    assert params == {"limit": 5, "offset": 10, "min_zombie": 3}


def test_skips_page_query_when_offset_is_past_the_end(client, fake_db):
    fake_db.responses = [[{"total": 2}]]
    body = client.get(URL, params={"offset": 10}).json()
    assert body["items"] == []
    assert len(fake_db.queries) == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"min_zombie": 0}])
def test_rejects_invalid_parameters_with_422(client, params):
    assert client.get(URL, params=params).status_code == 422


def test_returns_503_when_neo4j_is_down(client, fake_db):
    def unavailable(*_args, **_kwargs):
        raise ServiceUnavailable("down")

    fake_db.read = unavailable
    response = client.get(URL)
    assert response.status_code == 503
    assert "not reachable" in response.json()["detail"]


# ---------- Cypher tests (real Neo4j) ----------

# Retracted paper R1 is cited 5 times:
#   Journal A: P1 before retraction, P2 and P3 after  -> 2 zombie out of 3
#   Journal B: P4 after, P5 before                    -> 1 zombie out of 2
# Journal C only cites a normal paper, so it must not appear.
TEST_GRAPH = """
CREATE (r:Paper:Retracted:TestData {title: 'TEST R1', year: 2015})
CREATE (n:Paper:TestData {title: 'TEST normal paper', year: 2015})
CREATE (ja:Journal:TestData {name: 'TEST Journal A'})
CREATE (jb:Journal:TestData {name: 'TEST Journal B'})
CREATE (jc:Journal:TestData {name: 'TEST Journal C'})
CREATE (p1:Paper:TestData {title: 'TEST P1', year: 2014})-[:PUBLISHED_IN]->(ja)
CREATE (p2:Paper:TestData {title: 'TEST P2', year: 2019})-[:PUBLISHED_IN]->(ja)
CREATE (p3:Paper:TestData {title: 'TEST P3', year: 2021})-[:PUBLISHED_IN]->(ja)
CREATE (p4:Paper:TestData {title: 'TEST P4', year: 2020})-[:PUBLISHED_IN]->(jb)
CREATE (p5:Paper:TestData {title: 'TEST P5', year: 2013})-[:PUBLISHED_IN]->(jb)
CREATE (p6:Paper:TestData {title: 'TEST P6', year: 2020})-[:PUBLISHED_IN]->(jc)
CREATE (p1)-[:CITES {before_retraction: true}]->(r)
CREATE (p2)-[:CITES {before_retraction: false}]->(r)
CREATE (p3)-[:CITES {before_retraction: false}]->(r)
CREATE (p4)-[:CITES {before_retraction: false}]->(r)
CREATE (p5)-[:CITES {before_retraction: true}]->(r)
CREATE (p6)-[:CITES]->(n)
"""


@pytest.fixture
def test_graph(neo4j_client):
    """Load the tiny test graph, then remove it whatever happens."""
    neo4j_client.write("MATCH (n:TestData) DETACH DELETE n")
    neo4j_client.write(TEST_GRAPH)
    yield neo4j_client
    neo4j_client.write("MATCH (n:TestData) DETACH DELETE n")


def _test_journals(db):
    _, rows = zombie_journals(db, limit=10_000, offset=0, min_zombie=1)
    return {row["journal"]: row for row in rows if row["journal"].startswith("TEST ")}


@pytest.mark.neo4j
def test_cypher_counts_zombie_citations_per_journal(test_graph):
    journals = _test_journals(test_graph)
    assert journals["TEST Journal A"]["zombie_citations"] == 2
    assert journals["TEST Journal A"]["citations_to_retracted"] == 3
    assert journals["TEST Journal A"]["zombie_share"] == pytest.approx(0.667)
    assert journals["TEST Journal B"]["zombie_citations"] == 1
    assert "TEST Journal C" not in journals


@pytest.mark.neo4j
def test_cypher_examples_are_zombie_citations_newest_first(test_graph):
    examples = _test_journals(test_graph)["TEST Journal A"]["examples"]
    assert [e["citing_title"] for e in examples] == ["TEST P3", "TEST P2"]
    assert all(e["retracted_title"] == "TEST R1" for e in examples)


@pytest.mark.neo4j
def test_cypher_min_zombie_filters_journals(test_graph):
    _, rows = zombie_journals(test_graph, limit=10_000, offset=0, min_zombie=2)
    names = {row["journal"] for row in rows}
    assert "TEST Journal A" in names
    assert "TEST Journal B" not in names
