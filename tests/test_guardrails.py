"""Tests for the Cypher guardrails (app/agent/guardrails.py). No database needed."""

import pytest
from neo4j.exceptions import ClientError

from app.agent import guardrails
from app.agent.guardrails import GuardrailError, run_validated, uses_hypotheses, validate

# ---------- Queries that must pass ----------

SAFE_QUERIES = [
    "MATCH (p:Paper {doi: $doi}) RETURN p.title",
    "MATCH (p:Paper:Retracted) RETURN count(p) AS total",
    "MATCH (c:Paper)-[:CITES*1..2]->(r:Paper:Retracted) RETURN c.title LIMIT 20",
    "MATCH (n:RetractionNotice)-[:FOR_REASON]->(r:Reason) RETURN r.name, count(n) AS n",
    "MATCH (p:Paper) WHERE p.title CONTAINS 'set theory' RETURN p.title",
    "MATCH (a:Author)-[:AUTHORED]->(p:Paper)-[:PUBLISHED_IN]->(j:Journal) RETURN a.name, j.name",
    "MATCH (p:Paper) WHERE p:Retracted RETURN p.doi",
    "MATCH path = shortestPath((a:Paper)-[:CITES*..3]->(b:Paper:Retracted)) RETURN path",
    "CALL db.index.vector.queryNodes('abstracts', 5, $vector) YIELD node RETURN node.title",
    "MATCH (p:Paper) RETURN p.title // delete this later",
]


@pytest.mark.parametrize("query", SAFE_QUERIES)
def test_safe_queries_pass(query):
    assert "LIMIT" in validate(query)


# ---------- Queries that must be refused, with the right reason ----------

REFUSED_QUERIES = [
    ("MATCH (p:Paper) DETACH DELETE p", "write"),
    ("CREATE (:Paper {title: 'fake'})", "write"),
    ("MERGE (j:Journal {name: 'X'}) RETURN j", "write"),
    ("MATCH (p:Paper) SET p.title = 'x' RETURN p", "write"),
    ("match (p:Paper) set p.title = 'x' return p", "write"),
    ("MATCH (p:Paper) REMOVE p:Retracted RETURN p", "write"),
    ("DROP INDEX paper_doi", "write"),
    ("LOAD CSV FROM 'http://x' AS row RETURN row", "write"),
    ("MATCH (p:Paper) CALL { WITH p DELETE p } RETURN 1", "write"),
    ("CALL dbms.listUsers()", "procedure"),
    ("CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {})", "procedure"),
    ("CALL gds.graph.drop('citations')", "procedure"),
    ("MATCH (u:User) RETURN u", "schema"),
    ("MATCH (p:Paper)-[:LIKES]->(q:Paper) RETURN q", "schema"),
    ("MATCH (p:Paper)-[:CITES*]->(q:Paper) RETURN q", "cost"),
    ("MATCH (p:Paper)-[:CITES*1..10]->(q:Paper) RETURN q", "cost"),
    ("MATCH (p:Paper)-[:CITES*2..]->(q:Paper) RETURN q", "cost"),
    ("MATCH (p:Paper) RETURN p; MATCH (n) DETACH DELETE n", "shape"),
    ("MATCH (p:`Paper`) RETURN p", "shape"),
    ("   ", "shape"),
]


@pytest.mark.parametrize("query, reason", REFUSED_QUERIES)
def test_dangerous_queries_are_refused(query, reason):
    with pytest.raises(GuardrailError) as exc:
        validate(query)
    assert exc.value.reason == reason


# ---------- LIMIT handling ----------


def test_adds_limit_when_missing():
    assert validate("MATCH (p:Paper) RETURN p.title").endswith(f"LIMIT {guardrails.MAX_LIMIT}")


def test_keeps_small_limit():
    assert validate("MATCH (p:Paper) RETURN p.title LIMIT 5").endswith("LIMIT 5")


def test_lowers_big_limit():
    assert validate("MATCH (p:Paper) RETURN p LIMIT 100000").endswith(f"LIMIT {guardrails.MAX_LIMIT}")


def test_replaces_parameter_limit():
    assert validate("MATCH (p:Paper) RETURN p LIMIT $n").endswith(f"LIMIT {guardrails.MAX_LIMIT}")


def test_trailing_semicolon_is_accepted():
    assert validate("MATCH (p:Paper) RETURN p LIMIT 3;").endswith("LIMIT 3")


# ---------- Facts vs hypotheses ----------


def test_relies_on_is_flagged_as_hypothesis():
    assert uses_hypotheses("MATCH (a:Paper)-[:RELIES_ON]->(b:Paper) RETURN a")


def test_cites_is_a_fact():
    assert not uses_hypotheses("MATCH (a:Paper)-[:CITES]->(b:Paper) RETURN a")


def test_relies_on_inside_a_string_is_not_a_hypothesis():
    assert not uses_hypotheses("MATCH (p:Paper) WHERE p.title = 'RELIES_ON' RETURN p")


# ---------- EXPLAIN dry run (fake database) ----------


class FakeDB:
    """Records queries; can be told to fail on EXPLAIN."""

    def __init__(self, explain_error=None, rows=None):
        self.queries = []
        self.explain_error = explain_error
        self.rows = rows or []

    def read(self, query, params=None):
        text = query if isinstance(query, str) else query.text
        self.queries.append((text, params))
        if text.startswith("EXPLAIN") and self.explain_error:
            raise self.explain_error
        return self.rows


def test_run_validated_explains_then_runs():
    db = FakeDB(rows=[{"title": "A"}])
    rows = run_validated(db, "MATCH (p:Paper) RETURN p.title AS title")
    assert rows == [{"title": "A"}]
    assert db.queries[0][0].startswith("EXPLAIN")
    assert len(db.queries) == 2


def test_syntax_error_never_executes():
    db = FakeDB(explain_error=ClientError("Invalid input 'RETRN'"))
    with pytest.raises(GuardrailError) as exc:
        run_validated(db, "MATCH (p:Paper) RETRN p")
    assert exc.value.reason == "syntax"
    assert len(db.queries) == 1  # only the EXPLAIN


def test_refused_query_never_reaches_the_database():
    db = FakeDB()
    with pytest.raises(GuardrailError):
        run_validated(db, "MATCH (n:Paper) DETACH DELETE n")
    assert not db.queries