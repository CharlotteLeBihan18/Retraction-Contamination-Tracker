"""Response models for the contamination and exposure endpoints."""

from pydantic import BaseModel, Field

EXPOSURE_NOTE = (
    "A paper listed here cites the retracted paper, directly or through other papers. "
    "It is exposed to the retracted work, not proven wrong: it may only mention or criticise it."
)


EXPOSURE_ANSWER_NOTE = (
    "A paper listed here is a retracted paper that this paper cites, directly or through other papers. "
    "Relying on it is possible, not proven: the citation may only mention or criticise it."
)


class PaperRef(BaseModel):
    """A paper, as identified in the graph."""

    doi: str | None = Field(description="DOI, lowercase (some papers have none)")
    openalex_id: str | None = Field(description="OpenAlex id, for example W2741809807")
    title: str | None
    year: int | None


class ContaminatedPaper(PaperRef):
    """A paper that cites the retracted paper, directly or a few steps away."""

    hops: int = Field(description="Shortest number of citation steps to the retracted paper (1 = direct)")
    before_retraction: bool | None = Field(
        description="Direct citations only: true if made before the retraction, false if after "
        "(a zombie citation). Null for indirect citations."
    )


class ContaminationResponse(BaseModel):
    """One page of the papers that built on a retracted paper."""

    paper: PaperRef = Field(description="The retracted paper")
    depth: int = Field(description="Maximum number of citation steps that were followed")
    total: int = Field(description="Number of citing papers found within this depth, across all pages")
    direct_citations: int = Field(description="How many of them cite the retracted paper directly")
    after_retraction: int = Field(description="Direct citations made after the retraction (zombie citations)")
    limit: int
    offset: int
    items: list[ContaminatedPaper]
    note: str = Field(default=EXPOSURE_NOTE, description="How to read the result")


class ReachedRetraction(PaperRef):
    """A retracted paper that the paper cites, directly or a few steps away."""

    hops: int = Field(description="Length of the shortest citation chain to it (1 = cited directly)")
    before_retraction: bool | None = Field(
        description="Direct citations only: true if made before the retraction, false if after. "
        "Null for indirect citations."
    )
    retraction_date: str | None = Field(description="Earliest known retraction date (YYYY-MM-DD), if known")
    reasons: list[str] = Field(description="Retraction reasons given by Retraction Watch")
    path: list[PaperRef] = Field(description="Shortest chain, from the paper to the retracted paper")


class ExposureResponse(BaseModel):
    """Whether a paper relies on retracted work, and one page of the retracted papers it reaches."""

    paper: PaperRef = Field(description="The paper that was checked")
    paper_is_retracted: bool = Field(description="True if the checked paper is itself retracted")
    depth: int = Field(description="Maximum number of citation steps that were followed")
    exposed: bool = Field(description="True if at least one retracted paper is reached within this depth")
    total: int = Field(description="Number of retracted papers reached, across all pages")
    direct_citations: int = Field(description="How many of them the paper cites directly")
    limit: int
    offset: int
    items: list[ReachedRetraction]
    note: str = Field(default=EXPOSURE_ANSWER_NOTE, description="How to read the result")
