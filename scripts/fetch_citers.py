"""Download the papers that cite our retracted papers (OpenAlex), one hop only.

Step 2 of the download. Step 1 (``fetch_openalex.py``) saved the retracted papers. Here we ask
OpenAlex "who cites these?" and keep the answers in ``data/cache/citers/``.

To keep the repository small, each page is trimmed before it is saved: we keep the id, DOI, title,
year, date, journal, authors, and only the ids of the retracted papers the citing paper references
(the rest of its bibliography is not useful for the graph). Abstracts are left out on purpose.

Run with:  python -m scripts.fetch_citers             (everything)
           python -m scripts.fetch_citers --limit 1   (try with one batch of papers first)
"""

import argparse
import json
import sys
import time
from datetime import date
from typing import Any

from app.config import get_settings
from scripts.fetch_openalex import CACHE_DIR, batches, cache_file, get_json, normalize_doi, write_json

OPENALEX_PREFIX = "https://openalex.org/"
IDS_PER_QUERY = 50  # OpenAlex accepts up to 100 values in one OR filter
PAGE_SIZE = 200
CITER_FIELDS = (
    "id,doi,title,publication_year,publication_date,referenced_works,authorships,primary_location,is_retracted"
)


def short_id(openalex_id: str) -> str:
    """``https://openalex.org/W123`` becomes ``W123``."""
    return openalex_id.removeprefix(OPENALEX_PREFIX)


def retracted_ids() -> list[str]:
    """Short OpenAlex ids (``W...``) of the selected retracted papers saved by step 1, sorted.

    OpenAlex sometimes returns a paper we did not ask for. Only the DOIs of the Retraction Watch
    selection count, so these ids match exactly the papers that the ingestion script loads.
    """
    selection = json.loads((CACHE_DIR / "retraction_watch_selection.json").read_text(encoding="utf-8"))
    selected = {row["doi"] for row in selection}
    ids: set[str] = set()
    for path in (CACHE_DIR / "works_by_doi").glob("*.json"):
        for work in json.loads(path.read_text(encoding="utf-8"))["results"]:
            if work.get("id") and normalize_doi(work.get("doi") or "") in selected:
                ids.add(short_id(work["id"]))
    return sorted(ids)


def trim_citer(work: dict[str, Any], retracted: set[str]) -> dict[str, Any]:
    """Keep only what the graph needs from one citing paper."""
    source = ((work.get("primary_location") or {}).get("source")) or {}
    authors = [
        {"id": (a.get("author") or {}).get("id"), "name": (a.get("author") or {}).get("display_name")}
        for a in work.get("authorships") or []
        if (a.get("author") or {}).get("id")
    ]
    return {
        "id": work.get("id"),
        "doi": work.get("doi"),
        "title": work.get("title"),
        "publication_year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "is_retracted": bool(work.get("is_retracted")),
        "journal": source.get("display_name"),
        "authors": authors,
        "cites_retracted": sorted({short_id(ref) for ref in work.get("referenced_works") or []} & retracted),
    }


def fetch_page(ids: list[str], cursor: str, api_key: str, retracted: set[str]) -> tuple[dict[str, Any], bool]:
    """Return one trimmed page of citers (``results`` and ``next_cursor``), and whether it came from the cache."""
    path = cache_file("citers", "|".join(ids) + "@" + cursor)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), True
    params = {
        "filter": "cites:" + "|".join(ids),
        "per-page": str(PAGE_SIZE),
        "cursor": cursor,
        "select": CITER_FIELDS,
        "api_key": api_key,
    }
    answer = get_json(params)
    page = {
        "cites": ids,
        "cursor": cursor,
        "next_cursor": answer.get("meta", {}).get("next_cursor"),
        "results": [trim_citer(work, retracted) for work in answer["results"]],
    }
    write_json(path, page)
    return page, False


def parse_args() -> argparse.Namespace:
    """Read the command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="only handle the first N groups of papers (quick test)")
    return parser.parse_args()


def main() -> int:
    """Fetch the citers of every retracted paper, write a small report, return a shell exit code."""
    args = parse_args()
    api_key = get_settings().openalex_api_key.get_secret_value()
    if not api_key:
        print("No OpenAlex key found: fill in OPENALEX_API_KEY in your .env file first.")
        return 1
    ids = retracted_ids()
    if not ids:
        print("No retracted papers in data/cache/works_by_doi. Run: python -m scripts.fetch_openalex")
        return 1

    retracted = set(ids)
    citers: dict[str, dict[str, Any]] = {}
    pages = 0
    for number, group in enumerate(list(batches(ids, IDS_PER_QUERY))[: args.limit], start=1):
        cursor: str | None = "*"
        while cursor:
            page, from_cache = fetch_page(group, cursor, api_key, retracted)
            pages += 1
            for work in page["results"]:
                citers[work["id"]] = work
            cursor = page["next_cursor"] if page["results"] else None
            if not from_cache:
                time.sleep(0.2)
        print(f"group {number}: {len(citers)} different citing papers so far")

    write_json(
        CACHE_DIR / "citers_manifest.json",
        {
            "fetched_on": date.today().isoformat(),
            "retracted_papers_asked": len(ids),
            "pages": pages,
            "different_citing_papers": len(citers),
            "citing_papers_also_retracted": sum(1 for c in citers.values() if c["is_retracted"]),
            "citing_papers_without_doi": sum(1 for c in citers.values() if not c["doi"]),
        },
    )
    print(f"{len(citers)} different citing papers in {pages} pages (summary: data/cache/citers_manifest.json).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
