"""Load the hand-written sample graph (data/sample/sample.cypher) into Neo4j.

Run with:  python -m scripts.load_sample
"""

import sys
from pathlib import Path

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.config import Settings, get_settings

SAMPLE_FILE = Path(__file__).resolve().parent.parent / "data" / "sample" / "sample.cypher"


def read_statements(path: Path) -> list[str]:
    """Split a .cypher file into statements.

    Lines starting with ``//`` are comments and are dropped; statements end with ``;``.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("//")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def open_driver(settings: Settings) -> Driver:
    """Create a Neo4j driver from the application settings."""
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )


def load_sample(driver: Driver, database: str, path: Path = SAMPLE_FILE) -> int:
    """Run every statement of the sample file and return how many were run.

    The file starts by deleting the previous sample nodes, so running it twice is safe.
    """
    statements = read_statements(path)
    for statement in statements:
        driver.execute_query(statement, database_=database)
    return len(statements)


def main() -> int:
    """Load the sample graph and return a shell exit code."""
    settings = get_settings()
    driver = open_driver(settings)
    try:
        count = load_sample(driver, settings.neo4j_database)
    except (ServiceUnavailable, Neo4jError) as exc:
        print(f"Could not load the sample graph: {exc}")
        return 1
    finally:
        driver.close()
    print(f"Sample graph loaded ({count} statements).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
