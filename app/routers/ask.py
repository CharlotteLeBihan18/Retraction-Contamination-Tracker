"""Ask: a plain-English question becomes a safe Cypher query.

Owner: Person 3. Only the owner edits this file.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from neo4j.exceptions import ServiceUnavailable

from app.agent.ask import answer_question
from app.agent.guardrails import GuardrailError
from app.agent.llm import LLM, LLMUnavailableError, get_llm
from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.ask import AskResponse

router = APIRouter(prefix="/api", tags=["AI"])


@router.get(
    "/ask",
    response_model=AskResponse,
    summary="Answer a question about the graph",
    responses={
        422: {"description": "Empty question, or the query was refused by the guardrails (see detail.reason)"},
        503: {"description": "The LLM or Neo4j is not reachable"},
    },
)
def ask(
    q: str = Query(..., min_length=1, max_length=500, description="Your question in plain English"),
    db: Neo4jClient = Depends(get_db),
    llm: LLM = Depends(get_llm),
) -> AskResponse:
    """Turn the question into a graph query, check it with the guardrails, run it and answer.

    The rows are facts from the graph; the ``answer`` text is written by the LLM
    and always labelled as AI-generated.
    """
    try:
        return answer_question(q, db, llm)
    except GuardrailError as exc:
        raise HTTPException(
            status_code=422,
            detail={"reason": exc.reason, "message": str(exc)},
        ) from exc
    except (LLMUnavailableError, ServiceUnavailable) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
