"""Tests for the OpenAlex download script (no network, no database)."""

import pytest

from scripts import fetch_openalex as fetch

HEADER = "Record ID,Title,Subject,Journal,RetractionDate,OriginalPaperDOI,RetractionNature,Reason\n"


def test_normalize_doi_removes_prefix_and_lowercases() -> None:
    """A DOI written as a URL with capitals becomes a bare lowercase DOI."""
    assert fetch.normalize_doi("https://doi.org/10.1016/J.ABC.2020.1") == "10.1016/j.abc.2020.1"


def test_normalize_doi_decodes_percent_encoding() -> None:
    """Retraction Watch sometimes writes ``<`` and ``>`` as ``%3c`` and ``%3e``."""
    raw = "10.1002/1096-9896(200107)194:3%3c303::aid-path879%3e3.0.co;2-q"
    assert fetch.normalize_doi(raw) == "10.1002/1096-9896(200107)194:3<303::aid-path879>3.0.co;2-q"


def test_normalize_doi_repairs_a_doi_written_twice() -> None:
    """A DOI pasted twice in a row is cut back to one copy."""
    assert fetch.normalize_doi("10.3991/ijet.v16i08.1921710.3991/ijet.v16i08.19217") == "10.3991/ijet.v16i08.19217"


@pytest.mark.parametrize("value", ["", "unavailable", "n/a", "   "])
def test_normalize_doi_rejects_values_that_are_not_dois(value: str) -> None:
    """Anything that does not start with ``10.`` is not a DOI."""
    assert fetch.normalize_doi(value) is None


def test_split_reasons_strips_plus_signs_and_empty_parts() -> None:
    """The Reason column is a ``;`` list whose items start with ``+``."""
    assert fetch.split_reasons("+Plagiarism;+Fake Peer Review;") == ["Plagiarism", "Fake Peer Review"]
    assert fetch.split_reasons("") == []


def test_batches_cuts_a_list_in_slices() -> None:
    """The last slice may be shorter."""
    assert list(fetch.batches(["a", "b", "c", "d", "e"], 2)) == [["a", "b"], ["c", "d"], ["e"]]


def test_select_retracted_rows_applies_every_filter(tmp_path) -> None:
    """Keeps real retractions of the field with a DOI, once each, and counts what it drops."""
    csv_file = tmp_path / "rw.csv"
    csv_file.write_text(
        HEADER
        + "1,T1,(HSC) Dentistry,J1,5/7/2020 0:00,10.1/AAA,Retraction,+Plagiarism;+Fake Peer Review;\n"
        + "2,T2,(HSC) Dentistry,J1,5/7/2020 0:00,https://doi.org/10.1/aaa,Retraction,+Plagiarism;\n"
        + "3,T3,(HSC) Dentistry,J2,1/1/2021 0:00,unavailable,Retraction,+X;\n"
        + "4,T4,(HSC) Dentistry,J2,1/1/2021 0:00,10.2/b,Reinstatement,\n"
        + "5,T5,(BLS) Biology,J3,1/1/2021 0:00,10.3/c,Retraction,+Y;\n",
        encoding="utf-8",
    )
    rows, stats = fetch.select_retracted_rows(csv_file, "Dentistry")
    assert stats == {"rows_in_field": 4, "not_a_retraction": 1, "no_doi": 1, "duplicate_doi": 1, "kept": 1}
    assert rows == [
        {
            "record_id": "1",
            "doi": "10.1/aaa",
            "title": "T1",
            "journal": "J1",
            "retraction_date": "5/7/2020 0:00",
            "reasons": ["Plagiarism", "Fake Peer Review"],
        }
    ]


def test_fetch_works_by_doi_asks_once_then_reads_the_cache(tmp_path, monkeypatch) -> None:
    """The second call with the same DOIs, in any order, does not touch the API."""
    calls = []

    def fake_get_json(params: dict) -> dict:
        calls.append(params)
        return {"results": [{"doi": "https://doi.org/10.1/aaa"}]}

    monkeypatch.setattr(fetch, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(fetch, "get_json", fake_get_json)

    first, first_from_cache = fetch.fetch_works_by_doi(["10.1/aaa", "10.4/d"], "KEY")
    second, second_from_cache = fetch.fetch_works_by_doi(["10.4/d", "10.1/aaa"], "KEY")

    assert (first_from_cache, second_from_cache) == (False, True)
    assert first == second == [{"doi": "https://doi.org/10.1/aaa"}]
    assert len(calls) == 1
    assert calls[0]["filter"] == "doi:https://doi.org/10.1/aaa|https://doi.org/10.4/d"
