"""Top spreaders: retracted papers ranked by PageRank.

Owner: Person 1. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/api/retractions", tags=["graph algorithms"])

NOT_READY = "Not implemented yet"


@router.get("/top-spreaders", summary="Retracted papers that did the most damage", status_code=status.HTTP_200_OK)
def top_spreaders() -> dict:
    """Retracted papers that did the most damage. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
