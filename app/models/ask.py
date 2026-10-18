"""Response model for the Ask endpoint.

Owner: Person 3. Only the owner edits this file.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class CitedPaper(BaseModel):
    """A paper the answer is based on."""

    doi: str
    title: str | None = None


class AskResponse(BaseModel):
    """The answer to a plain-English question, with everything needed to check it."""

    question: str
    route: str = Field(
        description="'template:<intent>' for a pre-written, tested query; 'llm_cypher' when the LLM wrote it"
    )
    cypher: str = Field(description="The exact query that ran, after the guardrails")
    parameters: dict[str, Any]
    rows: list[dict[str, Any]] = Field(description="Raw results from the graph")
    evidence: Literal["fact", "hypothesis"] = Field(
        description="'fact' when the rows only use data links; 'hypothesis' when they use AI-made RELIES_ON links"
    )
    cited_papers: list[CitedPaper]
    answer: str = Field(description="Short summary written by the LLM from the rows")
    answer_is_ai_generated: bool = Field(True, description="The summary is always an AI judgement, never a fact")
