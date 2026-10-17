"""Ask: a plain-English question becomes a safe Cypher query.

Owner: Person 3. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter(prefix="/api", tags=["AI"])

NOT_READY = "Not implemented yet"
NOT_READY_RESPONSE = {501: {"description": NOT_READY}}


@router.get(
    "/ask", summary="Answer a question about the graph", status_code=status.HTTP_200_OK, responses=NOT_READY_RESPONSE
)
def ask(q: str = Query(..., min_length=1, description="Your question in plain English")) -> dict:
    """Answer a question about the graph. Returns 501 until implemented."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=NOT_READY)
