"""Tests for scripts/ingest.py.

Most tests need no database: the pure functions are tested directly and the Neo4j calls go
through the FakeNeo4jClient from conftest.py. The last tests (marked ``neo4j``) run the real
queries on a real database and are skipped when Neo4j is not running.
"""

import json

import pytest

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


def make_selection(doi: str, date: str = "3/15/2020 0:00", reasons: list[str] | None = None) -> dict:
    """A minimal row of retraction_watch_selection.json."""
    return {
        "record_id": "1",
        "doi": doi,
        "title": f"RW title of {doi}",
        "journal": "RW Journal",
        "retraction_date": date,
        "reasons": ["Plagiarism"] if reasons is None else reasons,
    }


# --- Pure functions ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("3/15/2025 0:00", "2025-03-15"),
        ("10/1/2026 0:00", "2026-10-01"),
        ("1/1/1900 0:00", None),  # Retraction Watch's placeholder for "unknown"
        ("", None),
        (None, None),
        ("not a date", None),
    ],
)
def test_parse_rw_date(raw, expected):
    assert ingest.parse_rw_date(raw) == expected


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
        make_selection("10.1/old", date="1/1/2015 0:00"),
        make_selection("10.1/new", date="1/1/2030 0:00"),
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


# --- Database calls, with the fake client ------------------------------------------------------


def test_load_creates_the_schema_before_any_data(fake_db):
    fake_db.rows = [{"papers": 1}]
    rows = {"papers": [{"doi": "10.1/a"}], "notices": [], "authors": [], "citations": []}

    summary = ingest.load(fake_db, rows)

    assert summary == {"papers": 1}
    queries = [query for query, _ in fake_db.queries]
    schema_count = len(ingest.SCHEMA_STATEMENTS)
    assert queries[:schema_count] == ingest.SCHEMA_STATEMENTS
    assert queries[schema_count] == ingest.PAPERS_QUERY
    assert fake_db.queries[schema_count][1] == {"rows": [{"doi": "10.1/a"}]}


def test_run_batches_sends_rows_by_batch_not_one_by_one(fake_db):
    rows = [{"n": n} for n in range(ingest.BATCH_SIZE * 2 + 1)]
    ingest.run_batches(fake_db, ingest.AUTHORS_QUERY, rows)
    assert [len(params["rows"]) for _, params in fake_db.queries] == [ingest.BATCH_SIZE, ingest.BATCH_SIZE, 1]


def test_every_query_reads_its_data_from_the_rows_parameter():
    for query in (ingest.PAPERS_QUERY, ingest.NOTICES_QUERY, ingest.AUTHORS_QUERY, ingest.CITATIONS_QUERY):
        assert query.lstrip().startswith("UNWIND $rows AS row")


def test_main_dry_run_does_not_touch_the_database(monkeypatch, capsys):
    monkeypatch.setattr(
        ingest, "load_cache", lambda: ([make_selection("10.1/a")], {"10.1/a": make_work("W1", "10.1/a")})
    )
    monkeypatch.setattr(ingest.Neo4jClient, "from_settings", lambda settings: pytest.fail("must not connect"))

    assert ingest.main(["--dry-run"]) == 0
    assert "Papers: 1 of 1 selected" in capsys.readouterr().out


def test_main_fails_cleanly_when_neo4j_is_down(monkeypatch, fake_db, capsys):
    fake_db.available = False
    monkeypatch.setattr(
        ingest, "load_cache", lambda: ([make_selection("10.1/a")], {"10.1/a": make_work("W1", "10.1/a")})
    )
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


@pytest.mark.neo4j
def test_schema_has_the_constraints_and_the_year_index(neo4j_client):
    ingest.create_schema(neo4j_client)
    constraints = {row["name"] for row in neo4j_client.read("SHOW CONSTRAINTS YIELD name RETURN name")}
    indexes = {row["name"] for row in neo4j_client.read("SHOW INDEXES YIELD name RETURN name")}
    assert {"paper_doi", "paper_openalex_id", "author_id", "journal_name", "reason_name"} <= constraints
    assert "paper_year" in indexes
