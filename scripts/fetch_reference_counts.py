"""Download how many references each citing paper has (OpenAlex), for the at-risk model.

Step 3 of the download. The pages saved by ``fetch_citers.py`` only keep the references of a
citing paper that point to our retracted papers, so its full number of references is lost. Here we
ask OpenAlex for the reference list of every citing paper, keep only its length, and save all the
counts in one small file: ``data/cache/reference_counts.json``.

The retracted papers don't need this step: their full records (step 1) already list all their
references. The loader (``ingest.py``) stores both as ``Paper.reference_count``.

The file is written after every request, so a run that stops halfway can simply be started again:
the papers already counted are skipped.

Run with:  python -m scripts.fetch_reference_counts
"""

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from app.config import get_settings
from scripts.fetch_citers import short_id
from scripts.fetch_openalex import CACHE_DIR, batches, get_json, write_json

IDS_PER_QUERY = 50  # OpenAlex accepts up to 100 values in one OR filter, and 100 results per page
FIELDS = "id,referenced_works"
OUT_NAME = "reference_counts.json"


def citer_ids(cache_dir: Path = CACHE_DIR) -> list[str]:
    """Short OpenAlex ids (``W...``) of every citing paper saved by ``fetch_citers.py``, sorted."""
    ids: set[str] = set()
    for path in (cache_dir / "citers").glob("*.json"):
        for work in json.loads(path.read_text(encoding="utf-8"))["results"]:
            ids.add(short_id(work["id"]))
    return sorted(ids)


def read_counts(cache_dir: Path = CACHE_DIR) -> dict[str, int]:
    """The counts saved by an earlier run, or nothing."""
    path = cache_dir / OUT_NAME
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["counts"]


def fetch_counts(ids: list[str], api_key: str) -> dict[str, int]:
    """Number of references of up to ``IDS_PER_QUERY`` papers, by short OpenAlex id."""
    params = {
        "filter": "openalex:" + "|".join(ids),
        "per-page": str(IDS_PER_QUERY),
        "select": FIELDS,
        "api_key": api_key,
    }
    answer: dict[str, Any] = get_json(params)
    return {short_id(work["id"]): len(work.get("referenced_works") or []) for work in answer["results"]}


def main() -> int:
    """Count the references of every citing paper and save them; return a shell exit code."""
    api_key = get_settings().openalex_api_key.get_secret_value()
    if not api_key:
        print("No OpenAlex key found: fill in OPENALEX_API_KEY in your .env file first.")
        return 1
    ids = citer_ids(CACHE_DIR)
    if not ids:
        print("No citing papers in data/cache/citers. Run: python -m scripts.fetch_citers")
        return 1

    counts = read_counts(CACHE_DIR)
    todo = [paper for paper in ids if paper not in counts]
    for number, group in enumerate(batches(todo, IDS_PER_QUERY), start=1):
        counts.update(fetch_counts(group, api_key))
        write_json(
            CACHE_DIR / OUT_NAME,
            {"fetched_on": date.today().isoformat(), "counts": dict(sorted(counts.items()))},
        )
        print(f"request {number}: {len(counts)} of {len(ids)} citing papers counted", flush=True)
        time.sleep(0.2)
    missing = len([paper for paper in ids if paper not in counts])
    print(f"{len(ids) - missing} of {len(ids)} citing papers have a reference count ({missing} not found on OpenAlex).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
