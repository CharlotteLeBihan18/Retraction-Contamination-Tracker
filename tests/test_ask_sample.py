"""The /ask templates on the hand-written sample graph (real Neo4j).

The expected values were worked out by hand from data/sample/sample.cypher, so these
tests check that each template returns what a human reading the graph would expect.
Skipped when Neo4j is not running.
"""

import pytest

from app.agent.ask import build_template
from app.agent.guardrails import run_validated
from scripts.load_sample import SAMPLE_FILE, read_statements

pytestmark = pytest.mark.neo4j

R001, R002, R003 = "10.0000/sample.001", "10.0000/sample.002", "10.0000/sample.003"


@pytest.fixture(scope="module")
def sample_db(neo4j_client):
    """Load the sample graph once for this file (the file cleans up its own previous copy)."""
    for statement in read_statements(SAMPLE_FILE):
        neo4j_client.write(statement)
    return neo4j_client


def run(db, intent):
    """Build the template for an intent and run it through the guardrails."""
    cypher, params = build_template(intent)
    return run_validated(db, cypher, params)


def test_exposure_needs_three_steps_to_reach_the_retracted_trial(sample_db):
    # sample.023 -> 021 -> 011/012 -> 001: nothing at depth 2, the trial at depth 3.
    assert run(sample_db, {"intent": "exposure", "doi": "10.0000/sample.023", "depth": 2}) == []
    rows = run(sample_db, {"intent": "exposure", "doi": "10.0000/sample.023", "depth": 3})
    assert [(r["doi"], r["hops"]) for r in rows] == [(R001, 3)]


def test_contamination_of_the_fluoride_trial(sample_db):
    rows = run(sample_db, {"intent": "contamination", "doi": R001, "depth": 2})
    hops = {r["doi"]: r["hops"] for r in rows}
    direct = {"10.0000/sample.011", "10.0000/sample.012", "10.0000/sample.013", "10.0000/sample.014",
              "10.0000/sample.025"}
    assert {d for d, h in hops.items() if h == 1} == direct
    assert {d for d, h in hops.items() if h == 2} == {"10.0000/sample.021", "10.0000/sample.022"}


def test_most_cited_retracted_papers(sample_db):
    rows = run(sample_db, {"intent": "most_cited_retracted"})
    counts = {r["doi"]: r["citers"] for r in rows if r["doi"].startswith("10.0000/")}
    assert counts == {R001: 5, R002: 3, R003: 2}


def test_retraction_reasons(sample_db):
    rows = run(sample_db, {"intent": "retraction_reasons"})
    reasons = {r["reason"]: r["retractions"] for r in rows}
    assert reasons["Fabrication"] >= 2  # 001 and 002 (the real data may add more)
    assert {"Image manipulation", "Plagiarism"} <= set(reasons)


def test_paper_info_merges_two_notices(sample_db):
    # sample.003 has a Retraction Watch notice (Plagiarism) and an OpenAlex one with no reason.
    [row] = run(sample_db, {"intent": "paper_info", "doi": R003})
    assert row["retracted"] is True and row["reasons"] == ["Plagiarism"]
    assert row["journal"] == "International Dental Reports"
