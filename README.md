# Retraction Contamination Tracker

<!-- Replace YOUR-USERNAME/YOUR-REPO once the GitHub repository exists -->
![Lint](https://github.com/YOUR-USERNAME/YOUR-REPO/actions/workflows/lint.yml/badge.svg)
![Tests](https://github.com/YOUR-USERNAME/YOUR-REPO/actions/workflows/test.yml/badge.svg)

A graph-backed application that maps how retracted scientific papers spread through the
citation network. Papers, citations and retractions are stored in Neo4j; a FastAPI service
exposes graph queries, and an LLM component answers questions in plain English.

The app reports **exposure and risk**, never that a paper is invalid: retraction is not
contagious, and citing a retracted paper can mean mentioning, criticising or relying on it.

> Status: milestone M1 (project skeleton). Sections marked *TODO* are filled in later milestones.

## Quick start

Requirements: Docker Desktop, Python 3.11+, make, git.

```bash
cp .env.example .env        # then set NEO4J_PASSWORD (8+ characters)
make install                # create .venv and install dependencies
make docker-run             # start Neo4j, the API and Nginx
```

| Service | URL |
| --- | --- |
| API docs (Swagger, through Nginx) | http://localhost/docs |
| Health check | http://localhost/health/ready |
| Neo4j Browser | http://localhost:7474 |

Check that Neo4j and its plugins are ready:

```bash
. .venv/bin/activate && python -m scripts.check_neo4j
```

## Makefile commands

| Command | What it does |
| --- | --- |
| `make help` | List all commands |
| `make install` | Create `.venv` and install runtime + dev dependencies |
| `make run` | Run the API locally with auto-reload on :8000 |
| `make docker-run` | Start the full stack in the background |
| `make docker-down` | Stop the stack (data is kept) |
| `make docker-logs` | Follow the logs |
| `make test` | Run all tests with coverage (fails below 60%) |
| `make test-unit` | Run only tests that need no running services |
| `make lint` | Run pylint (fails below 9.5) |
| `make format` | Format the code with black |
| `make clean` | Remove caches |

## Architecture

- **neo4j** (`neo4j:5.26`, official image): graph database with the GDS and APOC plugins. Ports 7474 and 7687.
- **api** (built from `Dockerfile`): FastAPI service, reachable only inside the Docker network.
- **nginx** (`nginx:1.27-alpine`): reverse proxy and the single public entry point (port 80), with rate limiting and security headers.

*TODO (M6): architecture diagram.*

## Tests

`make test` runs three kinds of tests:

- **Unit and API tests**: run anywhere, using a fake database.
- **Neo4j tests** (`-m neo4j`): run against a real Neo4j, skipped when it is not reachable.
- **Container tests** (`-m containers`): check the running stack through Nginx, skipped when it is down.

In GitHub Actions, the test workflow starts its own Neo4j, so the Neo4j tests always run in CI.

## Data sources

*TODO (M2):* [Retraction Watch](https://crossref.org/documentation/retrieve-metadata/retraction-watch/) (via Crossref) and [OpenAlex](https://openalex.org/).

## Graph schema and modeling rationale

*TODO (M2/M6).*

## API examples

*TODO (M3).*

## LLM component and guardrails

*TODO (M4).*
