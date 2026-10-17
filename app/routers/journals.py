"""Zombie citations: journals that keep citing papers after their retraction.

Owner: Person 1. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/api/journals", tags=["journals"])

NOT_READY = "Not implemented yet"


@router.get("/zombie-citations", summary="Journals that keep citing retracted papers", status_code=status.HTTP_200_OK)
def zombie_citations() -> dict:
    """Journals that keep citing retracted papers. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
