"""Load the cached Retraction Watch + OpenAlex data into Neo4j.

The script reads only ``data/cache/`` (written by ``scripts/fetch_openalex.py``), so it needs
no API key and no internet. It first creates the constraints and indexes, then loads papers,
retraction notices, reasons, authors, journals and the citations between the retracted papers.

Every write is a ``MERGE``, so running the script twice gives the same graph.

Run with:  python -m scripts.ingest             (load into Neo4j)
           python -m scripts.ingest --dry-run   (only show what would be loaded)
"""

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from neo4j.exceptions import Neo4jError

from app.config import get_settings
from app.database import Neo4jClient
from app.services.gds import drop_projection
from scripts.fetch_openalex import CACHE_DIR

BATCH_SIZE = 500
OPENALEX_PREFIX = "https://openalex.org/"
DOI_PREFIX = "https://doi.org/"

# Same names as data/sample/sample.cypher, so loading both never creates duplicates.
SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT paper_doi IF NOT EXISTS FOR (p:Paper) REQUIRE p.doi IS UNIQUE",
    "CREATE CONSTRAINT paper_openalex_id IF NOT EXISTS FOR (p:Paper) REQUIRE p.openalex_id IS UNIQUE",
    "CREATE CONSTRAINT author_id IF NOT EXISTS FOR (a:Author) REQUIRE a.openalex_id IS UNIQUE",
    "CREATE CONSTRAINT journal_name IF NOT EXISTS FOR (j:Journal) REQUIRE j.name IS UNIQUE",
    "CREATE CONSTRAINT reason_name IF NOT EXISTS FOR (r:Reason) REQUIRE r.name IS UNIQUE",
    "CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year)",
]

# All queries take their data as $rows: never string formatting, so no injection and a cached plan.
PAPERS_QUERY = """
UNWIND $rows AS row
MERGE (p:Paper {doi: row.doi})
SET p:Retracted,
    p.title = row.title,
    p.year = row.year,
    p.abstract = row.abstract,
    p.openalex_id = row.openalex_id,
    p.publication_date = date(row.publication_date)
WITH p, row
MERGE (j:Journal {name: row.journal})
MERGE (p)-[:PUBLISHED_IN]->(j)
"""

NOTICES_QUERY = """
UNWIND $rows AS row
MATCH (p:Paper {doi: row.doi})
MERGE (n:RetractionNotice {source: row.source})-[:RETRACTS]->(p)
SET n.date = date(row.date)
WITH n, row
UNWIND row.reasons AS reason
MERGE (r:Reason {name: reason})
MERGE (n)-[:FOR_REASON]->(r)
"""

AUTHORS_QUERY = """
UNWIND $rows AS row
MATCH (p:Paper {doi: row.doi})
MERGE (a:Author {openalex_id: row.author_id})
SET a.name = row.name
MERGE (a)-[:AUTHORED]->(p)
"""

# WITH between the two MATCH clauses: two index lookups in sequence, no Cartesian product.
CITATIONS_QUERY = """
UNWIND $rows AS row
MATCH (a:Paper {doi: row.citing})
WITH a, row
MATCH (b:Paper {doi: row.cited})
MERGE (a)-[c:CITES]->(b)
SET c.before_retraction = row.before_retraction
"""

# A missing flag makes "before_retraction = false" null in Cypher: the citation would be silently ignored.
MISSING_FLAG_QUERY = """
MATCH (:Paper)-[c:CITES]->(:Paper:Retracted)
WHERE c.before_retraction IS NULL
RETURN count(c) AS missing
"""

SUMMARY_QUERY = """
RETURN COUNT { (:Paper) } AS papers,
       COUNT { (:Paper:Retracted) } AS retracted,
       COUNT { (:RetractionNotice) } AS notices,
       COUNT { (:Reason) } AS reasons,
       COUNT { (:Author) } AS authors,
       COUNT { (:Journal) } AS journals,
       COUNT { ()-[:CITES]->() } AS citations
"""


class MissingFlagError(RuntimeError):
    """Raised when a citation to a retracted paper has no before_retraction value."""


def short_id(url: str) -> str:
    """``'https://openalex.org/A123'`` -> ``'A123'``."""
    return url.removeprefix(OPENALEX_PREFIX)


def rebuild_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Rebuild the abstract text from OpenAlex's ``{word: [positions]}`` format."""
    if not inverted_index:
        return None
    words: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for position in positions:
            words[position] = word
    return " ".join(words[position] for position in sorted(words))


def before_retraction(citing_date: str | None, citing_year: int | None, retraction_date: str | None) -> bool:
    """Say whether a citation was made before (or at the time of) the retraction.

    Same rules as ``app/services/journals.py``: compare full dates when both are known; with only a
    year, the same year counts as "before"; when nothing is known, answer True. A citation is only
    called a zombie (False) when we are sure it came after the retraction.
    """
    if retraction_date is None:
        return True
    if citing_date:
        return citing_date <= retraction_date  # ISO dates sort like dates
    if citing_year is not None:
        return citing_year <= int(retraction_date[:4])
    return True


def batches(rows: list[dict[str, Any]], size: int = BATCH_SIZE) -> Iterator[list[dict[str, Any]]]:
    """Yield successive slices of ``rows`` with at most ``size`` elements."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def load_cache(cache_dir: Path = CACHE_DIR) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Read the cache: the Retraction Watch selection and the OpenAlex works, keyed by lowercase DOI.

    Works whose DOI was not in the selection are ignored (OpenAlex sometimes returns extra records).
    """
    selection = json.loads((cache_dir / "retraction_watch_selection.json").read_text(encoding="utf-8"))
    wanted = {row["doi"] for row in selection}
    works: dict[str, dict[str, Any]] = {}
    for path in sorted((cache_dir / "works_by_doi").glob("*.json")):
        for work in json.loads(path.read_text(encoding="utf-8"))["results"]:
            doi = (work.get("doi") or "").removeprefix(DOI_PREFIX).lower()
            if doi in wanted:
                works[doi] = work
    return selection, works


def journal_name(work: dict[str, Any], fallback: str) -> str:
    """OpenAlex's journal name when it has one, otherwise the Retraction Watch name.

    The citing papers also come from OpenAlex, so using its names keeps one spelling per journal.
    """
    source = (work.get("primary_location") or {}).get("source") or {}
    return source.get("display_name") or fallback


def paper_row(row: dict[str, Any], work: dict[str, Any]) -> dict[str, Any]:
    """One parameter row for PAPERS_QUERY."""
    return {
        "doi": row["doi"],
        "title": work.get("title") or row["title"],
        "year": work.get("publication_year"),
        "abstract": rebuild_abstract(work.get("abstract_inverted_index")),
        "openalex_id": short_id(work["id"]),
        "publication_date": work.get("publication_date"),
        "journal": journal_name(work, row["journal"]),
    }


def notice_rows(row: dict[str, Any], work: dict[str, Any]) -> list[dict[str, Any]]:
    """Retraction Watch notice (with date and reasons), plus an OpenAlex notice when it agrees.

    OpenAlex only says "is_retracted", with no date, so its notice has no date and no reasons.
    Where the two sources disagree, Retraction Watch wins: the paper stays Retracted.
    """
    notices = [
        {
            "doi": row["doi"],
            "source": "Retraction Watch",
            "date": row["retraction_date"],
            "reasons": row["reasons"],
        }
    ]
    if work.get("is_retracted"):
        notices.append({"doi": row["doi"], "source": "OpenAlex", "date": None, "reasons": []})
    return notices


def author_rows(doi: str, work: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per author of the paper. Authors OpenAlex gives no id for cannot be keyed: skipped."""
    seen: set[str] = set()
    rows = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        if not author.get("id") or author["id"] in seen:
            continue
        seen.add(author["id"])
        rows.append({"doi": doi, "author_id": short_id(author["id"]), "name": author.get("display_name")})
    return rows


def citation_rows(works: dict[str, dict[str, Any]], retraction_dates: dict[str, str | None]) -> list[dict[str, Any]]:
    """Citations between two papers of the set (a retracted paper citing another retracted paper).

    OpenAlex lists the works each paper cites in ``referenced_works``; we keep the references that
    are themselves in the set. Citations from papers outside the set come from the next fetch step.
    """
    by_openalex_id = {work["id"]: doi for doi, work in works.items()}
    rows = []
    for citing, work in works.items():
        for reference in work.get("referenced_works") or []:
            cited = by_openalex_id.get(reference)
            if cited is None or cited == citing:
                continue
            rows.append(
                {
                    "citing": citing,
                    "cited": cited,
                    "before_retraction": before_retraction(
                        work.get("publication_date"), work.get("publication_year"), retraction_dates[cited]
                    ),
                }
            )
    return rows


def build_rows(selection: list[dict[str, Any]], works: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Turn the cache into the parameter rows of each query (no database involved)."""
    kept = [row for row in selection if row["doi"] in works]
    papers, notices, authors = [], [], []
    for row in kept:
        work = works[row["doi"]]
        papers.append(paper_row(row, work))
        notices.extend(notice_rows(row, work))
        authors.extend(author_rows(row["doi"], work))
    retraction_dates = {row["doi"]: row["retraction_date"] for row in kept}
    return {
        "papers": papers,
        "notices": notices,
        "authors": authors,
        "citations": citation_rows({row["doi"]: works[row["doi"]] for row in kept}, retraction_dates),
    }


def create_schema(client: Neo4jClient) -> None:
    """Create the constraints and indexes (a no-op when they already exist)."""
    for statement in SCHEMA_STATEMENTS:
        client.write(statement)


def run_batches(client: Neo4jClient, query: str, rows: list[dict[str, Any]]) -> None:
    """Run one query on the rows, BATCH_SIZE rows per transaction."""
    for chunk in batches(rows):
        client.write(query, {"rows": chunk})


def load(client: Neo4jClient, rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Create the schema, load every kind of row and return the counts found in the database.

    Afterwards the in-memory GDS graph is dropped, so the algorithms see the new data, and we check
    that every citation to a retracted paper has its ``before_retraction`` flag.
    """
    create_schema(client)
    run_batches(client, PAPERS_QUERY, rows["papers"])
    run_batches(client, NOTICES_QUERY, rows["notices"])
    run_batches(client, AUTHORS_QUERY, rows["authors"])
    run_batches(client, CITATIONS_QUERY, rows["citations"])
    drop_projection(client)
    missing = client.read(MISSING_FLAG_QUERY)[0]["missing"]
    if missing:
        raise MissingFlagError(f"{missing} citations to retracted papers have no before_retraction value")
    return client.read(SUMMARY_QUERY)[0]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options."""
    parser = argparse.ArgumentParser(description="Load the cached data into Neo4j.")
    parser.add_argument("--dry-run", action="store_true", help="only count what would be loaded")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Load the cache into Neo4j and return a shell exit code."""
    args = parse_args(argv)
    selection, works = load_cache()
    rows = build_rows(selection, works)
    print(f"Papers: {len(rows['papers'])} of {len(selection)} selected ({len(selection) - len(works)} not in OpenAlex)")
    print(f"Notices: {len(rows['notices'])}  Author links: {len(rows['authors'])}  Citations: {len(rows['citations'])}")
    if args.dry_run:
        return 0

    client = Neo4jClient.from_settings(get_settings())
    try:
        if not client.is_available():
            print("Neo4j is not reachable. Start the stack first: make docker-run")
            return 1
        summary = load(client, rows)
    except (Neo4jError, MissingFlagError) as exc:
        print(f"Loading failed: {exc}")
        return 1
    finally:
        client.close()
    print("In the database now: " + ", ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
