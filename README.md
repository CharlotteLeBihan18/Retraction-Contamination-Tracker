# Retraction Contamination Tracker

![Lint](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/actions/workflows/lint.yml/badge.svg)
![Tests](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/actions/workflows/test.yml/badge.svg)

A graph-backed application that maps how retracted scientific papers spread through the
citation network. Papers, citations and retractions are stored in Neo4j; a FastAPI service
exposes graph queries, and an LLM component answers questions in plain English.

The app reports **exposure and risk**, never that a paper is invalid: retraction is not
contagious, and citing a retracted paper can mean mentioning, criticising or relying on it.

> Status: in progress. Each section marked *TODO* names the team member who will write it
> (roles and task lists are in the project proposal).

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
.venv/bin/python -m scripts.check_neo4j
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

![Architecture: services, ports and data flows](docs/architecture.png)

How to read it (the numbers match the arrows):

1. **User requests** go to Nginx on port 80, which forwards them to the API on port 8000. The API is never exposed directly.
2. **AI requests** (`/api/ask` and `/api/audit` only) make the API call the LLM provider over HTTPS.
3. **Loading the data** runs once, not on each request: the scripts fetch Retraction Watch and OpenAlex, cache the raw files in `data/cache/`, then write them into Neo4j.
4. **Developers** can inspect the graph in the Neo4j Browser on port 7474.
5. **Delivery**: every push runs lint and tests in GitHub Actions, and the API image is published on Docker Hub.

The graph algorithms (PageRank, Louvain, FastRP) run on a GDS in-memory copy of the citation graph, built on the first request and dropped by the loading script after new data is loaded. The diagram source is `docs/architecture.drawio` (open it with [diagrams.net](https://app.diagrams.net)).

## Tests

`make test` runs three kinds of tests:

- **Unit and API tests**: run anywhere, using a fake database.
- **Neo4j tests** (`-m neo4j`): run against a real Neo4j, skipped when it is not reachable.
- **Container tests** (`-m containers`): check the running stack through Nginx, skipped when it is down.

In GitHub Actions, the test workflow starts its own Neo4j, so the Neo4j tests always run in CI.

## Data sources

*TODO (Person 2): describe and justify both sources.* [Retraction Watch](https://crossref.org/documentation/retrieve-metadata/retraction-watch/) (via Crossref) and [OpenAlex](https://openalex.org/).

## Graph schema and modeling rationale

*TODO (Person 2): schema diagram, modeling decisions, rejected alternatives and the anti-genericity appendix.*

## API examples

*TODO (everyone): one example per endpoint, written by its owner (see the team table in the proposal).*

## LLM component and guardrails

*TODO (Person 3): how Ask and the audit agent work, each guardrail, and the evaluation results.*

## Merged pull requests

Every change goes through a pull request reviewed by another team member (at least 2 constructive comments each). The full list is in the [closed pull requests](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pulls?q=is%3Apr+is%3Amerged).

| PR | What it adds | Author | Reviewer |
| --- | --- | --- | --- |
| [#1](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/1) | Placeholder files for every endpoint | Person 1 | Person 3 |
| [#2](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/2) | Zombie citations endpoint | Person 1 | Person 3 |
| [#3](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/3) | GDS in-memory projection | Person 1 | Person 3 |
| [#4](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/4) | Top spreaders endpoint (PageRank) | Person 1 | Person 3 |
| [#5](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/5) | Communities endpoint (Louvain) | Person 1 | Person 3 |

*Each author adds a row when their pull request is merged.*
