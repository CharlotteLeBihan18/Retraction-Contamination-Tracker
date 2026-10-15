"""Response models for the zombie-citations endpoint."""

from pydantic import BaseModel, Field


class ZombieExample(BaseModel):
    """One citation made after the cited paper was retracted."""

    citing_title: str | None = Field(description="Title of the paper that made the citation")
    citing_year: int | None = Field(description="Publication year of the citing paper")
    retracted_title: str | None = Field(description="Title of the retracted paper it cites")


class JournalZombieStats(BaseModel):
    """How often one journal's papers cite retracted work, before and after retraction."""

    journal: str = Field(description="Journal name")
    zombie_citations: int = Field(description="Citations made after the cited paper was retracted")
    citations_to_retracted: int = Field(description="All citations to papers that are now retracted")
    zombie_share: float = Field(description="zombie_citations / citations_to_retracted, between 0 and 1")
    examples: list[ZombieExample] = Field(description="Up to 3 example zombie citations")


class ZombieCitationsResponse(BaseModel):
    """One page of journals, ranked by number of zombie citations."""

    total: int = Field(description="Number of journals matching the filter, across all pages")
    limit: int
    offset: int
    items: list[JournalZombieStats]
