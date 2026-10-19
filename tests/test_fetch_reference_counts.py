"""Tests for the reference counts download script (no network, no database)."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import fetch_reference_counts as counts


def fake_settings(key: str) -> SimpleNamespace:
    """Stand-in for ``get_settings()`` with a chosen API key."""
    return SimpleNamespace(openalex_api_key=SimpleNamespace(get_secret_value=lambda: key))


def save_citers(cache: Path, ids: list[str]) -> None:
    """Two trimmed pages of citers, as fetch_citers.py saves them (one paper on both pages)."""
    (cache / "citers").mkdir()
    half = len(ids) // 2 + 1
    for name, part in (("a.json", ids[:half]), ("b.json", ids[half - 1 :])):
        results = [{"id": f"https://openalex.org/{paper}"} for paper in part]
        (cache / "citers" / name).write_text(json.dumps({"results": results}), encoding="utf-8")


def answer_for(params: dict[str, str]) -> dict[str, Any]:
    """OpenAlex's answer: W<n> has n references; W404 is unknown to OpenAlex."""
    ids = params["filter"].removeprefix("openalex:").split("|")
    return {
        "results": [
            {
                "id": f"https://openalex.org/{paper}",
                "referenced_works": [f"https://openalex.org/R{i}" for i in range(int(paper[1:]))],
            }
            for paper in ids
            if paper != "W404"
        ]
    }


@pytest.fixture(name="cache")
def fixture_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Send every cache read and write to a temporary folder, and never sleep."""
    monkeypatch.setattr(counts, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(counts.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(counts, "get_settings", lambda: fake_settings("key"))
    return tmp_path


def test_citer_ids_reads_every_page_once_and_sorts(cache: Path) -> None:
    save_citers(cache, ["W3", "W1", "W2"])
    assert counts.citer_ids(cache) == ["W1", "W2", "W3"]


def test_fetch_counts_asks_for_the_ids_and_keeps_only_the_length(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get_json(params: dict[str, str]) -> dict[str, Any]:
        calls.append(params)
        return answer_for(params)

    monkeypatch.setattr(counts, "get_json", fake_get_json)
    assert counts.fetch_counts(["W2", "W0"], "key") == {"W2": 2, "W0": 0}
    assert calls[0]["filter"] == "openalex:W2|W0"
    assert calls[0]["select"] == "id,referenced_works"


def test_main_saves_the_counts_and_says_which_are_missing(
    cache: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    save_citers(cache, ["W1", "W3", "W404"])
    monkeypatch.setattr(counts, "get_json", answer_for)
    assert counts.main() == 0
    saved = json.loads((cache / "reference_counts.json").read_text(encoding="utf-8"))
    assert saved["counts"] == {"W1": 1, "W3": 3}
    assert "key" not in json.dumps(saved)
    assert "2 of 3 citing papers have a reference count (1 not found on OpenAlex)" in capsys.readouterr().out


def test_main_skips_the_papers_already_counted(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_citers(cache, ["W1", "W2", "W3"])
    (cache / "reference_counts.json").write_text(json.dumps({"counts": {"W1": 1, "W2": 2}}), encoding="utf-8")
    asked: list[str] = []

    def fake_get_json(params: dict[str, str]) -> dict[str, Any]:
        asked.append(params["filter"])
        return answer_for(params)

    monkeypatch.setattr(counts, "get_json", fake_get_json)
    assert counts.main() == 0
    assert asked == ["openalex:W3"]
    saved = json.loads((cache / "reference_counts.json").read_text(encoding="utf-8"))
    assert saved["counts"] == {"W1": 1, "W2": 2, "W3": 3}


def test_main_stops_without_an_api_key(cache: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(counts, "get_settings", lambda: fake_settings(""))
    assert counts.main() == 1
    assert "OPENALEX_API_KEY" in capsys.readouterr().out
    assert not (cache / "reference_counts.json").exists()


def test_main_stops_when_the_citers_were_not_fetched(cache: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert counts.main() == 1
    assert "fetch_citers" in capsys.readouterr().out
