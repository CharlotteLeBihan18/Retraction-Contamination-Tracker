"""Response models for the health endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Status of the API and of its database connection."""

    status: Literal["ok", "degraded"] = Field(description="'ok' when every dependency is up")
    version: str = Field(description="Application version")
    neo4j: Literal["up", "down"] = Field(description="Whether Neo4j answers")
