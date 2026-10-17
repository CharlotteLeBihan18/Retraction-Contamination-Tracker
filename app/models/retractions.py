"""Response models for the top-spreaders endpoint."""

from pydantic import BaseModel, Field

from app.services.gds import ProjectionInfo


class GraphSize(BaseModel):
    """Size of the in-memory citation graph the algorithm ran on."""

    papers: int
    links: int = Field(description="Directed and undirected copies of each citation")

    @classmethod
    def from_projection(cls, info: ProjectionInfo) -> "GraphSize":
        """Build it from the projection returned by app.services.gds.ensure_projection."""
        return cls(papers=info.node_count, links=info.relationship_count)


class SpreaderPaper(BaseModel):
    """One retracted paper with its PageRank score."""

    paper_id: str | None = Field(description="OpenAlex ID")
    doi: str | None
    title: str | None
    year: int | None
    pagerank: float = Field(description="Centrality in the citation network: higher means more influential")
    direct_citations: int = Field(description="Papers that cite it directly")
    citations_after_retraction: int = Field(description="Of those, how many cited it after it was retracted")


class TopSpreadersResponse(BaseModel):
    """One page of retracted papers, most central first."""

    total: int = Field(description="Number of retracted papers in the graph")
    limit: int
    offset: int
    graph: GraphSize
    items: list[SpreaderPaper]
