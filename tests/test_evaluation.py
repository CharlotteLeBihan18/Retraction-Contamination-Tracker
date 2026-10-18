"""Tests for the evaluation script (app/agent/evaluation.py), with the pipeline faked."""

from app.agent import evaluation as ev
from app.agent.guardrails import GuardrailError
from app.agent.llm import LLMUnavailableError
from app.models.ask import AskResponse


def _response(route, rows):
    return AskResponse(question="q", route=route, cypher="MATCH (p:Paper) RETURN p LIMIT 100", parameters={},
                       rows=rows, evidence="fact", cited_papers=[], answer="a")


def _fake_answer(result):
    def answer(_question, _db, _llm):
        if isinstance(result, Exception):
            raise result
        return result

    return answer


def test_there_are_20_cases_with_5_refusals():
    assert len(ev.ASK_CASES) + len(ev.AUDIT_CASES) == 20
    assert sum(c.rejected for c in ev.ASK_CASES) == 5


def test_right_route_and_rows_pass(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(_response("template:exposure", [{"doi": ev.RETRACTED}])))
    case = ev.AskCase("q", "template:exposure", check=ev._has_doi(ev.RETRACTED), check_text="finds it")
    result = ev.run_ask_case(case, None, None)
    assert result.ok and "finds it: yes" in result.detail


def test_wrong_route_fails(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(_response("llm_cypher", [])))
    assert not ev.run_ask_case(ev.AskCase("q", "template:exposure"), None, None).ok


def test_wrong_rows_fail(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(_response("template:most_cited_retracted", [{"citers": 3}])))
    case = ev.AskCase("q", "template:most_cited_retracted", check=ev._total_citers(10), check_text="10 citers")
    assert not ev.run_ask_case(case, None, None).ok


def test_guardrail_refusal_counts_for_rejected_cases(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(GuardrailError("write", "no")))
    result = ev.run_ask_case(ev.AskCase("delete all", rejected=True), None, None)
    assert result.ok and result.got == "refused (write)"


def test_llm_declining_is_also_safe(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(_response("llm_cypher", [])))
    result = ev.run_ask_case(ev.AskCase("delete all", rejected=True), None, None)
    assert result.ok and result.got.startswith("LLM declined")


def test_refusing_a_normal_question_fails(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(GuardrailError("schema", "unknown label")))
    assert not ev.run_ask_case(ev.AskCase("q", "llm_cypher"), None, None).ok


def test_llm_error_is_recorded(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(LLMUnavailableError("quota")))
    result = ev.run_ask_case(ev.AskCase("q", "llm_cypher"), None, None)
    assert not result.ok and result.got == "LLM error"


def test_audit_case(monkeypatch):
    state = {"found_in": "graph", "retracted": True, "steps": [{"tool": "find_paper"}, {"tool": "contamination_check"}]}
    monkeypatch.setattr(ev, "run_audit", lambda *args, **kwargs: state)
    result = ev.run_audit_case(ev.AuditCase(ev.RETRACTED, "graph", retracted=True, note="n"), None, None)
    assert result.ok and result.detail == "find_paper → contamination_check"


def test_run_all_and_markdown(monkeypatch):
    monkeypatch.setattr(ev, "answer_question", _fake_answer(GuardrailError("write", "no")))
    monkeypatch.setattr(ev, "run_audit", lambda *args, **kwargs: {"found_in": "nowhere", "steps": []})
    table = ev.to_markdown(ev.run_all(None, None, rate_limit_wait=0))
    assert table.count("\n| ") == 21  # header separator + 20 rows
    assert "5 were refused by the guardrails" in table


def test_rate_limit_is_retried(monkeypatch):
    replies = [LLMUnavailableError("The LLM provider failed: GoogleRateLimitError"), _response("llm_cypher", [])]

    def answer(_question, _db, _llm):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(ev, "answer_question", answer)
    result = ev.run_with_retry(ev.run_ask_case, ev.AskCase("q", "llm_cypher"), None, None, wait=0)
    assert result.ok and not replies


def test_gives_up_after_max_attempts(monkeypatch):
    calls = []

    def answer(_question, _db, _llm):
        calls.append(1)
        raise LLMUnavailableError("GoogleRateLimitError")

    monkeypatch.setattr(ev, "answer_question", answer)
    result = ev.run_with_retry(ev.run_ask_case, ev.AskCase("q", "llm_cypher"), None, None, wait=0)
    assert result.got == "LLM error" and len(calls) == ev.MAX_ATTEMPTS


def test_audit_flags_llm_failures_inside_steps(monkeypatch):
    steps = [{"tool": "find_paper", "result": "ok"}, {"tool": "judge_reliance", "result": "GoogleRateLimitError"}]
    monkeypatch.setattr(ev, "run_audit", lambda *a, **k: {"found_in": "graph", "retracted": True, "steps": steps})
    result = ev.run_audit_case(ev.AuditCase(ev.RETRACTED, "graph", retracted=True), None, None)
    assert "LLM failed in 1 step(s)" in result.detail


def test_summary_does_not_count_llm_errors_as_declined():
    results = [
        ev.Result("ask", "q1", "refused", "refused (write)", True),
        ev.Result("ask", "q2", "refused", "LLM declined (llm_cypher)", True),
        ev.Result("ask", "q3", "refused", "LLM error", False),
    ]
    summary = ev.to_markdown(results).splitlines()[-1]
    assert "1 were refused by the guardrails and 1 were declined" in summary
    assert "1 case(s) could not run" in summary
