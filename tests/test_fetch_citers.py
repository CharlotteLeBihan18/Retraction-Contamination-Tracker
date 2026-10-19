"""Tests for the citers download script (no network, no database)."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import fetch_citers as citers

KEY = "test-key"


def fake_settings(key: str) -> SimpleNamespace:
    """Stand-in for ``get_settings()`` with a chosen API key."""
    return SimpleNamespace(openalex_api_key=SimpleNamespace(get_secret_value=lambda: key))


def make_work(number: int, refs: list[str], **extra: Any) -> dict[str, Any]:
    """A citing paper shaped like an OpenAlex answer."""
    work = {
        "id": f"https://openalex.org/W{number}",
        "doi": f"https://doi.org/10.1/c{number}",
        "title": f"Citing paper {number}",
        "publication_year": 2021,
        "publication_date": "2021-03-04",
        "is_retracted": False,
        "referenced_works": [f"https://openalex.org/{ref}" for ref in refs],
        "authorships": [
            {"author": {"id": "https://openalex.org/A1", "display_name": "Ada"}},
            {"author": {"id": None, "display_name": "No Id"}},
        ],
        "primary_location": {"source": {"display_name": "Journal of Tests"}},
    }
    work.update(extra)
    return work


@pytest.fixture(name="cache")
def fixture_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Send every cache read and write to a temporary folder."""
    monkeypatch.setattr(citers, "CACHE_DIR", tmp_path)
    monkeypatch.setattr("scripts.fetch_openalex.CACHE_DIR", tmp_path)
    return tmp_path


def test_short_id_removes_the_openalex_prefix() -> None:
    """The graph and the cache use ``W123``, not the full URL."""
    assert citers.short_id("https://openalex.org/W123") == "W123"


def test_trim_citer_keeps_only_what_the_graph_needs() -> None:
    """Only references to retracted papers survive, authors without an id are dropped, no abstract."""
    work = make_work(1, ["W10", "W99", "W11"], abstract_inverted_index={"x": [0]})
    trimmed = citers.trim_citer(work, {"W10", "W11"})
    assert trimmed["cites_retracted"] == ["W10", "W11"]
    assert trimmed["authors"] == [{"id": "https://openalex.org/A1", "name": "Ada"}]
    assert trimmed["journal"] == "Journal of Tests"
    assert "abstract_inverted_index" not in trimmed and "referenced_works" not in trimmed


def test_trim_citer_survives_missing_fields() -> None:
    """A paper without journal, authors or references still gives a valid record."""
    trimmed = citers.trim_citer({"id": "https://openalex.org/W5", "primary_location": None}, {"W1"})
    assert trimmed["journal"] is None
    assert trimmed["authors"] == []
    assert trimmed["cites_retracted"] == []
    assert trimmed["is_retracted"] is False


def test_retracted_ids_keeps_only_the_selected_papers(cache: Path) -> None:
    """Ids come from the saved works of the selection, sorted. An unrequested paper is ignored."""
    folder = cache / "works_by_doi"
    folder.mkdir()
    works = [
        {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.1/b"},
        {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/a"},
        {"id": "https://openalex.org/W3", "doi": "https://doi.org/10.1/not-selected"},
        {"id": "https://openalex.org/W4", "doi": None},
    ]
    (folder / "x.json").write_text(json.dumps({"results": works}), encoding="utf-8")
    selection = [{"doi": "10.1/a"}, {"doi": "10.1/b"}]
    (cache / "retraction_watch_selection.json").write_text(json.dumps(selection), encoding="utf-8")
    assert citers.retracted_ids() == ["W1", "W2"]


def test_fetch_page_trims_saves_and_then_reads_the_cache(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first call hits the API, the second one reads the file and never calls the API."""
    calls: list[dict[str, str]] = []

    def fake_get_json(params: dict[str, str]) -> dict[str, Any]:
        calls.append(params)
        return {"meta": {"next_cursor": "abc"}, "results": [make_work(1, ["W10"])]}

    monkeypatch.setattr(citers, "get_json", fake_get_json)
    page, from_cache = citers.fetch_page(["W10"], "*", "key", {"W10"})
    assert not from_cache
    assert page["next_cursor"] == "abc"
    assert page["results"][0]["cites_retracted"] == ["W10"]
    assert calls[0]["filter"] == "cites:W10"
    assert "key" not in json.dumps(page)
    again, from_cache = citers.fetch_page(["W10"], "*", "key", {"W10"})
    assert from_cache and again == page and len(calls) == 1
    assert list((cache / "citers").glob("*.json"))


def test_main_follows_the_cursor_and_counts_each_citer_once(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two pages are read, a paper seen twice is counted once, and the report is written."""
    pages = {
        "*": {
            "meta": {"next_cursor": "p2"},
            "results": [make_work(1, ["W10"]), make_work(2, ["W10"], is_retracted=True)],
        },
        "p2": {
            "meta": {"next_cursor": None},
            "results": [make_work(2, ["W10"], is_retracted=True), make_work(3, ["W10"])],
        },
    }
    monkeypatch.setattr(citers, "get_json", lambda params: pages[params["cursor"]])
    monkeypatch.setattr(citers, "retracted_ids", lambda: ["W10"])
    monkeypatch.setattr(citers.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(citers.sys, "argv", ["fetch_citers"])
    monkeypatch.setattr(citers, "get_settings", lambda: fake_settings(KEY))
    assert citers.main() == 0
    report = json.loads((cache / "citers_manifest.json").read_text(encoding="utf-8"))
    assert report["different_citing_papers"] == 3
    assert report["pages"] == 2
    assert report["citing_papers_also_retracted"] == 1
    assert "3 different citing papers" in capsys.readouterr().out


def test_main_stops_without_an_api_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty key is explained, and nothing is downloaded."""
    monkeypatch.setattr(citers.sys, "argv", ["fetch_citers"])
    monkeypatch.setattr(citers, "get_settings", lambda: fake_settings(""))
    assert citers.main() == 1
    assert "OPENALEX_API_KEY" in capsys.readouterr().out