"""Audit: the LangGraph integrity-audit agent.

Owner: Person 3. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/api", tags=["AI"])

NOT_READY = "Not implemented yet"


@router.post("/audit", summary="Integrity report for a paper, with every tool call", status_code=status.HTTP_200_OK)
def audit() -> dict:
    """Integrity report for a paper, with every tool call. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
