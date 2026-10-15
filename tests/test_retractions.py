"""Tests for GET /api/retractions/top-spreaders (PageRank)."""

import pytest

from app.services import gds
from app.services.retractions import top_spreaders

URL = "/api/retractions/top-spreaders"
INFO_ROW = {"graphName": "citations", "nodeCount": 5, "relationshipCount": 12}
PAPER_ROW = {
    "paper_id": "W1",
    "doi": "10.1/x",
    "title": "R1",
    "year": 2015,
    "pagerank": 0.42,
    "direct_citations": 2,
    "citations_after_retraction": 1,
}


# ---------- API tests (fake database) ----------


def test_returns_ranked_retracted_papers(client, fake_db):
    # exists?, projection info, count, page
    fake_db.responses = [[{"exists": True}], [INFO_ROW], [{"total": 1}], [PAPER_ROW]]
    body = client.get(URL).json()
    assert body["total"] == 1
    assert body["graph"] == {"papers": 5, "links": 12}
    assert body["items"][0]["pagerank"] == 0.42


def test_uses_the_directed_relationships_and_pagination(client, fake_db):
    fake_db.responses = [[{"exists": True}], [INFO_ROW], [{"total": 30}], []]
    client.get(URL, params={"limit": 5, "offset": 10})
    query, params = fake_db.queries[-1]
    assert "gds.pageRank.stream" in query
    assert params == {"graph": "citations", "relationship": "CITES", "limit": 5, "offset": 10}


def test_returns_503_when_no_data_is_loaded(client, monkeypatch):
    def not_ready(*_args, **_kwargs):
        raise gds.GraphNotReadyError("No papers to analyse yet: load the data first")

    monkeypatch.setattr("app.services.retractions.ensure_projection", not_ready)
    response = client.get(URL)
    assert response.status_code == 503
    assert "load the data" in response.json()["detail"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_rejects_invalid_parameters_with_422(client, params):
    assert client.get(URL, params=params).status_code == 422


# ---------- Real Neo4j + GDS ----------

# R1 (retracted) is cited by A and B; A is itself cited by C.
# R2 (retracted) is cited by nobody. N is a normal paper cited by A.
# So R1 must rank above R2, and N must not appear (not retracted).
TEST_GRAPH = """
CREATE (r1:Paper:Retracted:PrTest {title: 'PR R1', year: 2015})
CREATE (r2:Paper:Retracted:PrTest {title: 'PR R2', year: 2016})
CREATE (n:Paper:PrTest {title: 'PR N', year: 2014})
CREATE (a:Paper:PrTest {title: 'PR A', year: 2014})
CREATE (b:Paper:PrTest {title: 'PR B', year: 2019})
CREATE (c:Paper:PrTest {title: 'PR C', year: 2020})
CREATE (a)-[:CITES {before_retraction: true}]->(r1)
CREATE (b)-[:CITES {before_retraction: false}]->(r1)
CREATE (a)-[:CITES]->(n)
CREATE (c)-[:CITES]->(a)
"""
TEST_NAME = "test_pagerank"


@pytest.fixture
def pr_graph(neo4j_client):
    """Load the test papers, yield the client, then remove papers and projection."""
    neo4j_client.write("MATCH (n:PrTest) DETACH DELETE n")
    gds.drop_projection(neo4j_client, TEST_NAME)
    neo4j_client.write(TEST_GRAPH)
    yield neo4j_client
    gds.drop_projection(neo4j_client, TEST_NAME)
    neo4j_client.write("MATCH (n:PrTest) DETACH DELETE n")


@pytest.mark.neo4j
def test_pagerank_ranks_the_cited_retraction_first(pr_graph):
    info, _, rows = top_spreaders(pr_graph, limit=100, offset=0, graph=TEST_NAME, label="PrTest")
    titles = [row["title"] for row in rows]
    assert info.node_count == 6
    assert titles.index("PR R1") < titles.index("PR R2")
    assert "PR N" not in titles


@pytest.mark.neo4j
def test_counts_direct_and_after_retraction_citations(pr_graph):
    _, _, rows = top_spreaders(pr_graph, limit=100, offset=0, graph=TEST_NAME, label="PrTest")
    r1 = next(row for row in rows if row["title"] == "PR R1")
    assert r1["direct_citations"] == 2
    assert r1["citations_after_retraction"] == 1
    assert r1["pagerank"] > 0
