"""Cypher queries for the zombie-citations endpoint.

Owner: Person 1. Only the owner edits this file.

A "zombie citation" is a CITES relationship whose ``before_retraction`` property
is false: the citing paper was published after the cited paper was retracted.
That property is computed once at ingestion (Person 2), so these queries only
compare a boolean instead of comparing dates on every request.
"""

from typing import Any

from app.database import Neo4jClient

# Shared pattern: journal <- citing paper -> retracted paper.
# Starting from the Retracted label keeps the scan small: only citations that
# point at retracted papers are visited, not the whole citation graph.
_STATS = """
MATCH (r:Paper:Retracted)<-[c:CITES]-(citing:Paper)-[:PUBLISHED_IN]->(j:Journal)
WITH j,
     count(c) AS citations_to_retracted,
     sum(CASE WHEN c.before_retraction = false THEN 1 ELSE 0 END) AS zombie_citations
WHERE zombie_citations >= $min_zombie
"""

COUNT_QUERY = _STATS + "RETURN count(j) AS total"

PAGE_QUERY = _STATS + """
WITH j, citations_to_retracted, zombie_citations
ORDER BY zombie_citations DESC, citations_to_retracted DESC, j.name
SKIP $offset LIMIT $limit
CALL (j) {
    MATCH (j)<-[:PUBLISHED_IN]-(p:Paper)-[:CITES {before_retraction: false}]->(r:Paper:Retracted)
    WITH p, r
    ORDER BY p.year DESC
    LIMIT 3
    RETURN collect({citing_title: p.title, citing_year: p.year, retracted_title: r.title}) AS examples
}
RETURN j.name AS journal,
       zombie_citations,
       citations_to_retracted,
       round(toFloat(zombie_citations) / citations_to_retracted, 3) AS zombie_share,
       examples
"""


def zombie_journals(db: Neo4jClient, limit: int, offset: int, min_zombie: int) -> tuple[int, list[dict[str, Any]]]:
    """Return the total number of matching journals and one page of them.

    Ranked by number of zombie citations, then by all citations to retracted papers.
    """
    params = {"limit": limit, "offset": offset, "min_zombie": min_zombie}
    total_rows = db.read(COUNT_QUERY, params)
    total = total_rows[0]["total"] if total_rows else 0
    rows = db.read(PAGE_QUERY, params) if total > offset else []
    return total, rows
