"""Top spreaders: retracted papers ranked by PageRank.

Owner: Person 1. Only the owner edits this file.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.retractions import GraphSize, TopSpreadersResponse
from app.services.gds import GraphNotReadyError
from app.services.retractions import top_spreaders as rank_top_spreaders

router = APIRouter(prefix="/api/retractions", tags=["graph algorithms"])


@router.get(
    "/top-spreaders",
    response_model=TopSpreadersResponse,
    summary="Retracted papers that did the most damage",
    responses={503: {"description": "No data loaded yet, or Neo4j is not reachable"}},
)
def top_spreaders(
    limit: int = Query(10, ge=1, le=100, description="Papers per page"),
    offset: int = Query(0, ge=0, description="How many papers to skip (pagination)"),
    refresh: bool = Query(False, description="Rebuild the in-memory graph first (after loading new data)"),
    db: Neo4jClient = Depends(get_db),
) -> TopSpreadersResponse:
    """Rank retracted papers by PageRank: how central they are in the citation network.

    A high score means many papers, and influential ones, build on this retracted
    paper, so the damage from its retraction can spread the furthest.
    """
    try:
        info, total, rows = rank_top_spreaders(db, limit=limit, offset=offset, refresh=refresh)
    except GraphNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    graph = GraphSize.from_projection(info)
    return TopSpreadersResponse(total=total, limit=limit, offset=offset, graph=graph, items=rows)
