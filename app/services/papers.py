"""Cypher queries for the contamination and exposure endpoints.

Owner: Kenza Gueddas. Only the owner edits this file.

Contamination walks the CITES links downstream from a retracted paper: the papers that cite
it directly (1 hop), the papers that cite those (2 hops), and so on up to ``MAX_DEPTH``.
Only CITES is followed, never RELIES_ON: these are facts from the data, not AI guesses.

Exposure walks the other way, upstream from any paper: which retracted papers it cites,
directly or through other papers, with the shortest citation chain to each of them.

A paper found here is exposed to the retracted work, it is not wrong or invalid. For direct
citations, ``before_retraction`` (computed at loading time) says if the citation came before
the retraction; ``false`` is a "zombie" citation, made after the paper was retracted.
"""

import re
from typing import Any

from app.database import Neo4jClient

MAX_DEPTH = 3
OPENALEX_ID = re.compile(r"^(?:https?://openalex\.org/)?(w\d+)$", re.IGNORECASE)
DOI_PREFIX = re.compile(r"^(?:https?:/+(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)

# The key is chosen in Python from two fixed names, never from user input.
FIND_PAPER_QUERY = """
MATCH (p:Paper {{{key}: $value}})
RETURN p.doi AS doi, p.openalex_id AS openalex_id, p.title AS title, p.year AS year,
       p:Retracted AS retracted, elementId(p) AS node_id
LIMIT 1
"""

# Cypher does not accept a parameter as the path length, so the depth is written into the
# query, only after checking it is an int between 1 and MAX_DEPTH. One row per citing paper,
# with the shortest number of hops between it and the retracted paper.
_CITERS = """
MATCH (r:Paper:Retracted {{doi: $doi}})
MATCH path = (c:Paper)-[:CITES*1..{depth}]->(r)
WHERE c <> r
WITH r, c, min(length(path)) AS hops
"""

SUMMARY_QUERY = _CITERS + """
OPTIONAL MATCH (c)-[direct:CITES]->(r)
RETURN count(c) AS total,
       count(direct) AS direct_citations,
       count(CASE WHEN direct.before_retraction = false THEN 1 END) AS after_retraction
"""

PAGE_QUERY = _CITERS + """
ORDER BY hops, coalesce(c.year, 0) DESC, c.title, c.openalex_id
SKIP $offset LIMIT $limit
OPTIONAL MATCH (c)-[direct:CITES]->(r)
RETURN c.doi AS doi, c.openalex_id AS openalex_id, c.title AS title, c.year AS year, hops,
       direct.before_retraction AS before_retraction
"""


# Upstream: the retracted papers this paper reaches by following its own references.
# For each one, the shortest path is kept, so the chain of papers can be shown.
_REACHED = """
MATCH (p:Paper) WHERE elementId(p) = $node_id
MATCH path = (p)-[:CITES*1..{depth}]->(r:Paper:Retracted)
WHERE r <> p
WITH r, path ORDER BY length(path)
WITH r, collect(path)[0] AS shortest
"""

EXPOSURE_SUMMARY_QUERY = _REACHED + """
RETURN count(r) AS total,
       count(CASE WHEN length(shortest) = 1 THEN 1 END) AS direct_citations
"""

EXPOSURE_PAGE_QUERY = _REACHED + """
WITH r, shortest, length(shortest) AS hops
ORDER BY hops, coalesce(r.year, 0) DESC, r.title, r.doi
SKIP $offset LIMIT $limit
OPTIONAL MATCH (n:RetractionNotice)-[:RETRACTS]->(r)
WITH r, shortest, hops, min(n.date) AS retracted_on
OPTIONAL MATCH (r)<-[:RETRACTS]-(:RetractionNotice)-[:FOR_REASON]->(reason:Reason)
RETURN r.doi AS doi, r.openalex_id AS openalex_id, r.title AS title, r.year AS year, hops,
       CASE WHEN hops = 1 THEN relationships(shortest)[0].before_retraction END AS before_retraction,
       toString(retracted_on) AS retraction_date,
       collect(DISTINCT reason.name) AS reasons,
       [node IN nodes(shortest) | {{doi: node.doi, openalex_id: node.openalex_id, title: node.title,
                                    year: node.year}}] AS path
ORDER BY hops, coalesce(year, 0) DESC, title, doi
"""


def parse_paper_id(raw: str) -> tuple[str, str]:
    """Say whether an id is an OpenAlex id or a DOI, and clean it.

    ``W123`` or ``https://openalex.org/W123`` -> ("openalex_id", "W123").
    Anything else is read as a DOI, lowercased, without a ``https://doi.org/`` or ``doi:`` prefix.
    """
    value = raw.strip()
    match = OPENALEX_ID.match(value)
    if match:
        return "openalex_id", match.group(1).upper()
    return "doi", DOI_PREFIX.sub("", value).lower()


def find_paper(db: Neo4jClient, paper_id: str) -> dict[str, Any] | None:
    """Return the paper with this DOI or OpenAlex id, or None when it is not in the graph."""
    key, value = parse_paper_id(paper_id)
    rows = db.read(FIND_PAPER_QUERY.format(key=key), {"value": value})
    return rows[0] if rows else None


def _check_depth(depth: int) -> None:
    """Refuse a depth outside 1..MAX_DEPTH: it is written into the query text, so it must be safe."""
    if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}")


def contamination_queries(depth: int) -> tuple[str, str]:
    """The summary and page queries for this depth (raises ValueError outside 1..MAX_DEPTH)."""
    _check_depth(depth)
    return SUMMARY_QUERY.format(depth=depth), PAGE_QUERY.format(depth=depth)


def exposure_queries(depth: int) -> tuple[str, str]:
    """The exposure summary and page queries for this depth (raises ValueError outside 1..MAX_DEPTH)."""
    _check_depth(depth)
    return EXPOSURE_SUMMARY_QUERY.format(depth=depth), EXPOSURE_PAGE_QUERY.format(depth=depth)


def contamination(
    db: Neo4jClient, doi: str, depth: int, limit: int, offset: int
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Return the counts for all papers that cite the retracted paper, and one page of them.

    Nearest papers first (fewest hops), then the most recent ones.
    """
    summary_query, page_query = contamination_queries(depth)
    rows = db.read(summary_query, {"doi": doi})
    summary = rows[0] if rows else {"total": 0, "direct_citations": 0, "after_retraction": 0}
    params = {"doi": doi, "limit": limit, "offset": offset}
    items = db.read(page_query, params) if summary["total"] > offset else []
    return summary, items


def exposure(
    db: Neo4jClient, node_id: str, depth: int, limit: int, offset: int
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Return the counts for all retracted papers this paper reaches, and one page of them.

    ``node_id`` is the Neo4j element id given by ``find_paper``: it works for every paper,
    also the citing papers that have no DOI. Nearest retracted papers first.
    """
    summary_query, page_query = exposure_queries(depth)
    rows = db.read(summary_query, {"node_id": node_id})
    summary = rows[0] if rows else {"total": 0, "direct_citations": 0}
    params = {"node_id": node_id, "limit": limit, "offset": offset}
    items = db.read(page_query, params) if summary["total"] > offset else []
    return summary, items
