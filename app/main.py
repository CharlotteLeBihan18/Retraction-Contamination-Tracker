"""FastAPI entry point: creates the app, opens the database, registers routers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable

from app import __version__
from app.config import get_settings
from app.database import Neo4jClient
from app.routers import ask, audit, communities, health, journals, papers, retractions, risk


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j driver at startup and close it at shutdown."""
    application.state.db = Neo4jClient.from_settings(get_settings())
    yield
    application.state.db.close()


app = FastAPI(
    title="Retraction Contamination Tracker",
    description=(
        "Maps how retracted scientific papers spread through the citation network. "
        "Results describe exposure and risk, never that a paper is invalid."
    ),
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(ServiceUnavailable)
async def database_unavailable(_request: Request, _exc: ServiceUnavailable) -> JSONResponse:
    """Any endpoint: answer 503 with a clear message when Neo4j cannot be reached."""
    return JSONResponse(status_code=503, content={"detail": "The graph database is not reachable, try again later"})


# Every router is registered here once, so nobody else needs to edit this file.
for module in (health, papers, journals, retractions, communities, risk, ask, audit):
    app.include_router(module.router)
