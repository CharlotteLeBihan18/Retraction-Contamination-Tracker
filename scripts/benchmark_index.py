"""Measure how much our indexes speed up real queries (PROFILE before and after).

Owner: Person 2. Only the owner edits this file.

For each index of the graph model that our endpoints rely on, the script:

1. drops the index and measures the queries that use it ("before"),
2. creates the index again and measures the same queries ("after"),
3. makes sure the index is back in any case, even if something fails in between.

"Measures" means: one ``PROFILE`` run to read the plan (which operators Neo4j used and how
many database hits it needed), then several normal runs to time it. Database hits are the
fair comparison: they do not depend on how busy the laptop is. The timings are given as a
median over the runs, after a few warm-up runs so the plan is already cached.

Only run this on your local database: dropping a constraint for a few seconds is fine on a
laptop, not on a shared server.

Run with:  python -m scripts.benchmark_index                     (prints the table)
           python -m scripts.benchmark_index --out docs/index_benchmark.md
"""

import argparse
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Protocol

from neo4j import GraphDatabase, RoutingControl
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.config import get_settings

WARMUP_RUNS = 3
TIMED_RUNS = 50

# The most cited retracted paper and the most common year make the comparison realistic:
# they are the values a demo would ask about, and they return many rows.
PICK_DOI = """
MATCH (c:Paper)-[:CITES]->(r:Paper:Retracted)
RETURN r.doi AS doi, count(c) AS citers
ORDER BY citers DESC
LIMIT 1
"""
PICK_YEAR = """
MATCH (p:Paper) WHERE p.year IS NOT NULL
RETURN p.year AS year, count(p) AS papers
ORDER BY papers DESC
LIMIT 1
"""


@dataclass
class IndexCase:
    """One index, how to drop and create it, and the queries that need it."""

    name: str
    create: str
    drop: str
    queries: dict[str, str]
    param: str  # "doi" or "year": which picked value the queries take


CASES = [
    IndexCase(
        name="paper_doi (unique constraint on Paper.doi)",
        create="CREATE CONSTRAINT paper_doi IF NOT EXISTS FOR (p:Paper) REQUIRE p.doi IS UNIQUE",
        drop="DROP CONSTRAINT paper_doi IF EXISTS",
        queries={
            "Find a paper by DOI": "MATCH (p:Paper {doi: $doi}) RETURN p.title AS title",
            "Direct citers of a retracted paper (start of /contamination)": (
                "MATCH (r:Paper:Retracted {doi: $doi})<-[c:CITES]-(p:Paper) "
                "RETURN count(p) AS citers, count(CASE WHEN c.before_retraction = false THEN 1 END) AS zombies"
            ),
        },
        param="doi",
    ),
    IndexCase(
        name="paper_year (index on Paper.year)",
        create="CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year)",
        drop="DROP INDEX paper_year IF EXISTS",
        queries={"Papers of one year": "MATCH (p:Paper) WHERE p.year = $year RETURN count(p) AS papers"},
        param="year",
    ),
]


@dataclass
class Measure:
    """What one query cost, with or without the index."""

    query: str
    with_index: bool
    db_hits: int
    rows: int
    operators: list[str]
    median_ms: float


@dataclass
class CaseResult:
    """The measures of one index case."""

    case: str
    measures: list[Measure] = field(default_factory=list)


class Runner(Protocol):
    """What the benchmark needs from the database (a fake one is used in the tests)."""

    def write(self, query: str) -> None:
        """Run a schema statement."""

    def profile(self, query: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run the query once with PROFILE and return the plan."""

    def time_ms(self, query: str, params: dict[str, Any]) -> float:
        """Run the query once and return how long it took, in milliseconds."""


class Neo4jRunner:
    """The real runner: a driver of its own, because PROFILE needs the query summary."""

    def __init__(self, uri: str, user: str, password: str, database: str) -> None:
        self._database = database
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def write(self, query: str) -> None:
        """Run a schema statement."""
        self._driver.execute_query(query, database_=self._database, routing_=RoutingControl.WRITE)

    def read(self, query: str) -> list[dict[str, Any]]:
        """Run a read query and return its rows."""
        records, _, _ = self._driver.execute_query(query, database_=self._database, routing_=RoutingControl.READ)
        return [record.data() for record in records]

    def profile(self, query: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run the query once with PROFILE and return the plan."""
        _, summary, _ = self._driver.execute_query(
            "PROFILE " + query, params, database_=self._database, routing_=RoutingControl.READ
        )
        return summary.profile or {}

    def time_ms(self, query: str, params: dict[str, Any]) -> float:
        """Run the query once and return the wall-clock time in milliseconds."""
        start = time.perf_counter()
        self._driver.execute_query(query, params, database_=self._database, routing_=RoutingControl.READ)
        return (time.perf_counter() - start) * 1000

    def close(self) -> None:
        """Close the driver."""
        self._driver.close()


def plan_nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every operator of a PROFILE plan, parent first."""
    yield plan
    for child in plan.get("children", []):
        yield from plan_nodes(child)


def total_db_hits(plan: dict[str, Any]) -> int:
    """Database hits of the whole plan (each operator reports only its own)."""
    return sum(int(node.get("dbHits", 0)) for node in plan_nodes(plan))


def operators(plan: dict[str, Any]) -> list[str]:
    """Operator names from the leaves up, without the "@neo4j" suffix (e.g. NodeIndexSeek).

    The first operator also shows its details (e.g. which label it scans), because that is
    where the index makes the difference.
    """
    nodes = list(reversed(list(plan_nodes(plan))))
    names = [str(node.get("operatorType", "?")).split("@", maxsplit=1)[0] for node in nodes]
    details = (nodes[0].get("args") or {}).get("Details") if nodes else None
    if details:
        names[0] = f"{names[0]} ({details})"
    return names


def measure(runner: Runner, label: str, query: str, params: dict[str, Any], with_index: bool) -> Measure:
    """Warm up, PROFILE once, then time the query several times."""
    for _ in range(WARMUP_RUNS):
        runner.time_ms(query, params)
    plan = runner.profile(query, params)
    timings = [runner.time_ms(query, params) for _ in range(TIMED_RUNS)]
    return Measure(
        query=label,
        with_index=with_index,
        db_hits=total_db_hits(plan),
        rows=int(plan.get("rows", 0)),
        operators=operators(plan),
        median_ms=round(median(timings), 2),
    )


def run_case(runner: Runner, case: IndexCase, params: dict[str, Any]) -> CaseResult:
    """Measure the case's queries without the index, then with it, and always put it back."""
    result = CaseResult(case.name)
    try:
        runner.write(case.drop)
        for label, query in case.queries.items():
            result.measures.append(measure(runner, label, query, params, with_index=False))
        runner.write(case.create)
        runner.write("CALL db.awaitIndexes(300)")
        for label, query in case.queries.items():
            result.measures.append(measure(runner, label, query, params, with_index=True))
    finally:
        runner.write(case.create)
        runner.write("CALL db.awaitIndexes(300)")
    return result


def to_markdown(results: list[CaseResult], params: dict[str, Any], papers: int) -> str:
    """A table per index, with the speed-up in database hits and in time."""
    lines = [
        "# Index benchmark (PROFILE before and after)",
        "",
        f"Generated by `python -m scripts.benchmark_index` on a graph of {papers} papers.",
        f"Values used: DOI `{params['doi']}` (the most cited retracted paper), year {params['year']} "
        "(the most common year).",
        f"Time is the median of {TIMED_RUNS} runs after {WARMUP_RUNS} warm-up runs, measured from Python, "
        "so it includes the network round trip. Database hits do not depend on the machine.",
        "",
        "How to read the plan: `NodeIndexSeek` / `NodeUniqueIndexSeek` jump straight to the matching papers "
        "through the index; `NodeByLabelScan` reads every node of a label and `Filter` throws away the ones "
        "that do not match.",
    ]
    for result in results:
        lines += ["", f"## {result.case}", ""]
        lines += [
            "| Query | Index | Plan (from the leaves up) | DB hits | Median time |",
            "| --- | --- | --- | --- | --- |",
        ]
        for m in result.measures:
            plan = " → ".join(m.operators)
            state = "after (with)" if m.with_index else "before (without)"
            lines.append(f"| {m.query} | {state} | {plan} | {m.db_hits:,} | {m.median_ms} ms |")
        lines.append("")
        for label in dict.fromkeys(m.query for m in result.measures):
            pair = {m.with_index: m for m in result.measures if m.query == label}
            if True in pair and False in pair and pair[True].db_hits:
                ratio = pair[False].db_hits / pair[True].db_hits
                lines.append(f"- {label}: **{ratio:,.0f}x fewer database hits** with the index.")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options."""
    parser = argparse.ArgumentParser(description="PROFILE our main queries with and without their index.")
    parser.add_argument("--out", help="also write the Markdown report to this file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run every case and print the report; return a shell exit code."""
    args = parse_args(argv)
    settings = get_settings()
    runner = Neo4jRunner(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
        settings.neo4j_database,
    )
    try:
        picked_doi = runner.read(PICK_DOI)
        picked_year = runner.read(PICK_YEAR)
        if not picked_doi or not picked_year:
            print("The graph is empty: load the data first (python -m scripts.ingest).")
            return 1
        params = {"doi": picked_doi[0]["doi"], "year": picked_year[0]["year"]}
        papers = runner.read("MATCH (p:Paper) RETURN count(p) AS n")[0]["n"]
        results = []
        for case in CASES:
            print(f"Measuring {case.name}...", flush=True)
            results.append(run_case(runner, case, {case.param: params[case.param]}))
    except (ServiceUnavailable, Neo4jError, OSError) as exc:
        print(f"Benchmark stopped, Neo4j answered: {exc}")
        return 1
    finally:
        runner.close()
    report = to_markdown(results, params, papers)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(report)
        print(f"Report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
