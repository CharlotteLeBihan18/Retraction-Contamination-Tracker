"""FastAPI dependencies shared by the routers."""

from fastapi import Request

from app.database import Neo4jClient


def get_db(request: Request) -> Neo4jClient:
    """Return the Neo4j client created at application startup.

    Tests replace this dependency with a fake client, so they can run
    without a database.
    """
    return request.app.state.db
