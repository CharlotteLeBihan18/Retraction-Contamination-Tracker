"""Contamination and exposure traversals around a paper.

Owner: Person 2. Only the owner edits this file.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.papers import ContaminationResponse, ExposureResponse
from app.services.papers import MAX_DEPTH, contamination, exposure, find_paper

router = APIRouter(prefix="/api/papers", tags=["papers"])

NOT_FOUND = "No paper with this DOI or OpenAlex id"
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if not paper["retracted"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This paper is not retracted, so there is no contamination to follow. "
            "Use /api/papers/{paper_id}/exposure to see if it relies on retracted work",
        )
    summary, items = contamination(db, paper["doi"], depth=depth, limit=limit, offset=offset)
    return ContaminationResponse(paper=paper, depth=depth, limit=limit, offset=offset, items=items, **summary)


@router.get(
    "/{paper_id:path}/exposure",
    response_model=ExposureResponse,
    summary="Whether a paper relies on retracted work",
    responses={404: {"description": "Paper not found"}},
)
def exposure_endpoint(
    paper_id: str = Path(description=PAPER_ID_HELP),
    depth: int = Query(MAX_DEPTH, ge=1, le=MAX_DEPTH, description="How many citation steps to follow"),
    limit: int = Query(20, ge=1, le=100, description="Retracted papers per page"),
    offset: int = Query(0, ge=0, description="How many retracted papers to skip (pagination)"),
    db: Neo4jClient = Depends(get_db),
) -> ExposureResponse:
    """List the retracted papers that a paper cites, directly or up to ``depth`` steps away.

    Each one comes with the shortest citation chain that leads to it. ``exposed`` is false when
    no retracted paper is reached: that is an answer, not an error. Exposed is not the same as wrong.
    """
    paper = find_paper(db, paper_id)
    if paper is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    summary, items = exposure(db, paper["node_id"], depth=depth, limit=limit, offset=offset)
    return ExposureResponse(
        paper=paper,
        paper_is_retracted=paper["retracted"],
        depth=depth,
        exposed=summary["total"] > 0,
        limit=limit,
        offset=offset,
        items=items,
        **summary,
    )
