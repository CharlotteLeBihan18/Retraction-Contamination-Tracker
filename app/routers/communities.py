"""Research communities (Louvain) ranked by their share of retracted papers.

Owner: Charlotte Le Bihan. Only the owner edits this file.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.communities import CommunitiesResponse
from app.models.retractions import GraphSize
from app.services.communities import contaminated_communities
from app.services.gds import GraphNotReadyError

router = APIRouter(prefix="/api/communities", tags=["graph algorithms"])


@router.get(
    "",
    response_model=CommunitiesResponse,
    summary="Research communities ranked by contamination",
    responses={503: {"description": "No data loaded yet, or Neo4j is not reachable"}},
)
def list_communities(
    limit: int = Query(10, ge=1, le=100, description="Communities per page"),
    offset: int = Query(0, ge=0, description="How many communities to skip (pagination)"),
    min_size: int = Query(3, ge=1, description="Ignore communities with fewer papers than this"),
    db: Neo4jClient = Depends(get_db),
) -> CommunitiesResponse:
    """Group papers into research communities with Louvain and rank them by share of retracted papers.

    A community with a high share is a research area where retracted work is
    concentrated, so its citations deserve a closer look.
    """
    try:
        info, total, rows = contaminated_communities(db, limit=limit, offset=offset, min_size=min_size)
    except GraphNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    graph = GraphSize.from_projection(info)
    return CommunitiesResponse(total=total, limit=limit, offset=offset, graph=graph, items=rows)
