"""Tests for the audit agent's tools (app/agent/tools.py). No database, no network, no real LLM."""

import io
import json
import urllib.error

import pytest

from app.agent import tools
from app.agent.llm import LLMUnavailableError

ABSTRACT = "We reuse the stem cell protocol of Smith et al. to grow organoids. Results confirm the earlier findings."


def _text(query):
    return query if isinstance(query, str) else query.text


class FakeLLM:
    """Returns one canned reply (or raises it)."""

    def __init__(self, reply):
        self.reply = reply

    def __call__(self, system, user):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ---------- Graph tools ----------


def test_find_paper_by_doi_normalizes_it(fake_db):
    fake_db.responses = [[], [{"doi": "10.1/abc", "title": "T"}]]
    result = tools.find_paper(fake_db, doi="https://doi.org/10.1/ABC")
    assert result["rows"] == [{"doi": "10.1/abc", "title": "T"}]
    assert result["kind"] == "fact" and result["source"] == "graph"
    assert fake_db.queries[-1][1] == {"doi": "10.1/abc"}


def test_find_paper_by_title(fake_db):
    fake_db.responses = [[], []]
    tools.find_paper(fake_db, title="  organoids ")
    assert fake_db.queries[-1][1] == {"title": "organoids"}
    assert "LIMIT 5" in _text(fake_db.queries[-1][0])


def test_find_paper_needs_doi_or_title(fake_db):
    assert "error" in tools.find_paper(fake_db)
    assert not fake_db.queries


def test_exposure_and_contamination_use_bounded_paths(fake_db):
    fake_db.responses = [[], [], [], []]
    tools.exposure_check(fake_db, "10.1/a", depth=9)
    tools.contamination_check(fake_db, "10.1/a", depth=1)
    texts = [_text(q) for q, _ in fake_db.queries]
    assert "[:CITES*1..3]" in texts[1] and "[:CITES*1..1]" in texts[3]


def test_safe_cypher_refuses_writes_without_raising(fake_db):
    result = tools.safe_cypher(fake_db, "MATCH (n:Paper) DETACH DELETE n")
    assert result["refused"] == "write"
    assert not fake_db.queries


# ---------- OpenAlex ----------


def test_openalex_rebuilds_the_abstract(monkeypatch):
    work = {
        "title": "A paper",
        "publication_year": 2020,
        "cited_by_count": 4,
        "referenced_works": ["W1", "W2"],
        "is_retracted": False,
        "abstract_inverted_index": {"Hello": [0], "graph": [2], "world": [1]},
    }
    seen = {}

    def fake_urlopen(url, timeout):
        seen["url"] = url
        return io.BytesIO(json.dumps(work).encode())

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = tools.openalex_lookup("doi:10.1234/XYZ")
    assert result["abstract"] == "Hello world graph"
    assert result["references"] == 2 and result["source"] == "openalex"
    assert "doi:10.1234/xyz" in seen["url"]


def test_openalex_rejects_non_dois():
    assert "error" in tools.openalex_lookup("not a doi")


def test_openalex_network_error_is_reported(monkeypatch):
    def fail(url, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(tools.urllib.request, "urlopen", fail)
    assert "OpenAlex lookup failed" in tools.openalex_lookup("10.1234/xyz")["error"]


# ---------- Relies-on judgement ----------


def test_judge_relies_on_with_real_evidence():
    llm = FakeLLM(
        '{"verdict": "relies_on", "confidence": 0.85, '
        '"evidence": "We reuse the stem cell protocol of Smith et al. to grow organoids."}'
    )
    result = tools.judge_reliance(llm, ABSTRACT, "Smith et al. stem cell protocol")
    assert result["verdict"] == "relies_on" and result["confidence"] == 0.85
    assert result["kind"] == "hypothesis"


def test_judge_invented_evidence_becomes_unclear():
    llm = FakeLLM('{"verdict": "relies_on", "confidence": 0.95, "evidence": "This sentence is not in the abstract."}')
    result = tools.judge_reliance(llm, ABSTRACT, "Smith")
    assert result["verdict"] == "unclear" and result["confidence"] == 0.0


@pytest.mark.parametrize("reply", ["not json", '{"verdict": "maybe", "confidence": 2}', '{"confidence": "high"}'])
def test_judge_bad_answers_become_unclear(reply):
    assert tools.judge_reliance(FakeLLM(reply), ABSTRACT, "Smith")["verdict"] == "unclear"


def test_judge_without_abstract():
    assert tools.judge_reliance(FakeLLM("{}"), "  ", "Smith")["verdict"] == "unclear"


def test_judge_llm_down():
    assert "error" in tools.judge_reliance(FakeLLM(LLMUnavailableError("quota")), ABSTRACT, "Smith")


# ---------- Saving RELIES_ON ----------


def test_saves_only_confident_relies_on(fake_db):
    fake_db.responses = [[{"saved": 1}]]
    judgement = {"verdict": "relies_on", "confidence": 0.8, "evidence": "We reuse..."}
    assert tools.save_relies_on(fake_db, "10.1/c", "10.1/r", judgement)["saved"] == 1
    query, params = fake_db.queries[0]
    assert "MATCH (c:Paper {doi: $citing})-[:CITES]->" in query and "MERGE (c)-[x:RELIES_ON]->(r)" in query
    assert params["citing"] == "10.1/c" and params["confidence"] == 0.8


@pytest.mark.parametrize(
    "judgement",
    [
        {"verdict": "relies_on", "confidence": 0.4, "evidence": "x"},
        {"verdict": "mentions", "confidence": 0.9, "evidence": "x"},
        {"verdict": "unclear", "confidence": 0.0, "evidence": ""},
    ],
)
def test_weak_or_other_judgements_are_not_saved(fake_db, judgement):
    assert tools.save_relies_on(fake_db, "10.1/c", "10.1/r", judgement)["saved"] == 0
    assert not fake_db.queries


def test_no_cites_link_means_nothing_saved(fake_db):
    fake_db.responses = [[{"saved": 0}]]
    judgement = {"verdict": "relies_on", "confidence": 0.9, "evidence": "x"}
    assert "No CITES link" in tools.save_relies_on(fake_db, "10.1/c", "10.1/r", judgement)["reason"]