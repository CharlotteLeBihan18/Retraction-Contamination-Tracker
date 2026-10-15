"""At-risk prediction for a paper (FastRP embeddings + classifier).

Owner: Person 1. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/api/papers", tags=["machine learning"])

NOT_READY = "Not implemented yet"


@router.get("/{paper_id}/risk", summary="Predicted probability that a paper is at risk", status_code=status.HTTP_200_OK)
def paper_risk(paper_id: str) -> dict:
    """Predicted probability that a paper is at risk. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
