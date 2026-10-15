"""Tests for GET /api/communities (Louvain)."""

import pytest

from app.services import gds
from app.services.communities import contaminated_communities

URL = "/api/communities"
INFO_ROW = {"graphName": "citations", "nodeCount": 8, "relationshipCount": 21}


def community(community_id, share):
    """A community row as the Cypher query returns it."""
    return {
        "community_id": community_id,
        "size": 4,
        "retracted_papers": int(share * 4),
        "retracted_share": share,
        "retracted_examples": ["R1"] if share else [],
        "sample_titles": ["A", "B", "C"],
    }


# ---------- API tests (fake database) ----------


def test_returns_communities_with_graph_size(client, fake_db):
    fake_db.responses = [[{"exists": True}], [INFO_ROW], [community(0, 0.25), community(1, 0.0)]]
    body = client.get(URL).json()
    assert body["total"] == 2
    assert body["graph"] == {"papers": 8, "links": 21}
    assert body["items"][0]["retracted_share"] == 0.25


def test_paginates_after_running_louvain_once(client, fake_db):
    fake_db.responses = [[{"exists": True}], [INFO_ROW], [community(i, 0.5) for i in range(5)]]
    body = client.get(URL, params={"limit": 2, "offset": 2}).json()
    assert body["total"] == 5
    assert [c["community_id"] for c in body["items"]] == [2, 3]
    louvain_calls = [q for q, _ in fake_db.queries if "gds.louvain.stream" in q]
    assert len(louvain_calls) == 1


def test_sends_min_size_and_undirected_relationships(client, fake_db):
    fake_db.responses = [[{"exists": True}], [INFO_ROW], []]
    client.get(URL, params={"min_size": 5})
    _, params = fake_db.queries[-1]
    assert params == {"graph": "citations", "relationship": "CITES_UNDIRECTED", "min_size": 5}


def test_returns_503_when_no_data_is_loaded(client, monkeypatch):
    def not_ready(*_args, **_kwargs):
        raise gds.GraphNotReadyError("No papers to analyse yet: load the data first")

    monkeypatch.setattr("app.services.communities.ensure_projection", not_ready)
    assert client.get(URL).status_code == 503


@pytest.mark.parametrize("params", [{"limit": 0}, {"offset": -1}, {"min_size": 0}])
def test_rejects_invalid_parameters_with_422(client, params):
    assert client.get(URL, params=params).status_code == 422


# ---------- Real Neo4j + GDS ----------

# Two tight groups of 4 papers, each fully connected by citations, joined by a
# single citation (A3 -> B0). Group A contains one retracted paper, group B none.
# Louvain should find the two groups, and rank group A first.
# Separate statements on purpose: in a single query, each clause runs once per
# row coming out of the previous one, which would create the links many times.
TEST_GRAPH = [
    """
    UNWIND range(0, 3) AS i
    CREATE (:Paper:LvTest {title: 'LV A' + i, grp: 'A'}), (:Paper:LvTest {title: 'LV B' + i, grp: 'B'})
    """,
    """
    MATCH (x:LvTest), (y:LvTest)
    WHERE x.grp = y.grp AND x.title < y.title
    CREATE (x)-[:CITES]->(y)
    """,
    "MATCH (a3:LvTest {title: 'LV A3'}), (b0:LvTest {title: 'LV B0'}) CREATE (a3)-[:CITES]->(b0)",
    "MATCH (r:LvTest {title: 'LV A0'}) SET r:Retracted",
]
TEST_NAME = "test_louvain"


@pytest.fixture
def lv_graph(neo4j_client):
    """Load the two test groups, yield the client, then remove papers and projection."""
    neo4j_client.write("MATCH (n:LvTest) DETACH DELETE n")
    gds.drop_projection(neo4j_client, TEST_NAME)
    for statement in TEST_GRAPH:
        neo4j_client.write(statement)
    links = neo4j_client.read("MATCH (:LvTest)-[c:CITES]->(:LvTest) RETURN count(c) AS links")
    assert links == [{"links": 13}], "test graph should have 6 + 6 links inside the groups and 1 between them"
    yield neo4j_client
    gds.drop_projection(neo4j_client, TEST_NAME)
    neo4j_client.write("MATCH (n:LvTest) DETACH DELETE n")


@pytest.mark.neo4j
def test_louvain_finds_two_groups_and_ranks_the_contaminated_one_first(lv_graph):
    info, total, rows = contaminated_communities(
        lv_graph, limit=10, offset=0, min_size=1, graph=TEST_NAME, label="LvTest"
    )
    assert info.node_count == 8
    assert total == 2
    first, second = rows
    assert first["size"] == 4 and first["retracted_papers"] == 1
    assert first["retracted_share"] == pytest.approx(0.25)
    assert first["retracted_examples"] == ["LV A0"]
    assert second["retracted_papers"] == 0


@pytest.mark.neo4j
def test_min_size_hides_small_communities(lv_graph):
    _, total, _ = contaminated_communities(lv_graph, limit=10, offset=0, min_size=5, graph=TEST_NAME, label="LvTest")
    assert total == 0
