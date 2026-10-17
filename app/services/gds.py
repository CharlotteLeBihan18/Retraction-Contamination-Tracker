"""GDS helpers: create and drop the in-memory citation projection, run through Cypher.

Owner: Person 1. Only the owner edits this file.

GDS algorithms (PageRank, Louvain, FastRP) do not run on the database itself but on
a *projection*: a compact in-memory copy of the part of the graph they need. We
project every ``Paper`` and every ``CITES`` relationship, twice:

* ``CITES`` keeps the direction (citing -> cited). PageRank needs it: being cited
  by important papers is what makes a paper central.
* ``CITES_UNDIRECTED`` ignores the direction. Louvain and FastRP need it: two
  papers belong to the same community whichever one cites the other.

A projection is a snapshot. After new data is loaded, the loading script calls
``drop_projection(db)``, and the next request builds a fresh one. An empty
projection (no papers loaded yet) is never kept: it is dropped and
``GraphNotReadyError`` is raised, so the API answers 503 instead of caching
an empty graph that would be reused after the data arrives.
"""

from dataclasses import dataclass

from neo4j.exceptions import ClientError

from app.database import Neo4jClient

GRAPH_NAME = "citations"
DIRECTED = "CITES"
UNDIRECTED = "CITES_UNDIRECTED"

EXISTS_QUERY = "CALL gds.graph.exists($name) YIELD exists RETURN exists"

PROJECT_QUERY = """
CALL gds.graph.project(
    $name,
    $label,
    {
        CITES: {type: 'CITES', orientation: 'NATURAL'},
        CITES_UNDIRECTED: {type: 'CITES', orientation: 'UNDIRECTED'}
    }
)
YIELD graphName, nodeCount, relationshipCount
RETURN graphName, nodeCount, relationshipCount
"""

DROP_QUERY = "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName"

INFO_QUERY = """
CALL gds.graph.list($name)
YIELD graphName, nodeCount, relationshipCount
RETURN graphName, nodeCount, relationshipCount
"""


NO_DATA = "No papers to analyse yet: load the data first"


class GraphNotReadyError(RuntimeError):
    """Raised when there is no data to project yet (for example an empty database)."""


@dataclass(frozen=True)
class ProjectionInfo:
    """Size of the in-memory projection."""

    name: str
    node_count: int
    relationship_count: int


def _info(row: dict) -> ProjectionInfo:
    return ProjectionInfo(row["graphName"], row["nodeCount"], row["relationshipCount"])


def projection_exists(db: Neo4jClient, name: str = GRAPH_NAME) -> bool:
    """Return True when the projection is already in memory."""
    rows = db.read(EXISTS_QUERY, {"name": name})
    return bool(rows and rows[0]["exists"])


def drop_projection(db: Neo4jClient, name: str = GRAPH_NAME) -> None:
    """Free the memory used by the projection. Does nothing if it does not exist."""
    db.write(DROP_QUERY, {"name": name})


def _non_empty(db: Neo4jClient, info: ProjectionInfo) -> ProjectionInfo:
    """Return the projection, or drop it and raise if it has no papers."""
    if info.node_count == 0:
        drop_projection(db, info.name)
        raise GraphNotReadyError(NO_DATA)
    return info


def ensure_projection(
    db: Neo4jClient, name: str = GRAPH_NAME, label: str = "Paper", refresh: bool = False
) -> ProjectionInfo:
    """Make sure the citation projection is in memory and return its size.

    Reuses the existing projection unless ``refresh`` is True, because projecting
    reads the whole citation graph and should not happen on every request.
    ``label`` exists so tests can project only their own test nodes.
    """
    if refresh:
        drop_projection(db, name)
    elif projection_exists(db, name):
        return _non_empty(db, _info(db.read(INFO_QUERY, {"name": name})[0]))

    try:
        rows = db.write(PROJECT_QUERY, {"name": name, "label": label})
    except ClientError as exc:
        # These error messages come from GDS 2.13 (see docker-compose.yml).
        # Check them again if GDS is upgraded: a reworded message would skip these branches.
        if "already exists" in str(exc):  # another request projected it meanwhile
            return _non_empty(db, _info(db.read(INFO_QUERY, {"name": name})[0]))
        if "not found" in str(exc).lower() or "no nodes" in str(exc).lower():
            raise GraphNotReadyError(NO_DATA) from exc
        raise
    return _non_empty(db, _info(rows[0]))
