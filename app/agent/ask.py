"""Ask: a plain-English question becomes a safe graph query, then a cited answer.

Owner: Person 3. Only the owner edits this file.

Hybrid design, in four steps:

1. Route: the LLM reads the question and returns a small JSON *intent*
   (for example ``{"intent": "exposure", "doi": "10.1/x", "depth": 2}``).
2. Query: a known intent runs a pre-written, tested Cypher template. Anything
   else falls back to Cypher written by the LLM from the graph schema.
3. Guard: every query, template or not, goes through ``run_validated``
   (read-only, known schema, bounded paths, LIMIT, EXPLAIN, timeout).
4. Answer: the rows (facts from the graph), the papers they cite, and a short
   summary written by the LLM, which is always labelled as AI-generated.

Templates make the common questions reliable and cheap; the fallback keeps
the endpoint useful for everything else, and the guardrails make it safe.
"""

import json
import re
from typing import Any

from app.agent.guardrails import run_validated, uses_hypotheses, validate
from app.agent.llm import LLM, LLMUnavailableError
from app.database import Neo4jClient
from app.models.ask import AskResponse, CitedPaper

MAX_DEPTH = 3
SUMMARY_ROWS = 20  # rows shown to the LLM when it writes the summary

# Pre-written queries. In "contamination", ``before_retraction`` is only set for direct
# citations: false means the paper cited the work after it was retracted (a zombie citation).
# ``{depth}`` is filled with a checked integer (1 to 3),
# never with text from the question; the DOI is always a query parameter.
TEMPLATES: dict[str, str] = {
    "paper_info": """
MATCH (p:Paper {doi: $doi})
OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(j:Journal)
OPTIONAL MATCH (n:RetractionNotice)-[:RETRACTS]->(p)
OPTIONAL MATCH (n)-[:FOR_REASON]->(r:Reason)
RETURN p.doi AS doi, p.title AS title, p.year AS year, j.name AS journal,
       p:Retracted AS retracted, collect(DISTINCT r.name) AS reasons
""",
    "contamination": """
MATCH path = (c:Paper)-[:CITES*1..{depth}]->(r:Paper:Retracted {doi: $doi})
WITH c, min(length(path)) AS hops
OPTIONAL MATCH (c)-[direct:CITES]->(:Paper {doi: $doi})
RETURN c.doi AS doi, c.title AS title, hops, direct.before_retraction AS before_retraction
ORDER BY hops, title
""",
    "exposure": """
MATCH path = (p:Paper {doi: $doi})-[:CITES*1..{depth}]->(r:Paper:Retracted)
WITH p, r, min(length(path)) AS hops
OPTIONAL MATCH (p)-[direct:CITES]->(r)
RETURN r.doi AS doi, r.title AS title, hops, direct.before_retraction AS before_retraction
ORDER BY hops, title
""",
    "retraction_reasons": """
MATCH (n:RetractionNotice)-[:FOR_REASON]->(r:Reason)
RETURN r.name AS reason, count(DISTINCT n) AS retractions
ORDER BY retractions DESC
""",
    "most_cited_retracted": """
MATCH (c:Paper)-[:CITES]->(r:Paper:Retracted)
RETURN r.doi AS doi, r.title AS title, count(c) AS citers
ORDER BY citers DESC
""",
}
NEEDS_DOI = {"paper_info", "contamination", "exposure"}
NEEDS_DEPTH = {"contamination", "exposure"}

ROUTER_PROMPT = """You route questions about a citation graph of scientific papers and retractions.
Answer with ONE JSON object and nothing else.

Intents:
- "paper_info": details about one paper (needs "doi")
- "contamination": which papers built on a RETRACTED paper, directly or a few steps away (needs "doi")
- "exposure": does a paper rely on retracted work within a few citation steps (needs "doi")
- "retraction_reasons": the most common reasons for retraction
- "most_cited_retracted": retracted papers that are cited the most
- "other": anything else

Fields: "intent" (required), "doi" (only if a DOI like 10.xxxx/... appears in the question),
"depth" (1 to 3, default 2, only for contamination and exposure).
Example: {"intent": "exposure", "doi": "10.1016/j.cell.2020.01.001", "depth": 2}"""

SCHEMA = """Nodes: (:Paper {doi, title, year, abstract}), extra label :Retracted on withdrawn papers,
(:RetractionNotice {date, source}), (:Reason {name}), (:Author {name, openalex_id}), (:Journal {name}).
Relationships: (:Paper)-[:CITES {before_retraction}]->(:Paper), (:RetractionNotice)-[:RETRACTS]->(:Paper),
(:RetractionNotice)-[:FOR_REASON]->(:Reason), (:Author)-[:AUTHORED]->(:Paper),
(:Paper)-[:PUBLISHED_IN]->(:Journal),
(:Paper)-[:RELIES_ON {confidence, evidence}]->(:Paper:Retracted) which is an AI guess, not a fact."""

CYPHER_PROMPT = f"""You write ONE read-only Neo4j Cypher query that answers the user's question.
Graph schema:
{SCHEMA}

Rules: only MATCH / OPTIONAL MATCH / WHERE / WITH / RETURN / ORDER BY / LIMIT.
Never write, delete or call procedures. Use only the labels and relationships above.
Variable-length paths must be bounded, at most *1..{MAX_DEPTH}. Return readable columns
with aliases (doi, title, ...). Do not use RELIES_ON unless the question asks about reliance.
Answer with the Cypher query only, no explanation, no code fence."""

SUMMARY_PROMPT = """You explain graph query results to a researcher in at most 3 sentences.
Use ONLY the rows given; never add facts that are not in them. If there are no rows, say
nothing was found. Mention DOIs when they appear. Never say a paper is invalid or should be
retracted: talk about exposure and risk."""


def _strip_fences(text: str) -> str:
    """Remove ```json / ```cypher fences that LLMs like to add."""
    return re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip()).strip()


def parse_intent(raw: str) -> dict[str, Any]:
    """Read the router's JSON. Anything unreadable or unknown becomes {"intent": "other"}."""
    match = re.search(r"\{.*\}", _strip_fences(raw), re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict) or data.get("intent") not in TEMPLATES:
        return {"intent": "other"}
    if data["intent"] in NEEDS_DOI and not isinstance(data.get("doi"), str):
        return {"intent": "other"}  # e.g. a paper named by title: let the LLM write the query
    return data


def _depth(value: Any) -> int:
    try:
        return max(1, min(MAX_DEPTH, int(value)))
    except (TypeError, ValueError):
        return 2


def build_template(intent: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Turn a known intent into (cypher, parameters)."""
    name = intent["intent"]
    cypher = TEMPLATES[name]
    if name in NEEDS_DEPTH:
        cypher = cypher.replace("{depth}", str(_depth(intent.get("depth"))))
    params = {"doi": intent["doi"].strip().lower()} if name in NEEDS_DOI else {}
    return cypher.strip(), params


def cited_papers(rows: list[dict[str, Any]]) -> list[CitedPaper]:
    """Every distinct paper (doi + title) that appears in the rows."""
    seen: dict[str, CitedPaper] = {}
    for row in rows:
        doi = row.get("doi")
        if isinstance(doi, str) and doi not in seen:
            title = row.get("title")
            seen[doi] = CitedPaper(doi=doi, title=title if isinstance(title, str) else None)
    return list(seen.values())


def summarize(llm: LLM, question: str, rows: list[dict[str, Any]]) -> str:
    """Short answer from the rows. If the LLM fails here, fall back to a plain count."""
    payload = json.dumps(rows[:SUMMARY_ROWS], default=str, ensure_ascii=False)
    try:
        return llm(SUMMARY_PROMPT, f"Question: {question}\nRows ({len(rows)} in total): {payload}")
    except LLMUnavailableError:
        return f"{len(rows)} result(s) found. (The AI summary is unavailable right now.)"


def answer_question(question: str, db: Neo4jClient, llm: LLM) -> AskResponse:
    """Run the whole pipeline. Raises GuardrailError or LLMUnavailableError on failure."""
    intent = parse_intent(llm(ROUTER_PROMPT, question))
    if intent["intent"] == "other":
        route = "llm_cypher"
        cypher, params = _strip_fences(llm(CYPHER_PROMPT, question)), {}
    else:
        route = f"template:{intent['intent']}"
        cypher, params = build_template(intent)

    safe_cypher = validate(cypher)  # the exact text that will run, shown in the answer
    rows = run_validated(db, safe_cypher, params)
    return AskResponse(
        question=question,
        route=route,
        cypher=safe_cypher,
        parameters=params,
        rows=rows,
        evidence="hypothesis" if uses_hypotheses(safe_cypher) else "fact",
        cited_papers=cited_papers(rows),
        answer=summarize(llm, question, rows),
    )
