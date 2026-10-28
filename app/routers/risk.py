"""At-risk prediction for a paper (FastRP embeddings + classifier).

Owner: Lara Normand. Only the owner edits this file.
"""

from fastapi import APIRouter, HTTPException, status

from app.agent.tools import normalize_doi
from app.models.risk import ModelInfo, RiskResponse
from app.services.risk import ModelNotTrainedError, load_model

router = APIRouter(prefix="/api/papers", tags=["machine learning"])

EXPLANATION = (
    "Probability that this paper looks like the retracted papers in our graph, from its citation "
    "neighbourhood (FastRP), its year and its number of references. It is an AI hypothesis to "
    "prioritise checks, never evidence that the paper is flawed."
)
INFO_METRICS = ("precision", "recall", "f1", "roc_auc", "baseline_precision")


@router.get(
    "/{paper_id:path}/risk",
    response_model=RiskResponse,
    summary="Predicted probability that a paper is at risk",
    responses={
        404: {"description": "The paper was not in the graph when the model was trained"},
        503: {"description": "The model has not been trained yet"},
    },
)
def paper_risk(paper_id: str) -> RiskResponse:
    """Return the model's probability for a paper (by DOI), labelled as a hypothesis."""
    try:
        model = load_model()
    except ModelNotTrainedError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    doi = normalize_doi(paper_id)
    if doi not in model.scores:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{doi} was not in the graph when the model was trained ({model.trained_at})",
        )
    info = ModelInfo(
        trained_at=model.trained_at,
        n_papers=model.n_papers,
        n_retracted=model.n_retracted,
        **{name: model.metrics[name] for name in INFO_METRICS},
    )
    return RiskResponse(
        doi=doi,
        risk_score=model.scores[doi],
        already_retracted=doi in model.retracted,
        explanation=EXPLANATION,
        model=info,
    )
