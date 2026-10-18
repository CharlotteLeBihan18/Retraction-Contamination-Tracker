"""Tests for the hand-written sample graph (data/sample/sample.cypher)."""

import pytest

from app.config import Settings
from app.services.gds import DROP_QUERY
from scripts.load_sample import SAMPLE_FILE, load_sample, open_driver, read_statements


def test_sample_file_is_split_into_statements():
    statements = read_statements(SAMPLE_FILE)
    assert len(statements) >= 10
    assert not any(statement.startswith("//") for statement in statements)


class RecordingDriver:
    """Stand-in for a Neo4j driver that remembers the queries it was given."""

    def __init__(self):
        self.queries = []

    def execute_query(self, query, parameters=None, database_=None):
        self.queries.append(query)


def test_load_sample_drops_the_gds_graph_after_the_data(tmp_path):
    """The projection is dropped last, so Top spreaders and Communities rebuild it with the sample."""
    sample = tmp_path / "tiny.cypher"
    sample.write_text("CREATE (:Paper {doi: 'x'});\nCREATE (:Paper {doi: 'y'});\n", encoding="utf-8")
    driver = RecordingDriver()
    assert load_sample(driver, "neo4j", sample) == 2
    assert len(driver.queries) == 3
    assert driver.queries[-1] == DROP_QUERY


def test_sample_file_never_flags_reasons_as_sample():
    """Flagging a Reason would let the clean-up delete a real reason that has the same name."""
    text = SAMPLE_FILE.read_text(encoding="utf-8")
    assert "r.sample" not in text


@pytest.fixture(scope="module")
def sample_db(neo4j_client):
    """Load the sample graph into the real Neo4j and leave it there.

    The tests only read, and loading is safe to repeat. Keeping the sample means that running
    ``make test`` does not empty the database the endpoints are using.
    """
    settings = Settings()
    driver = open_driver(settings)
    load_sample(driver, settings.neo4j_database)
    driver.close()
    return neo4j_client


@pytest.mark.neo4j
def test_sample_has_the_expected_nodes(sample_db):
    rows = sample_db.read("MATCH (n {sample: true}) UNWIND labels(n) AS label RETURN label, count(*) AS n")
    counts = {row["label"]: row["n"] for row in rows}
    assert counts == {"Author": 15, "Journal": 4, "Paper": 20, "Retracted": 3, "RetractionNotice": 4}


@pytest.mark.neo4j
def test_sample_reasons_are_shared_by_name_and_not_flagged(sample_db):
    """Reasons are matched by name, so they carry no sample flag (a real reason must never be deleted)."""
    rows = sample_db.read(
        "MATCH (:RetractionNotice {sample: true})-[:FOR_REASON]->(r:Reason) "
        "RETURN count(DISTINCT r) AS n, count(r.sample) AS flagged"
    )
    assert rows[0] == {"n": 3, "flagged": 0}


@pytest.mark.neo4j
def test_sample_citations_to_retracted_papers_are_flagged(sample_db):
    rows = sample_db.read(
        "MATCH (:Paper {sample: true})-[c:CITES]->(:Retracted) RETURN c.before_retraction AS before, count(*) AS n"
    )
    assert {row["before"]: row["n"] for row in rows} == {True: 4, False: 6}


@pytest.mark.neo4j
def test_sample_has_a_chain_of_three_steps(sample_db):
    within_2 = {
        row["doi"]
        for row in sample_db.read(
            "MATCH (p:Paper {sample: true})-[:CITES*1..2]->(:Retracted) RETURN DISTINCT p.doi AS doi"
        )
    }
    within_3 = {
        row["doi"]
        for row in sample_db.read(
            "MATCH (p:Paper {sample: true})-[:CITES*1..3]->(:Retracted) RETURN DISTINCT p.doi AS doi"
        )
    }
    assert len(within_2) == 12
    assert within_3 - within_2 == {"10.0000/sample.023"}
    assert not any(doi.startswith("10.0000/sample.03") for doi in within_3)


@pytest.mark.neo4j
def test_a_paper_can_have_notices_from_two_sources(sample_db):
    rows = sample_db.read(
        "MATCH (n:RetractionNotice {sample: true})-[:RETRACTS]->(:Paper {doi: $doi}) RETURN n.source AS source",
        {"doi": "10.0000/sample.003"},
    )
    assert sorted(row["source"] for row in rows) == ["OpenAlex", "Retraction Watch"]