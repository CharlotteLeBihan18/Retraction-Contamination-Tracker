"""Contamination and exposure traversals around a paper.

Owner: Person 2. Only the owner edits this file.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.papers import ContaminationResponse
from app.services.papers import MAX_DEPTH, contamination, find_paper

router = APIRouter(prefix="/api/papers", tags=["papers"])

NOT_READY = "Not implemented yet"
NOT_READY_RESPONSE = {501: {"description": NOT_READY}}

PAPER_ID_HELP = "DOI (for example 10.1038/nature12373) or OpenAlex id (for example W2741809807)"


@router.get(
    # ":path" lets the id contain slashes, which every DOI does.
    "/{paper_id:path}/contamination",
    response_model=ContaminationResponse,
    summary="Papers that built on a retracted paper",
    responses={404: {"description": "Paper not found, or not a retracted paper"}},
)
def contamination_endpoint(
    paper_id: str = Path(description=PAPER_ID_HELP),
    depth: int = Query(2, ge=1, le=MAX_DEPTH, description="How many citation steps to follow"),
    limit: int = Query(20, ge=1, le=100, description="Papers per page"),
    offset: int = Query(0, ge=0, description="How many papers to skip (pagination)"),
    db: Neo4jClient = Depends(get_db),
) -> ContaminationResponse:
    """List the papers that cite a retracted paper, directly or up to ``depth`` steps away.

    Nearest papers come first. Direct citations say whether they were made before or after
    the retraction. Being listed means being exposed to the retracted work, not being wrong.
    """
    paper = find_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No paper with this DOI or OpenAlex id")
    if not paper["retracted"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This paper is not retracted, so there is no contamination to follow. "
            "Use /api/papers/{paper_id}/exposure to see if it relies on retracted work",
        )
    summary, items = contamination(db, paper["doi"], depth=depth, limit=limit, offset=offset)
    return ContaminationResponse(paper=paper, depth=depth, limit=limit, offset=offset, items=items, **summary)


@router.get(
    "/{paper_id}/exposure",
    summary="Whether a paper relies on retracted work",
    status_code=status.HTTP_200_OK,
    responses=NOT_READY_RESPONSE,
)
def exposure(paper_id: str) -> dict:
    """Whether a paper relies on retracted work. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
