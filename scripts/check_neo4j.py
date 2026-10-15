"""Check that Neo4j is reachable and that the GDS and APOC plugins are installed.

Run with:  python -m scripts.check_neo4j
"""

import sys

from app.config import get_settings
from app.database import Neo4jClient


def main() -> int:
    """Print the Neo4j version and plugin versions; return a shell exit code."""
    client = Neo4jClient.from_settings(get_settings())
    try:
        if not client.is_available():
            print("Neo4j is not reachable. Is the stack running? (make docker-run)")
            return 1
        version = client.read("CALL dbms.components() YIELD name, versions RETURN name, versions[0] AS version")
        gds = client.read("RETURN gds.version() AS version")
        apoc = client.read("RETURN apoc.version() AS version")
        print(f"Neo4j: {version[0]['name']} {version[0]['version']}")
        print(f"GDS:   {gds[0]['version']}")
        print(f"APOC:  {apoc[0]['version']}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
