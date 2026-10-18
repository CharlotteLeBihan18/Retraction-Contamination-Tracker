"""Request and response models for the Audit endpoint.

Owner: Person 3. Only the owner edits this file.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AuditRequest(BaseModel):
    """Which paper to audit."""

    doi: str = Field(min_length=3, max_length=200, examples=["10.1016/j.cell.2020.01.001"])
    save_hypotheses: bool = Field(
        True, description="Save confident 'relies on' judgements as RELIES_ON links (always labelled as AI guesses)"
    )


class AgentStep(BaseModel):
    """One thing the agent did."""

    tool: str
    input: dict[str, Any]
    result: str
    kind: Literal["fact", "hypothesis"]


class Judgement(BaseModel):
    """The LLM's opinion on whether a citing paper relies on a retracted one."""

    citing: str
    retracted: str
    verdict: str | None = None
    confidence: float | None = None
    evidence: str = ""
    saved: int | None = Field(None, description="1 if stored as a RELIES_ON link")
    error: str | None = None


class AuditResponse(BaseModel):
    """The integrity report: facts from the graph, AI hypotheses, the summary and every step."""

    doi: str
    found_in: Literal["graph", "openalex", "nowhere"]
    paper: dict[str, Any] | None
    retracted: bool
    exposure: list[dict[str, Any]] = Field(description="FACT: retracted papers it relies on through citations")
    contamination: list[dict[str, Any]] = Field(description="FACT: papers that built on it (if retracted)")
    hypotheses: list[Judgement] = Field(description="HYPOTHESIS: AI judgements of real reliance")
    report: str = Field(description="Summary written by the LLM from the facts and hypotheses above")
    report_is_ai_generated: bool = True
    steps: list[AgentStep] = Field(description="Every tool call the agent made, in order")
    