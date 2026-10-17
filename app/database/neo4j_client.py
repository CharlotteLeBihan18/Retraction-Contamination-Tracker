"""Thin wrapper around the official Neo4j Python driver."""

import logging
from typing import Any

from neo4j import Driver, GraphDatabase, RoutingControl
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.config import Settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Owns one Neo4j driver for the lifetime of the application.

    The driver keeps a connection pool, so it is created once at startup and
    shared by every request instead of opening a connection per request.
    """

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        self._database = database
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def from_settings(cls, settings: Settings) -> "Neo4jClient":
        """Build a client from the application settings."""
        return cls(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password.get_secret_value(),
            database=settings.neo4j_database,
        )

    def is_available(self) -> bool:
        """Return True when the database answers, False instead of raising."""
        try:
            self._driver.verify_connectivity()
            return True
        except (ServiceUnavailable, Neo4jError, OSError) as exc:
            logger.warning("Neo4j is not reachable: %s", exc)
            return False

    def read(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read-only query and return its records as plain dictionaries.

        Queries always take ``parameters`` instead of string formatting, which
        prevents Cypher injection and lets Neo4j cache the query plan.
        """
        records, _, _ = self._driver.execute_query(
            query,
            parameters or {},
            database_=self._database,
            routing_=RoutingControl.READ,
        )
        return [record.data() for record in records]

    def write(self, query: str, parameters: dict[str, Any] | None = None) -> None:
        """Run a write query. Used by the ingestion scripts and by tests, never by endpoints."""
        self._driver.execute_query(
            query,
            parameters or {},
            database_=self._database,
            routing_=RoutingControl.WRITE,
        )

    def close(self) -> None:
        """Close the driver and its connection pool."""
        self._driver.close()
