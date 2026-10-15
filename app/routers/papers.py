"""Contamination and exposure traversals around a paper.

Owner: Person 2. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/api/papers", tags=["papers"])

NOT_READY = "Not implemented yet"


@router.get(
    "/{paper_id}/contamination", summary="Papers that built on a retracted paper", status_code=status.HTTP_200_OK
)
def contamination(paper_id: str) -> dict:
    """Papers that built on a retracted paper. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)


@router.get("/{paper_id}/exposure", summary="Whether a paper relies on retracted work", status_code=status.HTTP_200_OK)
def exposure(paper_id: str) -> dict:
    """Whether a paper relies on retracted work. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
