"""Zombie citations: journals that keep citing papers after their retraction.

Owner: Person 1. Only the owner edits this file.
"""

from fastapi import APIRouter, Depends, Query

from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.journals import ZombieCitationsResponse
from app.services.journals import zombie_journals

router = APIRouter(prefix="/api/journals", tags=["journals"])


@router.get(
    "/zombie-citations",
    response_model=ZombieCitationsResponse,
    summary="Journals that keep citing retracted papers",
)
def zombie_citations(
    limit: int = Query(20, ge=1, le=100, description="Journals per page"),
    offset: int = Query(0, ge=0, description="How many journals to skip (pagination)"),
    min_zombie: int = Query(1, ge=1, description="Only journals with at least this many zombie citations"),
    db: Neo4jClient = Depends(get_db),
) -> ZombieCitationsResponse:
    """Rank journals by citations made after the cited paper was retracted.

    Each journal also shows all its citations to now-retracted papers, the share
    that came after retraction, and up to 3 examples.
    """
    total, rows = zombie_journals(db, limit=limit, offset=offset, min_zombie=min_zombie)
    return ZombieCitationsResponse(total=total, limit=limit, offset=offset, items=rows)
