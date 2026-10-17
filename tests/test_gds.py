"""Tests for the GDS projection helpers (app/services/gds.py)."""

import pytest
from neo4j.exceptions import ClientError

from app.services import gds

INFO_ROW = {"graphName": "citations", "nodeCount": 3, "relationshipCount": 6}


# ---------- Unit tests (fake database) ----------


def test_reuses_an_existing_projection(fake_db):
    fake_db.responses = [[{"exists": True}], [INFO_ROW]]
    info = gds.ensure_projection(fake_db)
    assert info == gds.ProjectionInfo("citations", 3, 6)
    assert not any("gds.graph.project" in query for query, _ in fake_db.queries)


def test_projects_when_missing(fake_db):
    fake_db.responses = [[{"exists": False}], [INFO_ROW]]
    gds.ensure_projection(fake_db)
    query, params = fake_db.queries[-1]
    assert "gds.graph.project" in query
    assert params == {"name": "citations", "label": "Paper"}


def test_refresh_drops_then_projects(fake_db):
    fake_db.responses = [[], [INFO_ROW]]
    gds.ensure_projection(fake_db, refresh=True)
    assert "gds.graph.drop" in fake_db.queries[0][0]
    assert "gds.graph.project" in fake_db.queries[1][0]


def test_empty_database_raises_graph_not_ready(fake_db):
    fake_db.responses = [[{"exists": False}]]

    def no_papers(*_args, **_kwargs):
        raise ClientError("Invalid node projection, one or more labels not found: 'Paper'")

    fake_db.write = no_papers
    with pytest.raises(gds.GraphNotReadyError):
        gds.ensure_projection(fake_db)


def test_empty_projection_is_dropped_not_cached(fake_db):
    empty = {"graphName": "citations", "nodeCount": 0, "relationshipCount": 0}
    fake_db.responses = [[{"exists": False}], [empty], []]
    with pytest.raises(gds.GraphNotReadyError):
        gds.ensure_projection(fake_db)
    assert "gds.graph.drop" in fake_db.queries[-1][0]


def test_cached_empty_projection_is_dropped(fake_db):
    empty = {"graphName": "citations", "nodeCount": 0, "relationshipCount": 0}
    fake_db.responses = [[{"exists": True}], [empty], []]
    with pytest.raises(gds.GraphNotReadyError):
        gds.ensure_projection(fake_db)
    assert "gds.graph.drop" in fake_db.queries[-1][0]


def test_reuses_projection_created_meanwhile_by_another_request(fake_db):
    fake_db.responses = [[{"exists": False}], [INFO_ROW]]

    def already_exists(*_args, **_kwargs):
        raise ClientError("A graph with name 'citations' already exists.")

    fake_db.write = already_exists
    assert gds.ensure_projection(fake_db) == gds.ProjectionInfo("citations", 3, 6)


def test_other_errors_are_not_hidden(fake_db):
    fake_db.responses = [[{"exists": False}]]

    def broken(*_args, **_kwargs):
        raise ClientError("Something else went wrong")

    fake_db.write = broken
    with pytest.raises(ClientError):
        gds.ensure_projection(fake_db)


@pytest.mark.neo4j
def test_label_with_no_papers_raises_and_leaves_no_projection(neo4j_client):
    # The label exists but has no nodes left (like Paper after the data was deleted):
    # GDS projects 0 nodes without error, and we must not keep that empty projection.
    name = "test_empty"
    gds.drop_projection(neo4j_client, name)
    neo4j_client.write("CREATE (:EmptyTest)")
    neo4j_client.write("MATCH (n:EmptyTest) DETACH DELETE n")
    with pytest.raises(gds.GraphNotReadyError):
        gds.ensure_projection(neo4j_client, name=name, label="EmptyTest")
    assert not gds.projection_exists(neo4j_client, name)


# ---------- Real Neo4j + GDS ----------

# Three test papers: A cites B, B cites C. Projected under a separate name and
# label so the real projection and real data are never touched.
TEST_GRAPH = """
CREATE (a:Paper:GdsTest {title: 'GDS A'})
CREATE (b:Paper:GdsTest {title: 'GDS B'})
CREATE (c:Paper:GdsTest {title: 'GDS C'})
CREATE (a)-[:CITES]->(b), (b)-[:CITES]->(c)
"""
TEST_NAME = "test_citations"


@pytest.fixture
def gds_graph(neo4j_client):
    """Load 3 test papers, yield the client, then remove papers and projection."""
    neo4j_client.write("MATCH (n:GdsTest) DETACH DELETE n")
    gds.drop_projection(neo4j_client, TEST_NAME)
    neo4j_client.write(TEST_GRAPH)
    yield neo4j_client
    gds.drop_projection(neo4j_client, TEST_NAME)
    neo4j_client.write("MATCH (n:GdsTest) DETACH DELETE n")


@pytest.mark.neo4j
def test_projection_counts_papers_and_both_orientations(gds_graph):
    info = gds.ensure_projection(gds_graph, name=TEST_NAME, label="GdsTest")
    assert info.node_count == 3
    # 2 directed CITES + 4 undirected (each link counted both ways) = 6
    assert info.relationship_count == 6
    assert gds.projection_exists(gds_graph, TEST_NAME)


@pytest.mark.neo4j
def test_second_call_reuses_projection_and_drop_removes_it(gds_graph):
    first = gds.ensure_projection(gds_graph, name=TEST_NAME, label="GdsTest")
    second = gds.ensure_projection(gds_graph, name=TEST_NAME, label="GdsTest")
    assert first == second
    gds.drop_projection(gds_graph, TEST_NAME)
    assert not gds.projection_exists(gds_graph, TEST_NAME)
