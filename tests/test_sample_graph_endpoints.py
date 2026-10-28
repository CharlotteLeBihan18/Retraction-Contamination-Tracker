"""Charlotte Le Bihan's graph endpoints on the hand-written sample graph (real Neo4j).

Zombie citations, Top spreaders (PageRank) and Communities (Louvain) are checked against
values worked out by hand from data/sample/sample.cypher, so these tests show that each
endpoint returns what a person reading the sample graph would expect. They also run in
GitHub Actions, which starts its own Neo4j. Skipped when Neo4j is not running.
"""

import pytest

from app.services import gds
from app.services.communities import contaminated_communities
from app.services.journals import zombie_journals
from app.services.retractions import top_spreaders
from scripts.load_sample import SAMPLE_FILE, read_statements

pytestmark = pytest.mark.neo4j

FLUORIDE, PEPTIDE, MOUTHWASH = "10.0000/sample.001", "10.0000/sample.002", "10.0000/sample.003"
SAMPLE_JOURNALS = {
    "Journal of Sample Dentistry",
    "Oral Health Letters",
    "International Dental Reports",
    "Clinical Periodontology Notes",
}
# The algorithms run on their own projection of the sample papers only, so real data
# loaded in the same database does not change the results.
LABEL = "SampleGraphTest"
GRAPH = "test_sample_graph"


@pytest.fixture(scope="module")
def sample_db(neo4j_client):
    """Load the sample graph, give its papers a temporary label, and remove the label afterwards."""
    for statement in read_statements(SAMPLE_FILE):
        neo4j_client.write(statement)
    gds.drop_projection(neo4j_client, GRAPH)
    neo4j_client.write(f"MATCH (p:Paper {{sample: true}}) SET p:{LABEL}")
    yield neo4j_client
    gds.drop_projection(neo4j_client, GRAPH)
    neo4j_client.write(f"MATCH (p:{LABEL}) REMOVE p:{LABEL}")


def test_zombie_citations_per_sample_journal(sample_db):
    # Citations made after the retraction: 3 in Journal of Sample Dentistry (sample.014, and
    # sample.025 twice), 2 in Clinical Periodontology Notes (016, 018), 1 in International Dental
    # Reports (013). Oral Health Letters only cited retracted papers before their retraction.
    _, rows = zombie_journals(sample_db, limit=10_000, offset=0, min_zombie=1)
    found = {row["journal"]: row for row in rows if row["journal"] in SAMPLE_JOURNALS}
    assert {name: row["zombie_citations"] for name, row in found.items()} == {
        "Journal of Sample Dentistry": 3,
        "Clinical Periodontology Notes": 2,
        "International Dental Reports": 1,
    }
    assert found["Journal of Sample Dentistry"]["citations_to_retracted"] == 4
    assert found["Journal of Sample Dentistry"]["zombie_share"] == pytest.approx(0.75)


def test_top_spreaders_rank_the_most_cited_retraction_first(sample_db):
    # The fluoride trial has 5 direct citers and a chain behind them, the peptide gel 3, the mouthwash 2.
    info, _, rows = top_spreaders(sample_db, limit=100, offset=0, graph=GRAPH, label=LABEL)
    assert info.node_count == 20
    ranked = [row for row in rows if row["doi"] and row["doi"].startswith("10.0000/sample.")]
    assert [row["doi"] for row in ranked] == [FLUORIDE, PEPTIDE, MOUTHWASH]
    counts = {row["doi"]: (row["direct_citations"], row["citations_after_retraction"]) for row in ranked}
    assert counts == {FLUORIDE: (5, 3), PEPTIDE: (3, 2), MOUTHWASH: (2, 1)}


def test_communities_put_every_sample_paper_somewhere(sample_db):
    # 20 papers in 4 separate groups: the caries/enamel papers, the mouthwash papers, the implant
    # papers and one orthodontics paper that cites nothing. Louvain never merges separate groups.
    info, total, rows = contaminated_communities(sample_db, limit=100, offset=0, min_size=1, graph=GRAPH, label=LABEL)
    assert info.node_count == 20
    assert total >= 4
    assert sum(row["size"] for row in rows) == 20
    assert sum(row["retracted_papers"] for row in rows) == 3
    # Ranked by share of retracted papers: the first community has retracted work, and the
    # implant papers end up in a community without any.
    assert rows[0]["retracted_papers"] > 0
    implant = [row for row in rows if "Ten-year survival of dental implants" in row["sample_titles"]]
    assert implant and implant[0]["retracted_papers"] == 0
