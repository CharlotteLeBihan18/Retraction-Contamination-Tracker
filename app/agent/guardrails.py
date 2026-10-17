"""Guardrails for LLM-written Cypher: nothing reaches Neo4j unless it passes every check.

Owner: Person 3. Only the owner edits this file.

An LLM can write any Cypher, including queries that delete data, read the whole
graph, or invent labels that do not exist. ``validate`` runs cheap text checks
(no database needed); ``run_validated`` adds an ``EXPLAIN`` dry run and a
timeout before the query really executes.

Checks, in order:

1. Shape: one statement only, no backticks (they can hide keywords).
2. Read-only: no write clause (CREATE, MERGE, SET...) and no procedure call
   except an explicit allow-list.
3. Schema: only the labels and relationship types agreed in the graph model.
4. Cost: variable-length paths must be bounded by ``MAX_PATH_LENGTH``, and a
   ``LIMIT`` of at most ``MAX_LIMIT`` is added or lowered.

Anything that fails raises ``GuardrailError`` with a short machine-readable
``reason`` so /ask and the evaluation can count rejections by type.
"""

import re

from neo4j import Query
from neo4j.exceptions import ClientError

from app.database import Neo4jClient

MAX_LIMIT = 100
MAX_PATH_LENGTH = 3
QUERY_TIMEOUT_S = 5.0

# The graph model from the proposal. Renaming anything here breaks every query.
ALLOWED_LABELS = frozenset({"Paper", "Retracted", "RetractionNotice", "Reason", "Author", "Journal"})
ALLOWED_RELATIONSHIPS = frozenset({"CITES", "RETRACTS", "FOR_REASON", "AUTHORED", "PUBLISHED_IN", "RELIES_ON"})
# RELIES_ON is an AI guess: answers that use it must be labelled as hypotheses.
HYPOTHESIS_RELATIONSHIPS = frozenset({"RELIES_ON"})

WRITE_KEYWORDS = ("CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "FOREACH", "LOAD")
# Read-only procedures /ask may call. Everything else (dbms.*, apoc.*, gds.*) is refused.
ALLOWED_PROCEDURES = frozenset({"db.index.vector.queryNodes"})

_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_CALL = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_SUBQUERY = re.compile(r"\bCALL\s*\{", re.IGNORECASE)
_NODE_LABELS = re.compile(r"\(\s*\w*\s*((?::\s*\w+\s*)+)")
_WHERE_LABELS = re.compile(r"\b\w+((?::\w+)+)")
_REL_TYPES = re.compile(r"\[\s*\w*\s*:\s*!?([\w|:\s!]+?)\s*(?:\*|\{|\])")
_VAR_LENGTH = re.compile(r"\*\s*(\d*)\s*(\.\.)?\s*(\d*)")
_LIMIT = re.compile(r"\bLIMIT\s+(\S+)\s*$", re.IGNORECASE)


class GuardrailError(ValueError):
    """Raised when a query is refused. ``reason`` is one of the short codes below."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _mask(query: str) -> str:
    """Hide comments and string contents so 'set' inside a title is not seen as SET."""
    without_comments = _COMMENT.sub(" ", query)
    return _STRING.sub("''", without_comments)


def _check_shape(masked: str) -> None:
    if "`" in masked:
        raise GuardrailError("shape", "Backtick-quoted names are not allowed")
    if ";" in masked.strip().rstrip(";"):
        raise GuardrailError("shape", "Only one statement is allowed")
    if not masked.strip():
        raise GuardrailError("shape", "Empty query")


def _check_read_only(masked: str) -> None:
    # Procedures first, so gds.graph.drop is reported as a procedure, not as DROP.
    for procedure in _CALL.findall(masked):
        if procedure not in ALLOWED_PROCEDURES:
            raise GuardrailError("procedure", f"Procedure {procedure} is not allowed")
    if _SUBQUERY.search(masked):
        raise GuardrailError("write", "CALL subqueries are not allowed")
    for keyword in WRITE_KEYWORDS:
        if re.search(rf"\b{keyword}\b", masked, re.IGNORECASE):
            raise GuardrailError("write", f"Write clause {keyword} is not allowed")


def _split(names: str) -> set[str]:
    return {name for name in re.split(r"[:|!\s]+", names) if name}


def _check_schema(masked: str) -> None:
    labels: set[str] = set()
    for group in _NODE_LABELS.findall(masked) + _WHERE_LABELS.findall(masked):
        labels |= _split(group)
    relationships: set[str] = set()
    for group in _REL_TYPES.findall(masked):
        relationships |= _split(group)
    labels -= relationships  # a relationship type also matches the WHERE pattern
    unknown_labels = labels - ALLOWED_LABELS
    if unknown_labels:
        raise GuardrailError("schema", f"Unknown label(s): {', '.join(sorted(unknown_labels))}")
    unknown_rels = relationships - ALLOWED_RELATIONSHIPS
    if unknown_rels:
        raise GuardrailError("schema", f"Unknown relationship type(s): {', '.join(sorted(unknown_rels))}")


def _check_path_length(masked: str) -> None:
    for low, dots, high in _VAR_LENGTH.findall(masked):
        if not dots:
            # [*] is unbounded; [*2] means exactly 2 hops.
            upper = int(low) if low else None
        else:
            upper = int(high) if high else None
        if upper is None:
            raise GuardrailError("cost", f"Variable-length paths need an upper bound (max {MAX_PATH_LENGTH})")
        if upper > MAX_PATH_LENGTH:
            raise GuardrailError("cost", f"Paths are capped at {MAX_PATH_LENGTH} hops, got {upper}")


def _enforce_limit(query: str, masked: str) -> str:
    """Return the query with a LIMIT of at most MAX_LIMIT at the end."""
    body = query.strip().rstrip(";").rstrip()
    match = _LIMIT.search(masked.strip().rstrip(";").rstrip())
    if match is None:
        return f"{body}\nLIMIT {MAX_LIMIT}"
    value = match.group(1)
    if value.isdigit() and int(value) <= MAX_LIMIT:
        return body
    # A parameter or a value above the cap: replace it with the cap.
    return _LIMIT.sub(f"LIMIT {MAX_LIMIT}", body)


def uses_hypotheses(query: str) -> bool:
    """True when the query reads AI-made links, so its answer must be marked as a hypothesis."""
    masked = _mask(query)
    return any(re.search(rf"\b{rel}\b", masked) for rel in HYPOTHESIS_RELATIONSHIPS)


def validate(query: str) -> str:
    """Run every text check and return the query ready to execute (with a safe LIMIT).

    Raises GuardrailError if the query is refused. Needs no database.
    """
    masked = _mask(query)
    _check_shape(masked)
    _check_read_only(masked)
    _check_schema(masked)
    _check_path_length(masked)
    return _enforce_limit(query, masked)


def run_validated(db: Neo4jClient, query: str, params: dict | None = None) -> list[dict]:
    """Validate, dry-run with EXPLAIN, then execute with a timeout.

    An invalid query fails at EXPLAIN and never executes.
    """
    safe = validate(query)
    params = params or {}
    try:
        db.read(f"EXPLAIN {safe}", params)
    except ClientError as exc:
        raise GuardrailError("syntax", f"Neo4j refused the query: {exc.message}") from exc
    return db.read(Query(safe, timeout=QUERY_TIMEOUT_S), params)
