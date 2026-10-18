"""Audit: an integrity report for one paper, built by the LangGraph agent.

Owner: Person 3. Only the owner edits this file.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j.exceptions import ServiceUnavailable

from app.agent.graph import run_audit
from app.agent.llm import LLM, get_llm
from app.agent.tools import DOI_PATTERN, normalize_doi
from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.audit import AuditRequest, AuditResponse, Judgement

router = APIRouter(prefix="/api", tags=["AI"])
JUDGEMENT_FIELDS = set(Judgement.model_fields)


@router.post(
    "/audit",
    response_model=AuditResponse,
    summary="Integrity report for a paper, with every tool call",
    responses={
        422: {"description": "Not a valid DOI"},
        503: {"description": "Neo4j is not reachable"},
    },
)
def audit(body: AuditRequest, db: Neo4jClient = Depends(get_db), llm: LLM = Depends(get_llm)) -> AuditResponse:
    """Run the audit agent: locate the paper, check exposure or contamination, judge reliance, report.

    ``exposure`` and ``contamination`` are facts from the graph; ``hypotheses`` and
    ``report`` come from the LLM and are labelled as such.
    """
    doi = normalize_doi(body.doi)
    if not DOI_PATTERN.match(doi):
        raise HTTPException(status_code=422, detail=f"Not a valid DOI: {body.doi!r}")
    try:
        state = run_audit(doi, db, llm, save_hypotheses=body.save_hypotheses)
    except ServiceUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return AuditResponse(
        doi=doi,
        found_in=state.get("found_in", "nowhere"),
        paper=state.get("paper"),
        retracted=state.get("retracted", False),
        exposure=state.get("exposure", []),
        contamination=state.get("contamination", []),
        hypotheses=[
            Judgement(**{k: v for k, v in j.items() if k in JUDGEMENT_FIELDS}) for j in state.get("judgements", [])
        ],
        report=state.get("report", ""),
        steps=state.get("steps", []),
    )
