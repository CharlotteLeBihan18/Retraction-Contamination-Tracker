"""Tests for scripts/ingest.py.

Most tests need no database: the pure functions are tested directly and the Neo4j calls go
through the FakeNeo4jClient from conftest.py. The last test (marked ``neo4j``) runs the real
queries on a real database and is skipped when Neo4j is not running.
"""

import json

import pytest

from app.services import gds
from scripts import ingest

# --- Small helpers to build fake cache content -------------------------------------------------


def make_work(openalex_id: str, doi: str, **overrides) -> dict:
    """A minimal OpenAlex work, shaped like the records saved in data/cache/works_by_doi/."""
    work = {
        "id": f"https://openalex.org/{openalex_id}",
        "doi": f"https://doi.org/{doi}",
        "title": f"Title of {openalex_id}",
        "publication_year": 2018,
        "publication_date": "2018-05-01",
        "abstract_inverted_index": {"Teeth": [0], "matter": [1]},
        "referenced_works": [],
        "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Ada Lovelace"}}],
        "primary_location": {"source": {"display_name": "Journal of Teeth"}},
        "is_retracted": True,
    }
    work.update(overrides)
    return work


def make_selection(doi: str, date: str | None = "2020-03-15", reasons: list[str] | None = None) -> dict:
    """A minimal row of retraction_watch_selection.json."""
    return {
        "record_id": "1",
        "doi": doi,
        "title": f"RW title of {doi}",
        "journal": "RW Journal",
        "retraction_date": date,
        "reasons": ["Plagiarism"] if reasons is None else reasons,
    }


def make_citer(openalex_id: str, doi: str | None, cites: list[str], **overrides) -> dict:
    """A citing paper, shaped like the records saved in data/cache/citers/ by fetch_citers.py."""
    citer = {
        "id": f"https://openalex.org/{openalex_id}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": f"Citing paper {openalex_id}",
        "publication_year": 2021,
        "publication_date": "2021-03-04",
        "is_retracted": False,
        "journal": "Journal of Citers",
        "authors": [{"id": "https://openalex.org/A2", "name": "Grace Hopper"}],
        "cites_retracted": cites,
    }
    citer.update(overrides)
    return citer


def empty_rows(**filled) -> dict:
    """The rows dict that ``load`` expects, with every list empty except the ones given."""
    keys = [
        "papers", "notices", "authors", "citations",
        "citer_papers", "citer_journals", "citer_authors", "citer_citations",
    ]  # fmt: skip
    return {key: filled.get(key, []) for key in keys}


# --- Pure functions ----------------------------------------------------------------------------


def test_short_id_removes_the_openalex_url():
    assert ingest.short_id("https://openalex.org/A5065434411") == "A5065434411"


def test_rebuild_abstract_orders_words_by_position():
    assert ingest.rebuild_abstract({"world": [1], "Hello": [0, 2]}) == "Hello world Hello"


@pytest.mark.parametrize("empty", [None, {}])
def test_rebuild_abstract_returns_none_when_missing(empty):
    assert ingest.rebuild_abstract(empty) is None


@pytest.mark.parametrize(
    "citing_date, citing_year, retraction_date, expected",
    [
        ("2019-01-01", 2019, "2020-06-15", True),  # cited before the retraction
        ("2020-06-15", 2020, "2020-06-15", True),  # same day: not sure it came after
        ("2020-06-16", 2020, "2020-06-15", False),  # one day after: zombie citation
        (None, 2020, "2020-06-15", True),  # year only, same year: not sure
        (None, 2019, "2020-06-15", True),
        (None, 2021, "2020-06-15", False),
        (None, None, "2020-06-15", True),  # nothing known about the citing paper
        ("2025-01-01", 2025, None, True),  # retraction date unknown
    ],
)
def test_before_retraction(citing_date, citing_year, retraction_date, expected):
    assert ingest.before_retraction(citing_date, citing_year, retraction_date) is expected


def test_batches_splits_without_losing_rows():
    rows = [{"n": n} for n in range(5)]
    assert [len(chunk) for chunk in ingest.batches(rows, 2)] == [2, 2, 1]


def test_journal_name_prefers_openalex_then_falls_back():
    assert ingest.journal_name(make_work("W1", "10.1/a"), "RW Journal") == "Journal of Teeth"
    assert ingest.journal_name(make_work("W1", "10.1/a", primary_location=None), "RW Journal") == "RW Journal"
    no_source = make_work("W1", "10.1/a", primary_location={"source": None})
    assert ingest.journal_name(no_source, "RW Journal") == "RW Journal"


def test_author_rows_skip_authors_without_id_and_duplicates():
    work = make_work(
        "W1",
        "10.1/a",
        authorships=[
            {"author": {"id": "https://openalex.org/A1", "display_name": "Ada"}},
            {"author": {"id": "https://openalex.org/A1", "display_name": "Ada again"}},
            {"author": {"id": None, "display_name": "No Id"}},
            {"author": {}},
        ],
    )
    assert ingest.author_rows("10.1/a", work) == [{"doi": "10.1/a", "author_id": "A1", "name": "Ada"}]


def test_notice_rows_adds_an_openalex_notice_only_when_openalex_agrees():
    row = make_selection("10.1/a", reasons=["Plagiarism", "Fabrication"])
    agree = ingest.notice_rows(row, make_work("W1", "10.1/a", is_retracted=True))
    assert [(n["source"], n["date"]) for n in agree] == [("Retraction Watch", "2020-03-15"), ("OpenAlex", None)]
    assert agree[0]["reasons"] == ["Plagiarism", "Fabrication"]

    disagree = ingest.notice_rows(row, make_work("W1", "10.1/a", is_retracted=False))
    assert [n["source"] for n in disagree] == ["Retraction Watch"]


def test_paper_row_falls_back_to_retraction_watch_title():
    row = ingest.paper_row(make_selection("10.1/a"), make_work("W1", "10.1/a", title=None))
    assert row["title"] == "RW title of 10.1/a"
    assert row["openalex_id"] == "W1"
    assert row["abstract"] == "Teeth matter"
    assert row["journal"] == "Journal of Teeth"


# --- build_rows --------------------------------------------------------------------------------


def test_build_rows_links_papers_and_flags_citations():
    selection = [
        make_selection("10.1/old", date="2015-01-01"),
        make_selection("10.1/new", date="2030-01-01"),
        make_selection("10.1/missing"),  # not found in OpenAlex
    ]
    works = {
        # "new" cites "old" and itself; it was published in 2018, after old's retraction (2015)
        "10.1/new": make_work(
            "W2", "10.1/new", referenced_works=["https://openalex.org/W1", "https://openalex.org/W2"]
        ),
        # "old" cites "new" and an unknown paper; published in 2018, before new's retraction (2030)
        "10.1/old": make_work(
            "W1", "10.1/old", referenced_works=["https://openalex.org/W2", "https://openalex.org/W999"]
        ),
    }

    rows = ingest.build_rows(selection, works)

    assert sorted(p["doi"] for p in rows["papers"]) == ["10.1/new", "10.1/old"]
    citations = {(c["citing"], c["cited"]): c["before_retraction"] for c in rows["citations"]}
    assert citations == {("10.1/new", "10.1/old"): False, ("10.1/old", "10.1/new"): True}
    assert all(isinstance(value, bool) for value in citations.values())  # never null
    assert len(rows["notices"]) == 4  # Retraction Watch + OpenAlex for each of the two papers


def test_build_rows_has_no_citation_to_a_missing_paper():
    works = {"10.1/a": make_work("W1", "10.1/a", referenced_works=["https://openalex.org/W404"])}
    assert ingest.build_rows([make_selection("10.1/a")], works)["citations"] == []


# --- Citing papers ----------------------------------------------------------------------------


def retracted_set():
    """Two retracted papers (W1 retracted in 2019, W2 with an unknown date) and their works."""
    selection = [make_selection("10.1/a", date="2019-06-01"), make_selection("10.1/b", date=None)]
    works = {"10.1/a": make_work("W1", "10.1/a"), "10.1/b": make_work("W2", "10.1/b")}
    return selection, works


def test_citer_rows_builds_papers_journals_authors_and_flagged_citations():
    selection, works = retracted_set()
    citers = {
        # published 2021-03-04: after the 2019 retraction of W1 (a zombie), unknown date for W2 (True)
        "W10": make_citer("W10", "10.2/Citer", ["W1", "W2"]),
        # published 2018: before the retraction
        "W11": make_citer("W11", None, ["W1"], publication_date="2018-01-01", publication_year=2018),
    }

    rows = ingest.build_rows(selection, works, citers)

    assert {p["openalex_id"]: p["doi"] for p in rows["citer_papers"]} == {"W10": "10.2/citer", "W11": None}
    assert [(j["openalex_id"], j["journal"]) for j in rows["citer_journals"]] == [("W10", "Journal of Citers")] * 1 + [
        ("W11", "Journal of Citers")
    ]
    assert {(a["openalex_id"], a["author_id"]) for a in rows["citer_authors"]} == {("W10", "A2"), ("W11", "A2")}
    flags = {(c["citing"], c["cited"]): c["before_retraction"] for c in rows["citer_citations"]}
    assert flags == {("W10", "W1"): False, ("W10", "W2"): True, ("W11", "W1"): True}
    assert all(isinstance(value, bool) for value in flags.values())  # never null


def test_citer_rows_skips_retracted_papers_duplicate_dois_and_unknown_targets():
    selection, works = retracted_set()
    citers = {
        "W1": make_citer("W1", "10.1/a", ["W2"]),  # one of the retracted papers: already loaded
        "W10": make_citer("W10", "10.2/same", ["W1"]),
        "W11": make_citer("W11", "10.2/SAME", ["W1"]),  # same DOI as W10: the constraint allows one
        "W12": make_citer("W12", "10.1/b", ["W1"]),  # DOI of a retracted paper
        "W13": make_citer("W13", None, ["W404"]),  # cites something that is not in the set
    }

    rows = ingest.build_rows(selection, works, citers)

    assert [p["openalex_id"] for p in rows["citer_papers"]] == ["W10", "W13"]
    assert [(c["citing"], c["cited"]) for c in rows["citer_citations"]] == [("W10", "W1")]


def test_citer_rows_leaves_out_missing_journals_and_ignores_openalex_retracted_flag():
    selection, works = retracted_set()
    citers = {"W10": make_citer("W10", "10.2/x", ["W1"], journal=None, is_retracted=True, authors=[])}

    rows = ingest.build_rows(selection, works, citers)

    assert rows["citer_journals"] == []
    assert rows["citer_authors"] == []
    assert set(rows["citer_papers"][0]) == {"openalex_id", "doi", "title", "year", "publication_date"}


def test_build_rows_without_citers_has_empty_citer_lists():
    selection, works = retracted_set()
    rows = ingest.build_rows(selection, works)
    assert all(rows[key] == [] for key in ("citer_papers", "citer_journals", "citer_authors", "citer_citations"))


def test_load_citers_reads_every_page_and_keeps_each_paper_once(tmp_path):
    (tmp_path / "citers").mkdir()
    page_one = {"results": [make_citer("W10", "10.2/a", ["W1"]), make_citer("W11", "10.2/b", ["W1"])]}
    page_two = {"results": [make_citer("W11", "10.2/b", ["W1"]), make_citer("W12", "10.2/c", ["W2"])]}
    (tmp_path / "citers" / "a.json").write_text(json.dumps(page_one))
    (tmp_path / "citers" / "b.json").write_text(json.dumps(page_two))

    assert sorted(ingest.load_citers(tmp_path)) == ["W10", "W11", "W12"]


def test_load_citers_returns_nothing_when_the_fetch_step_was_not_run(tmp_path):
    assert ingest.load_citers(tmp_path) == {}


# --- load_cache --------------------------------------------------------------------------------


def test_load_cache_ignores_works_that_were_not_selected(tmp_path):
    (tmp_path / "works_by_doi").mkdir()
    (tmp_path / "retraction_watch_selection.json").write_text(json.dumps([make_selection("10.1/a")]))
    no_doi = {**make_work("W3", "10.1/x"), "doi": None}
    results = [make_work("W1", "10.1/A"), make_work("W2", "10.1/extra"), no_doi]
    (tmp_path / "works_by_doi" / "one.json").write_text(json.dumps({"requested": [], "results": results}))

    selection, works = ingest.load_cache(tmp_path)

    assert [row["doi"] for row in selection] == ["10.1/a"]
    assert list(works) == ["10.1/a"]  # DOI lowercased, extra and DOI-less works dropped


def test_committed_cache_gives_consistent_rows():
    """Sanity checks on the real cache, without fixing exact numbers that a new fetch could change."""
    rows = ingest.build_rows(*ingest.load_cache())
    dois = {paper["doi"] for paper in rows["papers"]}
    assert len(dois) == len(rows["papers"]) > 0
    assert all(notice["doi"] in dois for notice in rows["notices"])
    assert all(link["doi"] in dois for link in rows["authors"])
    assert all(isinstance(c["before_retraction"], bool) for c in rows["citations"])
    assert all(c["citing"] in dois and c["cited"] in dois for c in rows["citations"])


def test_committed_citers_give_consistent_rows():
    """If the citing papers were fetched, their rows must be valid (checked without exact numbers)."""
    selection, works = ingest.load_cache()
    rows = ingest.build_rows(selection, works, ingest.load_citers())
    ids = [paper["openalex_id"] for paper in rows["citer_papers"]]
    dois = [paper["doi"] for paper in rows["citer_papers"] if paper["doi"]]
    retracted_ids = {paper["openalex_id"] for paper in rows["papers"]}
    assert len(ids) == len(set(ids)) and len(dois) == len(set(dois))  # unique keys: constraints hold
    assert not set(ids) & retracted_ids
    assert all(isinstance(c["before_retraction"], bool) for c in rows["citer_citations"])
    assert all(c["citing"] in set(ids) and c["cited"] in retracted_ids for c in rows["citer_citations"])


# --- Database calls, with the fake client ------------------------------------------------------


def test_load_creates_the_schema_before_any_data(fake_db):
    fake_db.rows = [{"papers": 1, "missing": 0}]
    rows = empty_rows(papers=[{"doi": "10.1/a"}])

    summary = ingest.load(fake_db, rows)

    assert summary == {"papers": 1, "missing": 0}
    queries = [query for query, _ in fake_db.queries]
    schema_count = len(ingest.SCHEMA_STATEMENTS)
    assert queries[:schema_count] == ingest.SCHEMA_STATEMENTS
    assert queries[schema_count] == ingest.PAPERS_QUERY
    assert fake_db.queries[schema_count][1] == {"rows": [{"doi": "10.1/a"}]}


def test_load_drops_the_gds_projection_after_the_data(fake_db):
    fake_db.rows = [{"missing": 0}]
    rows = empty_rows(papers=[{"doi": "10.1/a"}], citer_citations=[{"citing": "W2", "cited": "W1"}])

    ingest.load(fake_db, rows)

    queries = [query for query, _ in fake_db.queries]
    assert queries.index(gds.DROP_QUERY) > queries.index(ingest.CITER_CITATIONS_QUERY)
    assert queries.index(gds.DROP_QUERY) > queries.index(ingest.PAPERS_QUERY)
    assert (gds.DROP_QUERY, {"name": gds.GRAPH_NAME}) in fake_db.queries


def test_load_fails_when_a_citation_has_no_before_retraction(fake_db):
    fake_db.rows = [{"missing": 3}]
    rows = empty_rows()

    with pytest.raises(ingest.MissingFlagError, match="3 citations"):
        ingest.load(fake_db, rows)


def test_run_batches_sends_rows_by_batch_not_one_by_one(fake_db):
    rows = [{"n": n} for n in range(ingest.BATCH_SIZE * 2 + 1)]
    ingest.run_batches(fake_db, ingest.AUTHORS_QUERY, rows)
    assert [len(params["rows"]) for _, params in fake_db.queries] == [ingest.BATCH_SIZE, ingest.BATCH_SIZE, 1]


def test_every_query_reads_its_data_from_the_rows_parameter():
    queries = (
        ingest.PAPERS_QUERY,
        ingest.NOTICES_QUERY,
        ingest.AUTHORS_QUERY,
        ingest.CITATIONS_QUERY,
        ingest.CITER_PAPERS_QUERY,
        ingest.CITER_JOURNALS_QUERY,
        ingest.CITER_AUTHORS_QUERY,
        ingest.CITER_CITATIONS_QUERY,
    )
    for query in queries:
        assert query.lstrip().startswith("UNWIND $rows AS row")


def test_citation_queries_look_up_both_papers_before_matching_the_second():
    """A WITH between the two MATCH clauses keeps them two index lookups, not a Cartesian product."""
    for query in (ingest.CITATIONS_QUERY, ingest.CITER_CITATIONS_QUERY):
        assert "WITH a, row" in query


def test_load_runs_the_citer_queries_after_the_retracted_ones(fake_db):
    fake_db.rows = [{"missing": 0}]
    rows = empty_rows(
        papers=[{"doi": "10.1/a"}],
        citer_papers=[{"openalex_id": "W2"}],
        citer_journals=[{"openalex_id": "W2", "journal": "J"}],
        citer_authors=[{"openalex_id": "W2", "author_id": "A2", "name": "G"}],
        citer_citations=[{"citing": "W2", "cited": "W1", "before_retraction": True}],
    )

    ingest.load(fake_db, rows)

    queries = [query for query, _ in fake_db.queries]
    order = [
        ingest.PAPERS_QUERY,
        ingest.CITER_PAPERS_QUERY,
        ingest.CITER_JOURNALS_QUERY,
        ingest.CITER_AUTHORS_QUERY,
        ingest.CITER_CITATIONS_QUERY,
        gds.DROP_QUERY,
    ]
    assert [queries.index(query) for query in order] == sorted(queries.index(query) for query in order)


def test_main_dry_run_does_not_touch_the_database(monkeypatch, capsys):
    monkeypatch.setattr(
        ingest, "load_cache", lambda: ([make_selection("10.1/a")], {"10.1/a": make_work("W1", "10.1/a")})
    )
    monkeypatch.setattr(ingest, "load_citers", dict)
    monkeypatch.setattr(ingest.Neo4jClient, "from_settings", lambda settings: pytest.fail("must not connect"))

    assert ingest.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Papers: 1 of 1 selected" in output
    assert "Citing papers: 0" in output


def test_main_fails_cleanly_when_neo4j_is_down(monkeypatch, fake_db, capsys):
    fake_db.available = False
    monkeypatch.setattr(
        ingest, "load_cache", lambda: ([make_selection("10.1/a")], {"10.1/a": make_work("W1", "10.1/a")})
    )
    monkeypatch.setattr(ingest, "load_citers", dict)
    monkeypatch.setattr(ingest.Neo4jClient, "from_settings", lambda settings: fake_db)

    assert ingest.main([]) == 1
    assert "not reachable" in capsys.readouterr().out


# --- Real database -----------------------------------------------------------------------------

TEST_DOIS = ("10.0000/ingest-test.1", "10.0000/ingest-test.2")


def _clean(db) -> None:
    """Remove everything this test creates (and nothing else)."""
    db.write(
        "MATCH (p:Paper) WHERE p.doi IN $dois "
        "OPTIONAL MATCH (n:RetractionNotice)-[:RETRACTS]->(p) DETACH DELETE n, p",
        {"dois": list(TEST_DOIS)},
    )
    db.write("MATCH (j:Journal {name: 'Ingest Test Journal'}) DETACH DELETE j")
    db.write("MATCH (a:Author {openalex_id: 'A-ingest-test'}) DETACH DELETE a")
    db.write("MATCH (r:Reason {name: 'Ingest Test Reason'}) DETACH DELETE r")


@pytest.fixture
def clean_db(neo4j_client):
    """The real database, emptied of this test's data before and after."""
    _clean(neo4j_client)
    yield neo4j_client
    _clean(neo4j_client)


@pytest.mark.neo4j
def test_load_into_neo4j_is_complete_and_idempotent(clean_db):
    selection = [make_selection(doi, reasons=["Ingest Test Reason"]) for doi in TEST_DOIS]
    works = {
        TEST_DOIS[0]: make_work(
            "W-ingest-test-1",
            TEST_DOIS[0],
            referenced_works=["https://openalex.org/W-ingest-test-2"],
            primary_location={"source": {"display_name": "Ingest Test Journal"}},
            authorships=[{"author": {"id": "https://openalex.org/A-ingest-test", "display_name": "Test Author"}}],
        ),
        TEST_DOIS[1]: make_work(
            "W-ingest-test-2",
            TEST_DOIS[1],
            primary_location={"source": {"display_name": "Ingest Test Journal"}},
            authorships=[{"author": {"id": "https://openalex.org/A-ingest-test", "display_name": "Test Author"}}],
        ),
    }
    rows = ingest.build_rows(selection, works)

    ingest.load(clean_db, rows)
    ingest.load(clean_db, rows)  # second run must not create duplicates

    dois = {"dois": list(TEST_DOIS)}
    papers = "MATCH (p:Paper:Retracted) WHERE p.doi IN $dois RETURN count(p) AS n"
    notices = "MATCH (p:Paper)<-[:RETRACTS]-(n:RetractionNotice) WHERE p.doi IN $dois RETURN count(n) AS n"
    journal = (
        "MATCH (p:Paper)-[:PUBLISHED_IN]->(:Journal {name: 'Ingest Test Journal'}) "
        "WHERE p.doi IN $dois RETURN count(p) AS n"
    )
    authored = (
        "MATCH (:Author {openalex_id: 'A-ingest-test'})-[:AUTHORED]->(p:Paper) "
        "WHERE p.doi IN $dois RETURN count(p) AS n"
    )
    cites = (
        "MATCH (a:Paper)-[c:CITES]->(b:Paper) WHERE a.doi IN $dois AND b.doi IN $dois "
        "RETURN count(c) AS n, collect(c.before_retraction) AS flags"
    )
    assert clean_db.read(papers, dois) == [{"n": 2}]
    assert clean_db.read(notices, dois) == [{"n": 4}]  # Retraction Watch + OpenAlex, for two papers
    assert clean_db.read(journal, dois) == [{"n": 2}]
    assert clean_db.read(authored, dois) == [{"n": 2}]
    assert clean_db.read(cites, dois) == [{"n": 1, "flags": [True]}]

    reasons = clean_db.read("MATCH (r:Reason {name: 'Ingest Test Reason'}) RETURN count(r) AS n")
    assert reasons == [{"n": 1}]  # one Reason node shared by both notices


CITER_IDS = ("W-ingest-citer-1", "W-ingest-citer-2")


def _clean_citers(db) -> None:
    """Remove the citing papers, journal and author that the citers test creates."""
    db.write("MATCH (p:Paper) WHERE p.openalex_id IN $ids DETACH DELETE p", {"ids": list(CITER_IDS)})
    db.write("MATCH (j:Journal {name: 'Ingest Citer Journal'}) DETACH DELETE j")
    db.write("MATCH (a:Author {openalex_id: 'A-ingest-citer'}) DETACH DELETE a")


@pytest.fixture
def clean_citers(clean_db):
    """Like clean_db, and also empties the citing papers of the citers test."""
    _clean_citers(clean_db)
    yield clean_db
    _clean_citers(clean_db)


@pytest.mark.neo4j
def test_load_citers_into_neo4j_is_complete_and_idempotent(clean_citers):
    selection = [make_selection(TEST_DOIS[0], date="2019-06-01")]
    works = {TEST_DOIS[0]: make_work("W-ingest-test-1", TEST_DOIS[0])}
    citers = {
        CITER_IDS[0]: make_citer(
            CITER_IDS[0],
            "10.0000/ingest-citer.1",
            ["W-ingest-test-1"],
            journal="Ingest Citer Journal",
            authors=[{"id": "https://openalex.org/A-ingest-citer", "name": "Citer Author"}],
        ),
        CITER_IDS[1]: make_citer(
            CITER_IDS[1],
            None,  # no DOI: must still load
            ["W-ingest-test-1"],
            publication_date="2018-01-01",
            publication_year=2018,
            journal="Ingest Citer Journal",
            authors=[{"id": "https://openalex.org/A-ingest-citer", "name": "Citer Author"}],
        ),
    }
    rows = ingest.build_rows(selection, works, citers)

    ingest.load(clean_citers, rows)
    ingest.load(clean_citers, rows)  # second run must not create duplicates

    ids = {"ids": list(CITER_IDS)}
    papers = "MATCH (p:Paper) WHERE p.openalex_id IN $ids RETURN count(p) AS n, count(p.doi) AS with_doi"
    labels = "MATCH (p:Paper:Retracted) WHERE p.openalex_id IN $ids RETURN count(p) AS n"
    cites = (
        "MATCH (a:Paper)-[c:CITES]->(:Paper {doi: $doi}) WHERE a.openalex_id IN $ids "
        "RETURN a.openalex_id AS citing, c.before_retraction AS flag ORDER BY citing"
    )
    authored = (
        "MATCH (:Author {openalex_id: 'A-ingest-citer'})-[:AUTHORED]->(p:Paper) "
        "WHERE p.openalex_id IN $ids RETURN count(p) AS n"
    )
    assert clean_citers.read(papers, ids) == [{"n": 2, "with_doi": 1}]
    assert clean_citers.read(labels, ids) == [{"n": 0}]  # citing papers are never labelled Retracted
    assert clean_citers.read(cites, {**ids, "doi": TEST_DOIS[0]}) == [
        {"citing": CITER_IDS[0], "flag": False},  # published after the 2019 retraction
        {"citing": CITER_IDS[1], "flag": True},
    ]
    assert clean_citers.read(authored, ids) == [{"n": 2}]
    journals = "MATCH (j:Journal {name: 'Ingest Citer Journal'}) RETURN count(j) AS n"
    assert clean_citers.read(journals) == [{"n": 1}]


@pytest.mark.neo4j
def test_schema_has_the_constraints_and_the_year_index(neo4j_client):
    ingest.create_schema(neo4j_client)
    constraints = {row["name"] for row in neo4j_client.read("SHOW CONSTRAINTS YIELD name RETURN name")}
    indexes = {row["name"] for row in neo4j_client.read("SHOW INDEXES YIELD name RETURN name")}
    assert {"paper_doi", "paper_openalex_id", "author_id", "journal_name", "reason_name"} <= constraints
    assert "paper_year" in indexes
