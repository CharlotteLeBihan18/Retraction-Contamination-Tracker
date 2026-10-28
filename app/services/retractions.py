"""Cypher queries for the top-spreaders endpoint (PageRank).

Owner: Charlotte Le Bihan. Only the owner edits this file.

PageRank runs on the directed CITES relationships of the in-memory projection
(see app/services/gds.py). A paper scores high when it is cited by many papers,
and even higher when those papers are themselves heavily cited. For a retracted
paper, a high score means its flawed results sit at the centre of the network,
so the damage can reach the most research.

PageRank scores every paper; we then keep only the retracted ones. Scoring the
whole graph is needed anyway, because a retracted paper's score depends on all
the papers around it.
"""

from typing import Any

from app.database import Neo4jClient
from app.services.gds import DIRECTED, GRAPH_NAME, ProjectionInfo, ensure_projection

COUNT_QUERY = "MATCH (p:Paper:Retracted) RETURN count(p) AS total"

PAGE_QUERY = """
CALL gds.pageRank.stream($graph, {relationshipTypes: [$relationship], dampingFactor: 0.85, maxIterations: 20})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS paper, score
WHERE paper:Retracted
WITH paper, score
ORDER BY score DESC, paper.title
SKIP $offset LIMIT $limit
RETURN paper.openalex_id AS paper_id,
       paper.doi AS doi,
       paper.title AS title,
       paper.year AS year,
       round(score, 4) AS pagerank,
       COUNT { (paper)<-[:CITES]-(:Paper) } AS direct_citations,
       COUNT { (paper)<-[c:CITES]-(:Paper) WHERE c.before_retraction = false } AS citations_after_retraction
"""


def top_spreaders(
    db: Neo4jClient,
    limit: int,
    offset: int,
    refresh: bool = False,
    graph: str = GRAPH_NAME,
    label: str = "Paper",
) -> tuple[ProjectionInfo, int, list[dict[str, Any]]]:
    """Return the projection size, the number of retracted papers, and one page ranked by PageRank.

    ``graph`` and ``label`` let tests use their own projection; endpoints keep the defaults.
    """
    info = ensure_projection(db, name=graph, label=label, refresh=refresh)
    total_rows = db.read(COUNT_QUERY)
    total = total_rows[0]["total"] if total_rows else 0
    params = {"graph": graph, "relationship": DIRECTED, "limit": limit, "offset": offset}
    rows = db.read(PAGE_QUERY, params) if total > offset else []
    return info, total, rows
