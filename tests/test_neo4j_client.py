"""Unit tests for Neo4jClient, with the real driver replaced by a mock."""

from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import ServiceUnavailable
from pydantic import SecretStr

from app.config import Settings
from app.database import neo4j_client
from app.database.neo4j_client import Neo4jClient


@pytest.fixture
def mock_driver(monkeypatch):
    """Patch GraphDatabase.driver so no network connection is attempted."""
    driver = MagicMock()
    monkeypatch.setattr(neo4j_client.GraphDatabase, "driver", MagicMock(return_value=driver))
    return driver


def test_from_settings_passes_credentials(mock_driver):
    settings = Settings(neo4j_uri="bolt://db:7687", neo4j_user="u", neo4j_password=SecretStr("p"))
    Neo4jClient.from_settings(settings)
    neo4j_client.GraphDatabase.driver.assert_called_once_with("bolt://db:7687", auth=("u", "p"))


def test_is_available_true_when_driver_connects(mock_driver):
    assert Neo4jClient("bolt://x", "u", "p").is_available() is True


def test_is_available_false_instead_of_raising(mock_driver):
    mock_driver.verify_connectivity.side_effect = ServiceUnavailable("down")
    assert Neo4jClient("bolt://x", "u", "p").is_available() is False


def test_read_returns_dicts_and_uses_parameters(mock_driver):
    record = MagicMock()
    record.data.return_value = {"n": 1}
    mock_driver.execute_query.return_value = ([record], None, None)

    rows = Neo4jClient("bolt://x", "u", "p", database="neo4j").read("RETURN $n AS n", {"n": 1})

    assert rows == [{"n": 1}]
    args, kwargs = mock_driver.execute_query.call_args
    assert args == ("RETURN $n AS n", {"n": 1})
    assert kwargs["database_"] == "neo4j"


def test_close_closes_driver(mock_driver):
    Neo4jClient("bolt://x", "u", "p").close()
    mock_driver.close.assert_called_once()


def test_secrets_are_hidden_in_repr():
    settings = Settings(neo4j_password=SecretStr("super-secret"))
    assert "super-secret" not in repr(settings)
