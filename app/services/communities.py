"""Cypher queries for the communities endpoint (Louvain).

Owner: Charlotte Le Bihan. Only the owner edits this file.

Louvain groups papers into communities: sets of papers that cite each other much
more than they cite papers outside the set. In a citation network these match
research topics fairly well. It runs on the UNDIRECTED copy of the citations
(see app/services/gds.py): two papers belong together whichever one cites the other.

Louvain looks at the whole graph at once, so it runs once per request and the
communities are ranked and paginated afterwards in Python. Community numbers are
only labels: they can change when the projection is rebuilt.
"""

from typing import Any

from app.database import Neo4jClient
from app.services.gds import GRAPH_NAME, UNDIRECTED, ProjectionInfo, ensure_projection

COMMUNITIES_QUERY = """
CALL gds.louvain.stream($graph, {relationshipTypes: [$relationship], consecutiveIds: true, concurrency: 1})
YIELD nodeId, communityId
WITH communityId, gds.util.asNode(nodeId) AS paper
WITH communityId,
     count(paper) AS size,
     sum(CASE WHEN paper:Retracted THEN 1 ELSE 0 END) AS retracted_papers,
     collect(CASE WHEN paper:Retracted THEN paper.title END)[..3] AS retracted_examples,
     collect(paper.title)[..3] AS sample_titles
WHERE size >= $min_size
RETURN communityId AS community_id,
       size,
       retracted_papers,
       round(toFloat(retracted_papers) / size, 3) AS retracted_share,
       retracted_examples,
       sample_titles
ORDER BY retracted_share DESC, retracted_papers DESC, size DESC, community_id
"""


def contaminated_communities(
    db: Neo4jClient,
    limit: int,
    offset: int,
    min_size: int,
    refresh: bool = False,
    graph: str = GRAPH_NAME,
    label: str = "Paper",
) -> tuple[ProjectionInfo, int, list[dict[str, Any]]]:
    """Return the projection size, the number of communities, and one page ranked by retracted share.

    ``graph`` and ``label`` let tests use their own projection; endpoints keep the defaults.
    """
    info = ensure_projection(db, name=graph, label=label, refresh=refresh)
    rows = db.read(COMMUNITIES_QUERY, {"graph": graph, "relationship": UNDIRECTED, "min_size": min_size})
    return info, len(rows), rows[offset : offset + limit]
