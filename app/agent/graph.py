"""The LangGraph audit agent: an integrity report for one paper, with every step it took.

Owner: Lara Normand. Only the owner edits this file.

The agent is a small LangGraph state machine. Each node calls one or more tools
from app/agent/tools.py and appends what it did to ``steps``, so the report can
show the full trace:

    locate ──► (not found) ─────────────────────────────┐
       │                                                 ▼
       ├──► (retracted) contamination ──► judge ──► report ──► END
       └──► (not retracted) exposure ───┘

* locate: find the paper in our graph, else on OpenAlex
* exposure: retracted papers it relies on through citations (facts)
* contamination: papers that built on it, if it is retracted (facts)
* judge: for direct citations, the LLM reads the citing abstract and decides
  whether it really relies on the retracted work (hypotheses, capped at
  ``MAX_JUDGEMENTS`` LLM calls; confident ones are saved as RELIES_ON)
* report: the LLM writes a short summary from the facts and hypotheses only

Graph tools never raise, so one failing step is recorded and the audit goes on.
"""

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent import tools
from app.agent.llm import LLM, LLMUnavailableError
from app.database import Neo4jClient

MAX_JUDGEMENTS = 5  # cap on LLM calls in the judge step, so an audit stays fast and cheap
DEPTH = 2
MAX_REPORT_INPUT = 12000  # characters of findings sent to the LLM

REPORT_PROMPT = """You write a short integrity report (at most 5 sentences) about one scientific paper.
Use ONLY the JSON findings given. Separate clearly:
- FACTS: what the citation graph shows (retraction status, exposure, contamination, and how many
  direct citations came after the retraction: before_retraction=false, "zombie citations")
- HYPOTHESES: the AI judgements about reliance, with their confidence
Never say a paper is invalid, fraudulent or should be retracted: talk about exposure and risk.
If nothing was found, say so."""


class AuditState(TypedDict, total=False):
    """Everything the agent knows so far. ``steps`` grows as nodes append to it."""

    doi: str
    save_hypotheses: bool
    paper: dict[str, Any] | None
    found_in: str  # "graph", "openalex" or "nowhere"
    retracted: bool
    exposure: list[dict[str, Any]]
    contamination: list[dict[str, Any]]
    to_judge: list[dict[str, str]]
    judgements: list[dict[str, Any]]
    report: str
    steps: Annotated[list[dict[str, Any]], operator.add]


def _step(tool: str, args: dict[str, Any], result: str, kind: str = "fact") -> list[dict[str, Any]]:
    return [{"tool": tool, "input": args, "result": result, "kind": kind}]


def _direct(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only direct citations (1 hop) are worth an LLM judgement."""
    return [r for r in rows if r.get("hops") == 1 and r.get("doi")]


class AuditAgent:
    """The nodes of the graph. ``db`` and ``llm`` come from the request."""

    def __init__(self, db: Neo4jClient, llm: LLM, max_judgements: int | None = None) -> None:
        self.db = db
        self.llm = llm
        self.max_judgements = MAX_JUDGEMENTS if max_judgements is None else max_judgements

    def locate(self, state: AuditState) -> AuditState:
        """Find the paper in our graph, else on OpenAlex."""
        doi = state["doi"]
        found = tools.find_paper(self.db, doi=doi)
        rows = found.get("rows") or []
        if rows:
            retracted = bool(rows[0].get("retracted"))
            return {
                "paper": rows[0],
                "found_in": "graph",
                "retracted": retracted,
                "steps": _step("find_paper", {"doi": doi}, f"found in graph, retracted={retracted}"),
            }
        steps = _step("find_paper", {"doi": doi}, found.get("error") or "not in graph")
        remote = tools.openalex_lookup(doi)
        if "error" in remote:
            steps += _step("openalex_lookup", {"doi": doi}, remote["error"])
            return {"paper": None, "found_in": "nowhere", "retracted": False, "steps": steps}
        steps += _step("openalex_lookup", {"doi": doi}, f"found on OpenAlex: {remote.get('title')!r}")
        # Not in our graph: no citation data to analyse, but we can still describe it.
        return {"paper": remote, "found_in": "openalex", "retracted": False, "steps": steps}

    @staticmethod
    def route(state: AuditState) -> str:
        """Choose the next node after locate."""
        if state.get("found_in") != "graph":
            return "report"
        return "contamination" if state.get("retracted") else "exposure"

    def exposure(self, state: AuditState) -> AuditState:
        """Retracted papers this paper relies on through citations."""
        doi = state["doi"]
        result = tools.exposure_check(self.db, doi, depth=DEPTH)
        rows = result.get("rows") or []
        direct = _direct(rows)
        to_judge = [{"citing": doi, "retracted": r["doi"], "retracted_title": r.get("title") or ""} for r in direct]
        zombies = sum(r.get("before_retraction") is False for r in direct)
        summary = result.get("error") or (
            f"{len(rows)} retracted paper(s) within {DEPTH} steps, {len(direct)} cited directly, "
            f"{zombies} of them after the retraction (zombie citations)"
        )
        step = _step("exposure_check", {"doi": doi, "depth": DEPTH}, summary)
        return {"exposure": rows, "to_judge": to_judge, "steps": step}

    def contamination(self, state: AuditState) -> AuditState:
        """Papers that built on this retracted paper."""
        doi = state["doi"]
        result = tools.contamination_check(self.db, doi, depth=DEPTH)
        rows = result.get("rows") or []
        direct = _direct(rows)
        title = (state.get("paper") or {}).get("title") or ""
        to_judge = [{"citing": r["doi"], "retracted": doi, "retracted_title": title} for r in direct]
        zombies = sum(r.get("before_retraction") is False for r in direct)
        summary = result.get("error") or (
            f"{len(rows)} paper(s) built on it within {DEPTH} steps, {len(direct)} directly, "
            f"{zombies} of them after the retraction (zombie citations)"
        )
        step = _step("contamination_check", {"doi": doi, "depth": DEPTH}, summary)
        return {"contamination": rows, "to_judge": to_judge, "steps": step}

    def _judge_one(self, pair: dict[str, str], save: bool) -> dict[str, Any]:
        abstract = tools.get_abstract(self.db, pair["citing"])
        if not abstract:
            abstract = tools.openalex_lookup(pair["citing"]).get("abstract") or ""
        verdict = tools.judge_reliance(self.llm, abstract, pair["retracted_title"])
        entry = {**pair, **verdict}
        if save and "error" not in verdict:
            entry["saved"] = tools.save_relies_on(self.db, pair["citing"], pair["retracted"], verdict).get("saved", 0)
        return entry

    def judge(self, state: AuditState) -> AuditState:
        """Ask the LLM whether each direct citation is real reliance (capped)."""
        pairs = state.get("to_judge") or []
        judgements, steps = [], []
        for pair in pairs[: self.max_judgements]:
            entry = self._judge_one(pair, bool(state.get("save_hypotheses")))
            judgements.append(entry)
            result = entry.get("error") or f"{entry['verdict']} (confidence {entry['confidence']})"
            args = {"citing": pair["citing"], "retracted": pair["retracted"]}
            steps += _step("judge_reliance", args, result, "hypothesis")
        if len(pairs) > self.max_judgements:
            note = f"stopped after {self.max_judgements} judgements ({len(pairs)} candidates)"
            steps += _step("judge_reliance", {}, note)
        return {"judgements": judgements, "steps": steps}

    def report(self, state: AuditState) -> AuditState:
        """Short summary written by the LLM from the findings only (plain text if the LLM is down)."""
        keys = ("citing", "retracted", "verdict", "confidence", "evidence")
        findings = {
            "doi": state["doi"],
            "found_in": state.get("found_in"),
            "paper": state.get("paper"),
            "retracted": state.get("retracted"),
            "exposure": state.get("exposure", []),
            "contamination": state.get("contamination", []),
            "hypotheses": [{k: j.get(k) for k in keys} for j in state.get("judgements", [])],
        }
        try:
            text = self.llm(REPORT_PROMPT, json.dumps(findings, default=str, ensure_ascii=False)[:MAX_REPORT_INPUT])
            result = "report written"
        except LLMUnavailableError as exc:
            text = _fallback_report(state)
            result = f"LLM unavailable ({exc}), plain report used"
        return {"report": text, "steps": _step("write_report", {}, result, "hypothesis")}


def build_audit_graph(db: Neo4jClient, llm: LLM, max_judgements: int | None = None):
    """Compile the agent for one request."""
    agent = AuditAgent(db, llm, max_judgements)
    graph = StateGraph(AuditState)
    graph.add_node("locate", agent.locate)
    graph.add_node("exposure", agent.exposure)
    graph.add_node("contamination", agent.contamination)
    graph.add_node("judge", agent.judge)
    graph.add_node("report", agent.report)
    graph.add_edge(START, "locate")
    graph.add_conditional_edges("locate", AuditAgent.route, ["exposure", "contamination", "report"])
    graph.add_edge("exposure", "judge")
    graph.add_edge("contamination", "judge")
    graph.add_edge("judge", "report")
    graph.add_edge("report", END)
    return graph.compile()


def _fallback_report(state: AuditState) -> str:
    """A plain report from the facts, used when the LLM is down."""
    if state.get("found_in") == "nowhere":
        return "This DOI was found neither in our graph nor on OpenAlex."
    if state.get("found_in") == "openalex":
        return "This paper is on OpenAlex but not in our graph, so its citations were not analysed."
    if state.get("retracted"):
        built_on = len(state.get("contamination", []))
        return f"This paper is retracted. {built_on} paper(s) built on it within {DEPTH} steps."
    exposed = len(state.get("exposure", []))
    return f"This paper is not retracted. It relies on {exposed} retracted paper(s) within {DEPTH} steps."


def run_audit(doi: str, db: Neo4jClient, llm: LLM, save_hypotheses: bool = False) -> AuditState:
    """Run the agent on one DOI and return its final state."""
    agent = build_audit_graph(db, llm)
    return agent.invoke({"doi": tools.normalize_doi(doi), "save_hypotheses": save_hypotheses, "steps": []})
