"""Tests for GET /api/papers/{paper_id}/contamination.

API tests use the fake database. Cypher tests load a tiny graph into the real
Neo4j (marked ``neo4j``, skipped when it is not running) and delete it afterwards.
"""

import pytest
from neo4j.exceptions import ServiceUnavailable

from app.services import papers

RETRACTED = {"doi": "10.1/r", "openalex_id": "W1", "title": "R", "year": 2015, "retracted": True}
SUMMARY = {"total": 3, "direct_citations": 2, "after_retraction": 1}
ITEMS = [
    {"doi": "10.1/a", "openalex_id": "W2", "title": "A", "year": 2014, "hops": 1, "before_retraction": True},
    {"doi": None, "openalex_id": "W3", "title": "B", "year": 2020, "hops": 1, "before_retraction": False},
    {"doi": "10.1/c", "openalex_id": "W4", "title": "C", "year": 2021, "hops": 2, "before_retraction": None},
]


def url(paper_id: str) -> str:
    """The contamination URL for a paper id (a DOI keeps its slash, as a user would type it)."""
    return f"/api/papers/{paper_id}/contamination"


# ---------- API tests (fake database) ----------


def test_returns_the_retracted_paper_its_counts_and_a_page(client, fake_db):
    fake_db.responses = [[RETRACTED], [SUMMARY], ITEMS]
    response = client.get(url("10.1/r"))
    assert response.status_code == 200
    body = response.json()
    assert body["paper"]["doi"] == "10.1/r"
    assert (body["total"], body["direct_citations"], body["after_retraction"]) == (3, 2, 1)
    assert [item["hops"] for item in body["items"]] == [1, 1, 2]
    assert body["items"][1]["before_retraction"] is False
    assert body["items"][2]["before_retraction"] is None
    assert "not proven wrong" in body["note"]


def test_a_doi_with_slashes_and_a_prefix_is_found(client, fake_db):
    fake_db.responses = [[RETRACTED], [SUMMARY], ITEMS]
    assert client.get(url("https://doi.org/10.1/R")).status_code == 200
    query, params = fake_db.queries[0]
    assert "{doi: $value}" in query
    assert params == {"value": "10.1/r"}


def test_an_openalex_id_is_looked_up_by_openalex_id(client, fake_db):
    fake_db.responses = [[RETRACTED], [SUMMARY], ITEMS]
    assert client.get(url("w1")).status_code == 200
    query, params = fake_db.queries[0]
    assert "{openalex_id: $value}" in query
    assert params == {"value": "W1"}


def test_the_traversal_uses_the_doi_stored_in_the_graph(client, fake_db):
    fake_db.responses = [[RETRACTED], [SUMMARY], ITEMS]
    client.get(url("W1"), params={"limit": 5, "offset": 0})
    assert fake_db.queries[1][1] == {"doi": "10.1/r"}
    assert fake_db.queries[2][1] == {"doi": "10.1/r", "limit": 5, "offset": 0}


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_depth_sets_the_path_length(client, fake_db, depth):
    fake_db.responses = [[RETRACTED], [SUMMARY], ITEMS]
    client.get(url("10.1/r"), params={"depth": depth})
    assert f"[:CITES*1..{depth}]" in fake_db.queries[1][0]
    assert f"[:CITES*1..{depth}]" in fake_db.queries[2][0]


def test_unknown_paper_gives_404(client, fake_db):
    fake_db.responses = [[]]
    response = client.get(url("10.1/unknown"))
    assert response.status_code == 404
    assert "No paper" in response.json()["detail"]


def test_paper_that_is_not_retracted_gives_404(client, fake_db):
    fake_db.responses = [[{**RETRACTED, "retracted": False}]]
    response = client.get(url("10.1/r"))
    assert response.status_code == 404
    assert "not retracted" in response.json()["detail"]
    assert len(fake_db.queries) == 1


def test_skips_the_page_query_when_offset_is_past_the_end(client, fake_db):
    fake_db.responses = [[RETRACTED], [SUMMARY]]
    body = client.get(url("10.1/r"), params={"offset": 10}).json()
    assert body["items"] == []
    assert len(fake_db.queries) == 2


@pytest.mark.parametrize("params", [{"depth": 0}, {"depth": 4}, {"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_rejects_invalid_parameters_with_422(client, params):
    assert client.get(url("10.1/r"), params=params).status_code == 422


def test_returns_503_when_neo4j_is_down(client, fake_db):
    def unavailable(*_args, **_kwargs):
        raise ServiceUnavailable("down")

    fake_db.read = unavailable
    assert client.get(url("10.1/r")).status_code == 503


# ---------- Pure functions ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("W123", ("openalex_id", "W123")),
        ("https://openalex.org/w123", ("openalex_id", "W123")),
        ("10.1016/J.CELL.2020.01.001", ("doi", "10.1016/j.cell.2020.01.001")),
        ("https://doi.org/10.1/x", ("doi", "10.1/x")),
        ("doi:10.1/x", ("doi", "10.1/x")),
        (" 10.1/x ", ("doi", "10.1/x")),
    ],
)
def test_parse_paper_id(raw, expected):
    assert papers.parse_paper_id(raw) == expected


@pytest.mark.parametrize("depth", [0, 4, "2"])
def test_contamination_queries_refuse_a_depth_out_of_range(depth):
    with pytest.raises(ValueError):
        papers.contamination_queries(depth)


def test_queries_read_the_doi_from_a_parameter():
    """User input never goes into the query text, only the checked depth does."""
    for query in papers.contamination_queries(2):
        assert "{doi: $doi}" in query
        assert "RELIES_ON" not in query  # facts only, never the AI hypothesis


# ---------- Cypher tests (real Neo4j) ----------

# R is retracted in 2019. A cites R before (2014), B after (2021): a zombie citation.
# C cites A (2 hops), D cites C (3 hops). E cites R and A: it must appear once, at 1 hop.
# F cites a normal paper N only, so it must never appear.
TEST_GRAPH = """
CREATE (r:Paper:Retracted:TestData {doi: '10.0000/test-contamination.r', title: 'TEST R', year: 2015})
CREATE (n:Paper:TestData {doi: '10.0000/test-contamination.n', title: 'TEST N', year: 2015})
CREATE (a:Paper:TestData {doi: '10.0000/test-contamination.a', title: 'TEST A', year: 2014})
CREATE (b:Paper:TestData {openalex_id: 'W-test-contamination-b', title: 'TEST B', year: 2021})
CREATE (c:Paper:TestData {doi: '10.0000/test-contamination.c', title: 'TEST C', year: 2016})
CREATE (d:Paper:TestData {doi: '10.0000/test-contamination.d', title: 'TEST D', year: 2018})
CREATE (e:Paper:TestData {doi: '10.0000/test-contamination.e', title: 'TEST E', year: 2017})
CREATE (f:Paper:TestData {doi: '10.0000/test-contamination.f', title: 'TEST F', year: 2020})
CREATE (a)-[:CITES {before_retraction: true}]->(r)
CREATE (b)-[:CITES {before_retraction: false}]->(r)
CREATE (e)-[:CITES {before_retraction: true}]->(r)
CREATE (c)-[:CITES]->(a)
CREATE (e)-[:CITES]->(a)
CREATE (d)-[:CITES]->(c)
CREATE (f)-[:CITES]->(n)
"""
TEST_DOI = "10.0000/test-contamination.r"


@pytest.fixture
def test_graph(neo4j_client):
    """Load the tiny test graph, then remove it whatever happens."""
    neo4j_client.write("MATCH (n:TestData) DETACH DELETE n")
    neo4j_client.write(TEST_GRAPH)
    yield neo4j_client
    neo4j_client.write("MATCH (n:TestData) DETACH DELETE n")


def _titles_and_hops(db, depth, limit=100, offset=0):
    summary, items = papers.contamination(db, TEST_DOI, depth=depth, limit=limit, offset=offset)
    return summary, [(item["title"], item["hops"], item["before_retraction"]) for item in items]


@pytest.mark.neo4j
def test_cypher_depth_1_lists_direct_citations_with_their_flag(test_graph):
    summary, rows = _titles_and_hops(test_graph, depth=1)
    assert summary == {"total": 3, "direct_citations": 3, "after_retraction": 1}
    assert rows == [("TEST B", 1, False), ("TEST E", 1, True), ("TEST A", 1, True)]  # newest first


@pytest.mark.neo4j
def test_cypher_deeper_steps_add_indirect_papers_once(test_graph):
    summary, rows = _titles_and_hops(test_graph, depth=3)
    assert summary == {"total": 5, "direct_citations": 3, "after_retraction": 1}
    assert rows[3:] == [("TEST C", 2, None), ("TEST D", 3, None)]
    assert [title for title, _, _ in rows].count("TEST E") == 1
    assert "TEST F" not in {title for title, _, _ in rows}


@pytest.mark.neo4j
def test_cypher_pages_do_not_overlap(test_graph):
    _, first = _titles_and_hops(test_graph, depth=3, limit=2, offset=0)
    _, second = _titles_and_hops(test_graph, depth=3, limit=2, offset=2)
    assert len(first) == 2 and len(second) == 2
    assert not {row[0] for row in first} & {row[0] for row in second}


@pytest.mark.neo4j
def test_cypher_find_paper_by_doi_and_by_openalex_id(test_graph):
    assert papers.find_paper(test_graph, "https://doi.org/10.0000/TEST-contamination.r")["retracted"] is True
    assert papers.find_paper(test_graph, "W-test-contamination-b") is None  # not a W + digits id: read as DOI
    assert papers.find_paper(test_graph, "10.0000/test-contamination.a")["retracted"] is False
