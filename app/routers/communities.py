"""Research communities (Louvain) ranked by their share of retracted papers.

Owner: Person 1. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/api/communities", tags=["graph algorithms"])

NOT_READY = "Not implemented yet"


@router.get("", summary="Research communities ranked by contamination", status_code=status.HTTP_200_OK)
def list_communities() -> dict:
    """Research communities ranked by contamination. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
