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
    rows = [{"doi": ev.RETRACTED, "citers": 3}]
    monkeypatch.setattr(ev, "answer_question", _fake_answer(_response("template:most_cited_retracted", rows)))
    case = ev.AskCase("q", "template:most_cited_retracted", check=ev._sample_citers({ev.RETRACTED: 5}))
    assert not ev.run_ask_case(case, None, None).ok


def test_sample_checks_ignore_the_real_data():
    # Kenza's review: with the real papers loaded next to the sample, only the sample rows are checked.
    real = {"doi": "10.1016/real.paper", "title": "A real retracted paper", "citers": 40}
    citers = ev._sample_citers({ev.RETRACTED: 5})
    assert citers([real, {"doi": ev.RETRACTED, "citers": 5}])
    assert not citers([real])
    titles = ev._mentions_all(ev.SAMPLE_RETRACTED_TITLES)
    rows = [{"p.title": t, "year": 2017} for t in ev.SAMPLE_RETRACTED_TITLES]
    assert titles(rows + [real]) and not titles(rows[:2] + [real])


def test_declined_question_shows_the_query_that_ran(monkeypatch):
    response = _response("llm_cypher", [])
    response.cypher = "MATCH (p:Paper)\nWHERE p.year > 2000 OR p.year < 1990\nRETURN p.title LIMIT 100"
    monkeypatch.setattr(ev, "answer_question", _fake_answer(response))
    result = ev.run_ask_case(ev.AskCase("Delete everything", rejected=True), None, None)
    assert result.ok and result.got == "LLM declined (llm_cypher)"
    one_line = "MATCH (p:Paper) WHERE p.year > 2000 OR p.year < 1990 RETURN p.title LIMIT 100"
    assert result.detail == f"ran instead: `{one_line}`"


def test_pipes_in_details_do_not_break_the_table():
    result = ev.Result("ask", "q", "refused", "LLM declined", True, "ran instead: `RETURN [x IN [1] | x]`")
    assert "[x IN [1] \\| x]" in ev.to_markdown([result])


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
