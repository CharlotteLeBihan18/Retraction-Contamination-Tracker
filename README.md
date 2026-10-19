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

We use two sources and join them on the DOI of each paper.

- **[Retraction Watch](https://crossref.org/documentation/retrieve-metadata/retraction-watch/)** (a CSV, distributed through Crossref) tells us which papers were retracted, when, and why. We treat it as the reference for retractions. We used commit `5338abda2966b2da8b0a84e189f805867bfc5171` of the open dataset.
- **[OpenAlex](https://openalex.org/)** (an API) gives us what Retraction Watch doesn't have: abstract, authors, journal, publication date and the list of works each paper cites.

We only kept one field, Dentistry, so the graph stays small enough to explore and to explain during the demo.

`python -m scripts.fetch_openalex` downloads everything into `data/cache/`. We commit that folder on purpose, so nobody needs an API key to rerun the project. `python -m scripts.ingest` then loads the cache into Neo4j. You can run it twice, you get the same graph (we use `MERGE`).

### How we matched the two sources (and what we decided)

Matching papers between two datasets is messy, so here is what we chose and why.

- **Only real retractions.** Out of 519 Dentistry rows, 16 are not retractions (corrections, expressions of concern), so we dropped them.
- **No DOI, no paper.** 46 rows had no DOI. The DOI is the only reliable key between the two sources, so we dropped them too. That leaves 457 papers.
- **Fixing broken DOIs.** Some DOIs in the CSV were URL-encoded (`%3c` instead of `<`) or written twice in a row. We repair them and store everything in lowercase, otherwise OpenAlex doesn't find the paper.
- **Matching by DOI only.** 453 of the 457 were found in OpenAlex. The 4 others are listed in `data/cache/manifest.json` and left out. We preferred losing 4 papers over guessing with fuzzy title matching and merging the wrong ones.
- **When the sources disagree, Retraction Watch wins.** For 2 papers OpenAlex doesn't flag them as retracted, but Retraction Watch does, so they stay `Retracted`.
- **One notice per source.** Each paper gets a `RetractionNotice` from Retraction Watch (with the date and the reasons) and one from OpenAlex (no date, OpenAlex doesn't give one). That way we keep track of who says what.
- **Cleaning the reasons.** Retraction Watch writes them like `+Plagiarism;+Fake Peer Review;`. We split them into separate `Reason` nodes, shared between papers, so we can count and compare them.
- **Journal names come from OpenAlex**, with the Retraction Watch name as a fallback. The citing papers also come from OpenAlex, so we get one spelling per journal.
- **Authors without an OpenAlex id are skipped** (147 links out of 2115). Without an id, two people with the same name would end up merged.
- **`before_retraction` is computed once, when loading.** We compare the full dates when we have them. If we only know the year and it is the same as the retraction year, we say "before". A citation is only called a zombie when we are sure it came after the retraction.
- **Missing abstracts.** OpenAlex has no abstract for 91 of the 453 papers (362 have one). They are loaded with an empty abstract, so any search that only reads abstracts cannot find them. Searching on title + abstract fixes that.

### The papers that cite them

A retracted paper alone doesn't tell us much, what we care about is who cited it. So `python -m scripts.fetch_citers` asks OpenAlex for every paper that cites one of our 453 retracted papers (50 ids per request, with paging), and saves the pages in `data/cache/citers/`. `python -m scripts.ingest` loads them together with the retracted papers.

- **8,881 citing papers** are loaded, with their authors and journals, and **10,033 citations** to the retracted papers (plus the 106 citations between two retracted papers). OpenAlex returned 8,942 records: 61 were skipped because they are one of our retracted papers already, or their DOI was already taken.
- **Every citation to a retracted paper has `before_retraction`**, with the same rule as above. 4,861 of the 10,139 citations came after the retraction (zombie citations).
- **379 citing papers have no DOI**, so papers are also keyed by their OpenAlex id.
- **OpenAlex flags 208 citing papers as retracted, we don't.** Retraction Watch has no notice for them, so we have no date and no reasons. They stay normal papers and we keep the flag as `openalex_retracted = true`, so the information isn't lost.
- **Citing papers have no abstract in the graph.** We didn't need them for the graph questions, and the audit agent fetches the abstract from OpenAlex when it judges a citation.
- **464 citing papers have no journal** in OpenAlex, so they have no `PUBLISHED_IN` link. They are still in the graph and in Contamination / Exposure, but their 250 zombie citations don't show up in the Zombie citations endpoint, because it groups the citations by journal.
- **We stop at one level.** We didn't download the papers that cite the citing papers: it would multiply the size of the graph for a small demo gain. So a contamination path of 2 or 3 steps in our data goes through another retracted paper.

**A real zombie example.** The paper with the most zombie citations in our data is *"Marsupialization as a Definitive Treatment for the Odontogenic Keratocyst"* (2004). It was retracted in February 2007, but it's still cited every year until 2026: 268 citations came after the retraction, 25 of them in 2021 alone. This query finds it:

```cypher
MATCH (c:Paper)-[:CITES {before_retraction: false}]->(r:Paper:Retracted)
RETURN r.title AS title, r.doi AS doi, count(c) AS zombie_citations
ORDER BY zombie_citations DESC
LIMIT 1
```

## Graph schema and modeling rationale

![Graph schema](docs/graph_schema.png)

The full version, with every property, an example on the sample graph and all our decisions, is in [`docs/graph_model.md`](docs/graph_model.md). Here are the main ideas:

- **`Retracted` is a label on `Paper`, not a separate node and not a boolean.** A retracted paper is still a paper (it gets cited, has authors, can cite other retracted papers). With a label, queries can start from the 456 retracted papers instead of reading all 9,354.
- **A retraction is a `RetractionNotice` with a `source`.** Retraction Watch and OpenAlex don't always agree, so we keep one notice per source and let Retraction Watch decide.
- **Reasons are nodes.** 351 notices give 2 to 11 reasons, and as nodes they are easy to count.
- **`before_retraction` is stored on `CITES`.** It's computed once when loading, so the zombie-citation queries just compare a boolean.
- **`RELIES_ON` is separate from `CITES`.** A citation is a fact, "it relies on it" is an AI guess with a confidence score. Fact-only queries never touch it.
- **Authors and papers use OpenAlex ids.** 403 author names are shared by 926 different people in our data, so a name can't be a key.

### Indexes

Unique constraints on `Paper.doi`, `Paper.openalex_id`, `Author.openalex_id`, `Journal.name` and `Reason.name`, and an index on `Paper.year`, all created before loading. `python -m scripts.benchmark_index` measures them with `PROFILE`, before and after adding them ([full report](docs/index_benchmark.md)):

| Query | Before (no index) | After (index) |
| --- | --- | --- |
| Find a paper by DOI | 18,710 db hits | 3 db hits |
| Direct citers of a retracted paper | 2,493 db hits (scans only the `:Retracted` papers) | 1,127 db hits |
| Papers of one year | 18,709 db hits | 821 db hits |

### Appendix: our schema vs a basic AI-generated one

We gave free ChatGPT our project description (the "project in simple terms" text with its 3 questions) and asked it for a Neo4j schema. Its answer is a good start: a separate `Retraction` node with a source, citations in the right direction, an id for papers without a DOI. But it doesn't fit our data and our questions in a few places:

| ChatGPT's schema | Problem with our data | What we did |
| --- | --- | --- |
| Zombie citations computed in every query with `citation_date > retraction_date` | OpenAlex has no citation date, sometimes only a year. A missing date silently drops the citation | `before_retraction` computed once, with a clear rule and a final check |
| `reason` as one string | 351 notices have several reasons | `Reason` nodes |
| Retracted = "has a retraction", from any source | OpenAlex alone would add 208 retracted papers with no date | `:Retracted` label from Retraction Watch only |
| No label for retracted papers | An extra hop in every query | `(:Paper:Retracted)` |
| Exposure up to 5 citation steps | The number of paths explodes | depth capped at 3 |
| No place for AI judgements | Guesses would end up mixed with facts | `RELIES_ON` with confidence and evidence |

The full comparison, with what ChatGPT got right and the ideas we chose not to use, is in [`docs/graph_model.md`](docs/graph_model.md#comparison-with-a-basic-ai-generated-schema).

## API examples

*TODO (everyone): one example per endpoint, written by its owner (see the team table in the proposal).*

**Contamination** (Person 2): which papers built on a retracted paper, up to `depth` steps (1 to 3).

```bash
curl "http://localhost/api/papers/10.1002/14651858.cd001724.pub2/contamination?depth=2&limit=20"
```

On our data this paper has 373 direct citers, 118 of them after its retraction, and 383 papers at depth 2. Each paper comes with `hops`, and direct citations with `before_retraction`. Unknown DOI → 404, `depth=5` → 422.

**Exposure** (Person 2): does a paper rely on retracted work, and through which chain.

```bash
curl "http://localhost/api/papers/10.0000/sample.023/exposure?depth=3"
```

On the sample graph, `sample.023` reaches the retracted `sample.001` in 3 steps, and the answer shows the chain of papers in between. A paper that reaches nothing gets `"exposed": false` (that's an answer, not an error).

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

*To avoid Git conflicts, only Person 1 edits this file: each author writes their row in their pull request description, and Person 1 adds the rows together.*
