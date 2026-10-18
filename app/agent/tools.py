"""Tools the audit agent can call. Each one returns a plain dict the agent can read.

Owner: Person 3. Only the owner edits this file.

Every result says where it comes from and how much to trust it:

* ``source``: "graph" (our Neo4j), "openalex" (the live API) or "llm" (Gemini)
* ``kind``: "fact" for data, "hypothesis" for anything an LLM judged

Tools never raise on bad input or a refused query: they return ``{"error": ...}``
so the agent can read the problem and try something else.

``vector_search`` is not here yet: it needs the abstract embeddings and the
vector index that the ingestion script (Person 2) will create.
"""

import json
import re
import urllib.parse
import urllib.request
from typing import Any

from app.agent.ask import build_template
from app.agent.guardrails import GuardrailError, run_validated
from app.agent.llm import LLM, LLMUnavailableError
from app.config import get_settings
from app.database import Neo4jClient

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")
OPENALEX_URL = "https://api.openalex.org/works/doi:{doi}"
OPENALEX_TIMEOUT_S = 10
MIN_CONFIDENCE_TO_SAVE = 0.6
VERDICTS = ("relies_on", "mentions", "criticizes", "unclear")

FIND_BY_DOI = """
MATCH (p:Paper {doi: $doi})
OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(j:Journal)
RETURN p.doi AS doi, p.title AS title, p.year AS year, j.name AS journal, p:Retracted AS retracted
"""
FIND_BY_TITLE = """
MATCH (p:Paper)
WHERE toLower(p.title) CONTAINS toLower($title)
RETURN p.doi AS doi, p.title AS title, p.year AS year, p:Retracted AS retracted
ORDER BY p.year DESC
LIMIT 5
"""
SAVE_RELIES_ON = """
MATCH (c:Paper {doi: $citing})-[:CITES]->(r:Paper:Retracted {doi: $retracted})
MERGE (c)-[x:RELIES_ON]->(r)
SET x.confidence = $confidence, x.evidence = $evidence, x.verdict = $verdict,
    x.source = 'llm', x.model = $model, x.judged_at = datetime()
RETURN count(x) AS saved
"""

JUDGE_PROMPT = """You check whether a citing paper DEPENDS on a retracted paper's results.
You get the citing paper's abstract and the retracted paper's title.
Answer with ONE JSON object and nothing else:
{"verdict": "relies_on" | "mentions" | "criticizes" | "unclear",
 "confidence": a number from 0 to 1,
 "evidence": the ONE sentence copied word for word from the abstract that supports your verdict}
- "relies_on": the citing work builds on or reuses the retracted results
- "mentions": cites it as background only
- "criticizes": disputes or questions it
- "unclear": the abstract does not say
If no sentence supports a verdict, answer "unclear" with "evidence": ""."""


def _normalize_doi(doi: str) -> str:
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi


def _graph(db: Neo4jClient, cypher: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        rows = run_validated(db, cypher, params)
    except GuardrailError as exc:
        return {"error": str(exc), "refused": exc.reason, "source": "graph"}
    return {"rows": rows, "source": "graph", "kind": "fact"}


# ---------- Graph tools ----------


def find_paper(db: Neo4jClient, doi: str = "", title: str = "") -> dict[str, Any]:
    """Find a paper in our graph by DOI (exact) or by part of its title (up to 5 matches)."""
    if doi:
        return _graph(db, FIND_BY_DOI, {"doi": _normalize_doi(doi)})
    if title.strip():
        return _graph(db, FIND_BY_TITLE, {"title": title.strip()})
    return {"error": "Give a DOI or a title"}


def exposure_check(db: Neo4jClient, doi: str, depth: int = 2) -> dict[str, Any]:
    """Retracted papers this paper relies on through citations, within ``depth`` steps (max 3)."""
    cypher, params = build_template({"intent": "exposure", "doi": _normalize_doi(doi), "depth": depth})
    return _graph(db, cypher, params)


def contamination_check(db: Neo4jClient, doi: str, depth: int = 2) -> dict[str, Any]:
    """Papers that built on this RETRACTED paper, within ``depth`` citation steps (max 3)."""
    cypher, params = build_template({"intent": "contamination", "doi": _normalize_doi(doi), "depth": depth})
    return _graph(db, cypher, params)


def safe_cypher(db: Neo4jClient, query: str) -> dict[str, Any]:
    """Any other read-only graph question, through the same guardrails as /ask."""
    return _graph(db, query, {})


# ---------- OpenAlex ----------


def _abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inverted_index:
        return ""
    words = sorted((pos, word) for word, positions in inverted_index.items() for pos in positions)
    return " ".join(word for _, word in words)


def openalex_lookup(doi: str) -> dict[str, Any]:
    """Fetch a paper from OpenAlex when it is not in our graph (title, year, retraction flag, abstract)."""
    doi = _normalize_doi(doi)
    if not DOI_PATTERN.match(doi):
        return {"error": f"Not a valid DOI: {doi!r}"}
    url = OPENALEX_URL.format(doi=urllib.parse.quote(doi, safe="/"))
    key = get_settings().openalex_api_key.get_secret_value()
    if key:
        url += "?" + urllib.parse.urlencode({"api_key": key})
    try:
        with urllib.request.urlopen(url, timeout=OPENALEX_TIMEOUT_S) as response:  # nosec: fixed https host
            work = json.loads(response.read().decode("utf-8"))
    except OSError as exc:  # network errors and 404 (HTTPError is an OSError)
        return {"error": f"OpenAlex lookup failed: {exc}", "source": "openalex"}
    return {
        "doi": doi,
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "cited_by_count": work.get("cited_by_count"),
        "references": len(work.get("referenced_works") or []),
        # OpenAlex's flag has known errors; Retraction Watch (in our graph) wins when they disagree.
        "openalex_is_retracted": work.get("is_retracted"),
        "abstract": _abstract(work.get("abstract_inverted_index")),
        "source": "openalex",
        "kind": "fact",
    }


# ---------- Relies-on judgement ----------


def _parse_judgement(raw: str, abstract: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    verdict = data.get("verdict") if data.get("verdict") in VERDICTS else "unclear"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = str(data.get("evidence") or "").strip()
    # Anti-hallucination check: the quoted sentence must really be in the abstract.
    if verdict != "unclear" and (not evidence or evidence.lower() not in abstract.lower()):
        verdict, confidence, evidence = "unclear", 0.0, ""
    return {"verdict": verdict, "confidence": round(confidence, 2), "evidence": evidence}


def judge_reliance(llm: LLM, citing_abstract: str, retracted_title: str) -> dict[str, Any]:
    """Ask the LLM whether a citing abstract relies on, mentions or criticizes a retracted paper."""
    if not citing_abstract.strip():
        return {"verdict": "unclear", "confidence": 0.0, "evidence": "", "error": "No abstract to read"}
    try:
        raw = llm(JUDGE_PROMPT, f"Retracted paper: {retracted_title}\n\nCiting abstract: {citing_abstract}")
    except LLMUnavailableError as exc:
        return {"error": str(exc), "source": "llm"}
    return {**_parse_judgement(raw, citing_abstract), "source": "llm", "kind": "hypothesis"}


def save_relies_on(db: Neo4jClient, citing_doi: str, retracted_doi: str, judgement: dict[str, Any]) -> dict[str, Any]:
    """Store a confident "relies_on" judgement as a RELIES_ON link, marked as an AI hypothesis.

    Only saved when the citing paper really CITES the retracted one in our graph,
    so an AI guess can never create a link that the data does not support.
    """
    if judgement.get("verdict") != "relies_on" or judgement.get("confidence", 0) < MIN_CONFIDENCE_TO_SAVE:
        return {"saved": 0, "reason": "Not a confident 'relies_on' judgement"}
    rows = db.write(
        SAVE_RELIES_ON,
        {
            "citing": _normalize_doi(citing_doi),
            "retracted": _normalize_doi(retracted_doi),
            "confidence": judgement["confidence"],
            "evidence": judgement["evidence"],
            "verdict": judgement["verdict"],
            "model": get_settings().llm_model,
        },
    )
    saved = rows[0]["saved"] if rows else 0
    return {"saved": saved, "reason": "" if saved else "No CITES link between these papers in the graph"}