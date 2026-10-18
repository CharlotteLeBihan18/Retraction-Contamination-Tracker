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
3. **Loading the data** runs once, not on each request: the scripts fetch Retraction Watch and OpenAlex, cache the raw files in `data/cache/`, then write them into Neo4j, with an embedding of each abstract for vector search. `/api/ask` embeds the question with the same model, otherwise the vector search would silently return unrelated papers.
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

*Person 3. Code in `app/agent/`, endpoints `GET /api/ask` and `POST /api/audit`.*

### LLM setup

We use **Google Gemini** (free tier) through LangChain. The model is set in `.env`, never in the code:

```
LLM_PROVIDER=gemini
LLM_API_KEY=your-key          # free key from aistudio.google.com, never committed
LLM_MODEL=gemini-3.8-flash    # any Flash / Flash-Lite model works
```

Temperature is 0, so the same question gives the same answer, which makes testing and the demo repeatable. Every call goes through one small function (`app/agent/llm.py`), so tests replace the LLM with a fake and never touch the network. If the key is missing or the quota is exceeded, the endpoints answer **503** instead of crashing.

Free-tier note: Google may use free-tier prompts to improve its models. We only send public paper titles, abstracts and the user's question, never personal data.

### `/api/ask`: a question in plain English becomes a safe graph query

```
GET /api/ask?q=Which papers built on the retracted paper 10.0000/sample.001?
```

Hybrid design, in four steps:

1. **Route.** Gemini reads the question and returns a small JSON *intent*, e.g. `{"intent": "contamination", "doi": "10.0000/sample.001", "depth": 2}`.
2. **Query.** A known intent runs a **pre-written, tested Cypher template** (paper info, contamination, exposure, retraction reasons, most-cited retracted papers). Anything else falls back to **Cypher written by Gemini** from the graph schema.
3. **Guard.** Every query, template or not, goes through the guardrails below.
4. **Answer.** The response contains the exact Cypher that ran, the rows from the graph, the papers it cites, and a short summary written by Gemini.

Why hybrid: templates make the common questions reliable, fast and cheap, while the fallback keeps the endpoint useful for everything else. The `route` field says which path was taken (`template:<intent>` or `llm_cypher`).

**Facts vs AI guesses.** Rows from the graph are facts. The `answer` text is always flagged `answer_is_ai_generated: true`. If the query uses `RELIES_ON` links (which are AI judgements, see below), `evidence` is `"hypothesis"` instead of `"fact"`.

### Guardrails (`app/agent/guardrails.py`)

Nothing the LLM writes reaches Neo4j without passing every check. A refused query returns **422** with the reason.

| Check | What it blocks | Reason code |
| --- | --- | --- |
| Shape | several statements (`;`), backtick-quoted names (they can hide keywords), empty queries | `shape` |
| Read-only | `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`, `FOREACH`, `LOAD CSV`, `CALL { }` subqueries | `write` |
| Procedures | every procedure (`dbms.*`, `apoc.*`, `gds.*`, `db.labels()`…) except the vector search | `procedure` |
| Schema | any label or relationship type outside the 6 + 6 of our graph model (`:User`, `:paper`, `[:LIKES]`…) | `schema` |
| Cost | variable-length paths without an upper bound or longer than 3 hops | `cost` |
| Limit | adds `LIMIT 100` when missing and lowers bigger limits | (rewritten, not refused) |
| Syntax | an `EXPLAIN` dry run first: a query Neo4j cannot parse never executes | `syntax` |
| Time | the real query runs with a 5-second timeout | |

Strings and comments are masked before checking, so a title like *"How to SET up a lab"* is not mistaken for a `SET` clause. As a second layer, the Neo4j client runs these queries in **read mode**, so the database itself refuses writes even if one slipped through. The guardrails have 50 unit tests, including 9 edge cases found by our reviewer while trying to break them.

### `/api/audit`: an integrity report with every step (LangGraph agent)

```
POST /api/audit   {"doi": "10.0000/sample.023"}
```

A LangGraph state machine that calls tools from `app/agent/tools.py`:

```
locate ──► (not found) ─────────────────────────────┐
   │                                                 ▼
   ├──► (retracted) contamination ──► judge ──► report
   └──► (not retracted) exposure ───┘
```

1. **locate:** find the paper in our graph, or else on OpenAlex
2. **exposure** (normal paper): retracted papers it relies on, up to 2 citation steps away. **contamination** (retracted paper): papers that built on it
3. **judge:** for each *direct* citation, Gemini reads the citing paper's abstract and decides whether it **relies on**, only **mentions**, or **criticizes** the retracted work, with a confidence and a sentence quoted from the abstract. Capped at 5 LLM calls per audit.
4. **report:** a short summary written by Gemini from the findings only

The response separates **facts** (`exposure`, `contamination`) from **hypotheses** (`hypotheses`, `report`), and lists every tool call in `steps`, so the reasoning can be checked.

**Tools:** `find_paper`, `exposure_check`, `contamination_check`, `safe_cypher` (same guardrails as /ask), `openalex_lookup`, `judge_reliance`. Each result is tagged with its `source` (graph, openalex, llm) and `kind` (fact, hypothesis). Tools never crash the agent: a failure is recorded as a step and the audit continues.

**RELIES_ON links (AI hypotheses).** A judgement is saved as a `RELIES_ON` relationship only when:
- the verdict is `relies_on` with confidence ≥ 0.6, **and**
- the quoted sentence really appears in the abstract (otherwise the verdict becomes `unclear`: our anti-hallucination check), **and**
- a real `CITES` link exists between the two papers in our data.

So an AI guess can never create a connection the data does not support. Each link stores its confidence, the evidence sentence, the model name and the date.

### Evaluation

`python -m app.agent.evaluation` runs 20 cases with the real LLM against the sample graph: 12 normal questions (with the expected route), 5 questions that must be refused, and 3 audits.

*TODO: paste the table printed by the script here.*

### Limitations

- The router can pick the wrong template for an ambiguous question; the `route` field makes this visible.
- Relies-on judgements only read abstracts, not full texts, so "unclear" is common and expected.
- The free LLM tier has a daily quota; the endpoints return 503 when it is reached.
- Vector search over abstracts is not wired in yet (it needs the embeddings created at ingestion).
## Merged pull requests

Every change goes through a pull request reviewed by another team member (at least 2 constructive comments each). The full list is in the [closed pull requests](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pulls?q=is%3Apr+is%3Amerged).

| PR | What it adds | Author | Reviewer |
| --- | --- | --- | --- |
| [#1](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/1) | Placeholder files for every endpoint | Person 1 | Person 3 |
| [#2](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/2) | Zombie citations endpoint | Person 1 | Person 3 |
| [#3](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/3) | GDS in-memory projection | Person 1 | Person 3 |
| [#4](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/4) | Top spreaders endpoint (PageRank) | Person 1 | Person 3 |
| [#5](https://github.com/CharlotteLeBihan18/Retraction-Contamination-Tracker/pull/5) | Communities endpoint (Louvain) | Person 1 | Person 3 |

*To avoid Git conflicts, only Person 1 edits this file: each author writes their row in their pull request description, and Person 1 adds the rows together.*

