"""Tests for POST /api/audit (the LangGraph agent). No database, no network, no real LLM."""

import pytest
from fastapi.testclient import TestClient

from app.agent import graph as graph_module
from app.agent import tools
from app.agent.llm import LLMUnavailableError, get_llm
from app.dependencies import get_db
from app.main import app

URL = "/api/audit"
PAPER = "10.1000/citer"
RETRACTED = "10.1000/retracted"
ABSTRACT = "We reuse the stem cell protocol of Smith et al. to grow organoids."
JUDGE_REPLY = f'{{"verdict": "relies_on", "confidence": 0.9, "evidence": "{ABSTRACT}"}}'


class GraphDB:
    """A tiny fake graph: answers each query by what it looks like."""

    def __init__(self, papers, links, abstracts=None):
        self.papers = papers  # doi -> {"title", "retracted"}
        self.links = links  # (citing, cited) pairs, direct citations only
        self.abstracts = abstracts or {}
        self.writes = []

    def _hops(self, start, forward):
        """doi -> shortest number of hops from ``start`` (up to 2), following citations."""
        seen, frontier = {}, {start}
        for hop in (1, 2):
            nxt = set()
            for a, b in self.links:
                src, dst = (a, b) if forward else (b, a)
                if src in frontier and dst not in seen and dst != start:
                    seen[dst] = hop
                    nxt.add(dst)
            frontier = nxt
        return seen

    def read(self, query, parameters=None):
        text = query if isinstance(query, str) else query.text
        params = parameters or {}
        doi = params.get("doi")
        if text.startswith("EXPLAIN"):
            return []
        if "p.abstract AS abstract" in text:
            return [{"abstract": self.abstracts[doi]}] if doi in self.abstracts else []
        if "p:Retracted AS retracted" in text and doi:
            p = self.papers.get(doi)
            return [{"doi": doi, "title": p["title"], "year": 2020, "journal": None, "retracted": p["retracted"]}] if p else []
        if "(p:Paper {doi: $doi})-[:CITES*" in text:  # exposure
            return [
                {"doi": d, "title": self.papers[d]["title"], "hops": h}
                for d, h in self._hops(doi, forward=True).items()
                if self.papers[d]["retracted"]
            ]
        if "(c:Paper)-[:CITES*" in text:  # contamination
            return [{"doi": d, "title": self.papers[d]["title"], "hops": h} for d, h in self._hops(doi, forward=False).items()]
        raise AssertionError(f"unexpected query: {text}")

    def write(self, query, parameters=None):
        self.writes.append(parameters)
        return [{"saved": 1}]


class FakeLLM:
    """Answers judge prompts and report prompts with canned text."""

    def __init__(self, judge=JUDGE_REPLY, report="Short report.", fail=False):
        self.judge, self.report, self.fail = judge, report, fail
        self.calls = 0

    def __call__(self, system, user):
        self.calls += 1
        if self.fail:
            raise LLMUnavailableError("quota exceeded")
        return self.judge if "DEPENDS" in system else self.report


@pytest.fixture
def run():
    """Call the endpoint with a given fake graph and fake LLM."""

    def call(db, llm, **body):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_llm] = lambda: llm
        try:
            return TestClient(app).post(URL, json=body)
        finally:
            app.dependency_overrides.clear()

    return call


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """OpenAlex is never really called in these tests."""
    monkeypatch.setattr(tools, "openalex_lookup", lambda doi: {"error": "offline in tests", "source": "openalex"})


def make_db():
    papers = {
        PAPER: {"title": "Organoids", "retracted": False},
        RETRACTED: {"title": "Smith et al. stem cell protocol", "retracted": True},
        "10.1000/later": {"title": "Later work", "retracted": False},
    }
    links = [(PAPER, RETRACTED), ("10.1000/later", PAPER)]
    return GraphDB(papers, links, abstracts={PAPER: ABSTRACT})


# ---------- Normal paper: exposure path ----------


def test_exposed_paper_gets_facts_hypotheses_and_steps(run):
    db = make_db()
    body = run(db, FakeLLM(), doi=PAPER, save_hypotheses=True).json()

    assert body["found_in"] == "graph" and body["retracted"] is False
    assert body["exposure"] == [{"doi": RETRACTED, "title": "Smith et al. stem cell protocol", "hops": 1}]
    assert body["contamination"] == []
    [judgement] = body["hypotheses"]
    assert judgement["verdict"] == "relies_on" and judgement["saved"] == 1
    assert [s["tool"] for s in body["steps"]] == ["find_paper", "exposure_check", "judge_reliance", "write_report"]
    assert body["steps"][2]["kind"] == "hypothesis"
    assert body["report"] == "Short report." and body["report_is_ai_generated"] is True
    assert db.writes[0]["citing"] == PAPER and db.writes[0]["retracted"] == RETRACTED


def test_nothing_is_saved_by_default(run):
    db = make_db()
    body = run(db, FakeLLM(), doi=PAPER).json()
    assert body["hypotheses"][0]["saved"] is None
    assert not db.writes


# ---------- Retracted paper: contamination path ----------


def test_retracted_paper_lists_who_built_on_it(run):
    body = run(make_db(), FakeLLM(judge='{"verdict": "unclear", "confidence": 0}'), doi=RETRACTED).json()
    assert body["retracted"] is True
    assert {r["doi"]: r["hops"] for r in body["contamination"]} == {PAPER: 1, "10.1000/later": 2}
    assert body["steps"][1]["tool"] == "contamination_check"
    # Only the direct citer is judged; the 2-hop one is a fact, not a judgement.
    assert [j["citing"] for j in body["hypotheses"]] == [PAPER]


def test_judgements_are_capped(run, monkeypatch):
    monkeypatch.setattr(graph_module, "MAX_JUDGEMENTS", 2)
    papers = {RETRACTED: {"title": "R", "retracted": True}}
    links = []
    for i in range(5):
        papers[f"10.1000/c{i}"] = {"title": f"C{i}", "retracted": False}
        links.append((f"10.1000/c{i}", RETRACTED))
    llm = FakeLLM()
    body = run(GraphDB(papers, links), llm, doi=RETRACTED).json()
    assert len(body["hypotheses"]) == 2
    assert "stopped after 2 judgements" in body["steps"][-2]["result"]


# ---------- Not in the graph ----------


def test_unknown_doi_is_reported_not_crashed(run):
    body = run(make_db(), FakeLLM(), doi="10.9999/nowhere").json()
    assert body["found_in"] == "nowhere" and body["paper"] is None
    assert [s["tool"] for s in body["steps"]] == ["find_paper", "openalex_lookup", "write_report"]


def test_openalex_fallback(run, monkeypatch):
    monkeypatch.setattr(tools, "openalex_lookup", lambda doi: {"doi": doi, "title": "Remote", "source": "openalex"})
    body = run(make_db(), FakeLLM(), doi="10.9999/remote").json()
    assert body["found_in"] == "openalex" and body["paper"]["title"] == "Remote"


# ---------- Errors ----------


def test_llm_down_still_gives_a_plain_report(run):
    body = run(make_db(), FakeLLM(fail=True), doi=PAPER).json()
    assert "error" in body["hypotheses"][0]
    assert "relies on 1 retracted paper(s)" in body["report"]
    assert "LLM unavailable" in body["steps"][-1]["result"]


def test_invalid_doi_is_422(run):
    assert run(make_db(), FakeLLM(), doi="not-a-doi").status_code == 422


def test_doi_url_is_normalized(run):
    body = run(make_db(), FakeLLM(), doi=f"https://doi.org/{PAPER.upper()}").json()
    assert body["doi"] == PAPER and body["found_in"] == "graph"


def test_contamination_counts_zombie_citations(run, monkeypatch):
    rows = [
        {"doi": "10.1000/a", "title": "A", "hops": 1, "before_retraction": True},
        {"doi": "10.1000/b", "title": "B", "hops": 1, "before_retraction": False},
        {"doi": "10.1000/c", "title": "C", "hops": 2, "before_retraction": None},
    ]
    monkeypatch.setattr(tools, "contamination_check", lambda *a, **k: {"rows": rows, "source": "graph"})
    body = run(make_db(), FakeLLM(judge='{"verdict": "unclear", "confidence": 0}'), doi=RETRACTED).json()
    assert body["contamination"][1]["before_retraction"] is False
    assert "2 directly, 1 of them after the retraction" in body["steps"][1]["result"]


def test_exposure_step_counts_zombie_citations(monkeypatch):
    rows = [
        {"doi": "10.1/a", "title": "A", "hops": 1, "before_retraction": False},
        {"doi": "10.1/b", "title": "B", "hops": 1, "before_retraction": True},
        {"doi": "10.1/c", "title": "C", "hops": 2, "before_retraction": None},
    ]
    monkeypatch.setattr("app.agent.graph.tools.exposure_check", lambda *a, **k: {"rows": rows})
    state = graph_module.AuditAgent(None, None).exposure({"doi": PAPER})
    assert state["steps"][0]["result"] == (
        "3 retracted paper(s) within 2 steps, 2 cited directly, 1 of them after the retraction (zombie citations)"
    )
