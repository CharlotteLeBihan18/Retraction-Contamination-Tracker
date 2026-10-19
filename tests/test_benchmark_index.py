"""Tests for scripts/benchmark_index.py.

The plan helpers and the drop / restore order are tested with a fake runner. One test
uses the real Neo4j (marked ``neo4j``) on its own throwaway label and index, so it never
touches the indexes of the real graph.
"""

import pytest

from app.config import Settings
from scripts import benchmark_index as bench

# A PROFILE plan shaped like the driver returns it (each operator reports its own hits).
PLAN = {
    "operatorType": "ProduceResults@neo4j",
    "dbHits": 0,
    "rows": 1,
    "children": [
        {
            "operatorType": "Projection@neo4j",
            "dbHits": 2,
            "children": [
                {
                    "operatorType": "NodeUniqueIndexSeek@neo4j",
                    "dbHits": 3,
                    "args": {"Details": "UNIQUE p:Paper(doi) WHERE doi = $doi"},
                    "children": [],
                }
            ],
        }
    ],
}


class FakeRunner:
    """Records every statement; can be told to fail when the index is gone."""

    def __init__(self, fail_without_index: bool = False) -> None:
        self.statements: list[str] = []
        self.fail_without_index = fail_without_index
        self.index_dropped = False

    def write(self, query: str) -> None:
        self.statements.append(query)
        if query.startswith("DROP"):
            self.index_dropped = True
        elif query.startswith("CREATE"):
            self.index_dropped = False

    def profile(self, query, params):  # pylint: disable=unused-argument
        if self.fail_without_index and self.index_dropped:
            raise RuntimeError("database went away")
        return PLAN

    def time_ms(self, query, params):  # pylint: disable=unused-argument
        return 1.0


CASE = bench.IndexCase(
    name="test index",
    create="CREATE INDEX test IF NOT EXISTS FOR (n:X) ON (n.k)",
    drop="DROP INDEX test IF EXISTS",
    queries={"q": "MATCH (n:X {k: $doi}) RETURN n"},
    param="doi",
)


def test_total_db_hits_adds_every_operator():
    assert bench.total_db_hits(PLAN) == 5


def test_operators_are_listed_from_the_leaves_up_without_suffix():
    assert bench.operators(PLAN) == [
        "NodeUniqueIndexSeek (UNIQUE p:Paper(doi) WHERE doi = $doi)",
        "Projection",
        "ProduceResults",
    ]


def test_run_case_measures_before_then_after_and_restores_the_index():
    runner = FakeRunner()
    result = bench.run_case(runner, CASE, {"doi": "10.1/x"})
    assert [m.with_index for m in result.measures] == [False, True]
    assert runner.statements[0] == CASE.drop
    assert runner.statements[-2:] == [CASE.create, "CALL db.awaitIndexes(300)"]  # always put back
    assert result.measures[0].db_hits == 5 and result.measures[0].rows == 1


def test_index_is_restored_even_when_a_measure_fails():
    runner = FakeRunner(fail_without_index=True)  # fails during the "before" measures
    with pytest.raises(RuntimeError):
        bench.run_case(runner, CASE, {"doi": "10.1/x"})
    assert runner.statements[-2] == CASE.create
    assert runner.index_dropped is False


def test_markdown_reports_the_speed_up():
    measures = [
        bench.Measure("Find a paper", False, 20_000, 1, ["NodeByLabelScan", "Filter"], 9.0),
        bench.Measure("Find a paper", True, 4, 1, ["NodeUniqueIndexSeek"], 1.5),
    ]
    params = {"doi": "10.1/x", "openalex_id": "W1", "year": 2020}
    report = bench.to_markdown([bench.CaseResult("paper_doi", measures, note="Why it differs.")], params, 9354)
    assert "| Find a paper | before (without) | NodeByLabelScan → Filter | 20,000 | 9.0 ms |" in report
    assert "5,000x fewer database hits" in report
    assert "9354 papers" in report and "`W1`" in report
    assert report.index("5,000x") < report.index("Why it differs.")  # the note comes under the table


def test_run_case_keeps_the_note_of_the_case():
    case = bench.IndexCase(CASE.name, CASE.create, CASE.drop, CASE.queries, CASE.param, note="a note")
    assert bench.run_case(FakeRunner(), case, {"doi": "10.1/x"}).note == "a note"


def test_the_openalex_id_constraint_is_benchmarked_like_the_loader_creates_it():
    from scripts.ingest import SCHEMA_STATEMENTS  # pylint: disable=import-outside-toplevel

    case = next(c for c in bench.CASES if c.param == "openalex_id")
    assert case.create in SCHEMA_STATEMENTS


def test_every_case_drops_and_creates_the_same_index():
    for case in bench.CASES:
        name = case.drop.split()[2]
        assert name in case.create and "IF NOT EXISTS" in case.create and "IF EXISTS" in case.drop
        assert all(f"${case.param}" in query for query in case.queries.values())


@pytest.mark.neo4j
def test_index_turns_a_scan_into_a_seek_on_a_throwaway_label(neo4j_client):
    """Real Neo4j: 500 nodes of a test-only label, with and without a test-only index."""
    settings = Settings()
    runner = bench.Neo4jRunner(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password.get_secret_value(), settings.neo4j_database
    )
    case = bench.IndexCase(
        name="bench test",
        create="CREATE INDEX bench_test_key IF NOT EXISTS FOR (n:BenchTest) ON (n.key)",
        drop="DROP INDEX bench_test_key IF EXISTS",
        queries={"lookup": "MATCH (n:BenchTest {key: $doi}) RETURN n.key AS key"},
        param="doi",
    )
    neo4j_client.write("UNWIND range(1, 500) AS i CREATE (:BenchTest {key: 'k' + toString(i)})")
    try:
        result = bench.run_case(runner, case, {"doi": "k250"})
    finally:
        runner.write(case.drop)
        neo4j_client.write("MATCH (n:BenchTest) DETACH DELETE n")
        runner.close()
    without_index, with_index = result.measures
    assert with_index.operators[0].startswith("NodeIndexSeek")
    assert not without_index.operators[0].startswith("NodeIndexSeek")
    assert without_index.db_hits > 10 * with_index.db_hits
