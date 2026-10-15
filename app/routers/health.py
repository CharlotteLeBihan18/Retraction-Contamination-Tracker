"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status

from app import __version__
from app.database import Neo4jClient
from app.dependencies import get_db
from app.models.health import HealthResponse

router = APIRouter(tags=["health"])


def _build_status(db: Neo4jClient) -> HealthResponse:
    """Check the database and summarise the overall status."""
    neo4j_up = db.is_available()
    return HealthResponse(
        status="ok" if neo4j_up else "degraded",
        version=__version__,
        neo4j="up" if neo4j_up else "down",
    )


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
def health(db: Neo4jClient = Depends(get_db)) -> HealthResponse:
    """Always answer 200 while the API process runs, and report Neo4j's state.

    Docker uses this endpoint to know the API container is alive.
    """
    return _build_status(db)


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    summary="Readiness check",
    responses={503: {"description": "Neo4j is not reachable"}},
)
def ready(db: Neo4jClient = Depends(get_db)) -> HealthResponse:
    """Answer 200 only when the API can serve graph queries, 503 otherwise."""
    result = _build_status(db)
    if result.neo4j == "down":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Neo4j is not reachable")
    return result
