"""Response models for the communities endpoint."""

from pydantic import BaseModel, Field

from app.models.retractions import GraphSize


class Community(BaseModel):
    """One research community found by Louvain."""

    community_id: int = Field(description="Label of the community (can change when the graph is rebuilt)")
    size: int = Field(description="Number of papers in the community")
    retracted_papers: int
    retracted_share: float = Field(description="retracted_papers / size, between 0 and 1")
    retracted_examples: list[str] = Field(description="Up to 3 titles of retracted papers")
    sample_titles: list[str] = Field(description="Up to 3 titles from the community, to show its topic")


class CommunitiesResponse(BaseModel):
    """One page of communities, most contaminated first."""

    total: int = Field(description="Number of communities with at least min_size papers")
    limit: int
    offset: int
    graph: GraphSize
    items: list[Community]
