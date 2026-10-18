"""Download the real data from OpenAlex and keep a copy on disk.

Step 1 (this file so far): pick the retracted papers of one research field in the Retraction
Watch CSV, then fetch their records from OpenAlex.

Every API answer is saved in ``data/cache/`` and committed. The project can then run again
without an API key, and the free daily budget is never spent twice on the same request.

Run with:  python -m scripts.fetch_openalex            (everything)
           python -m scripts.fetch_openalex --limit 50  (try with 50 papers first)
"""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
RW_DIR = ROOT / "data" / "raw" / "retraction-watch-data"
CACHE_DIR = ROOT / "data" / "cache"

API_URL = "https://api.openalex.org/works"
BATCH_SIZE = 50
WORK_FIELDS = (
    "id,doi,title,publication_year,publication_date,abstract_inverted_index,"
    "referenced_works,authorships,primary_location,is_retracted,cited_by_count"
)
MAX_TRIES = 5
# Retraction Watch writes 1/1/1900 when the retraction date is unknown
UNKNOWN_DATE_YEAR = 1900


def normalize_doi(value: str) -> str | None:
    """Return a clean lowercase DOI (``10.xxxx/...``), or None when the value is not a DOI.

    Two defects seen in the Retraction Watch CSV are repaired: DOIs written URL-encoded
    (``%3c`` instead of ``<``) and DOIs written twice in a row.
    """
    doi = urllib.parse.unquote(value.strip().lower())
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    half = len(doi) // 2
    if len(doi) % 2 == 0 and doi[:half] == doi[half:]:
        doi = doi[:half]
    return doi if doi.startswith("10.") else None


def iso_date(value: str | None) -> str | None:
    """Turn a Retraction Watch date (month/day/year, ``'3/15/2025 0:00'``) into ``'2025-03-15'``.

    Retraction Watch writes the month first, so ``10/1/2026`` is October 1st. Returns None when
    the value is empty, malformed or the 1900 placeholder that means "unknown".
    """
    try:
        parsed = datetime.strptime(value.strip(), "%m/%d/%Y %H:%M")
    except (ValueError, AttributeError):
        return None
    return None if parsed.year <= UNKNOWN_DATE_YEAR else parsed.date().isoformat()


def split_reasons(raw: str) -> list[str]:
    """Turn ``'+Plagiarism;+Fake Peer Review;'`` into ``['Plagiarism', 'Fake Peer Review']``."""
    return [part.strip("+ ").strip() for part in raw.split(";") if part.strip("+ ").strip()]


def select_retracted_rows(csv_path: Path, subject: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read the Retraction Watch CSV and keep the true retractions of one field that have a DOI.

    Returns the kept rows (one per DOI) and counters that show how many rows each filter dropped.
    """
    stats = {"rows_in_field": 0, "not_a_retraction": 0, "no_doi": 0, "duplicate_doi": 0, "kept": 0}
    kept: dict[str, dict[str, Any]] = {}
    with csv_path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            if subject.lower() not in row.get("Subject", "").lower():
                continue
            stats["rows_in_field"] += 1
            if row.get("RetractionNature") != "Retraction":
                stats["not_a_retraction"] += 1
                continue
            doi = normalize_doi(row.get("OriginalPaperDOI", ""))
            if doi is None:
                stats["no_doi"] += 1
                continue
            if doi in kept:
                stats["duplicate_doi"] += 1
                continue
            kept[doi] = {
                "record_id": row.get("Record ID"),
                "doi": doi,
                "title": row.get("Title"),
                "journal": row.get("Journal"),
                "retraction_date": iso_date(row.get("RetractionDate")),
                "reasons": split_reasons(row.get("Reason", "")),
            }
    stats["kept"] = len(kept)
    return list(kept.values()), stats


def batches(items: list[str], size: int) -> Iterator[list[str]]:
    """Yield successive slices of ``items`` with at most ``size`` elements."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def cache_file(kind: str, key: str) -> Path:
    """Path of the cache file for one request: ``data/cache/<kind>/<hash of key>.json``."""
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return CACHE_DIR / kind / f"{digest}.json"


def get_json(params: dict[str, str]) -> dict[str, Any]:
    """Call the OpenAlex works endpoint and return the decoded JSON.

    Waits and retries on rate limits and server errors. Error messages never contain the URL,
    because the URL holds the API key.
    """
    url = f"{API_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe='')}"
    for attempt in range(1, MAX_TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == MAX_TRIES:
                raise RuntimeError(f"OpenAlex answered HTTP {exc.code} ({exc.reason})") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == MAX_TRIES:
                raise RuntimeError("OpenAlex could not be reached (network error)") from None
        time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def fetch_works_by_doi(dois: list[str], api_key: str) -> tuple[list[dict[str, Any]], bool]:
    """Return the OpenAlex records of up to 50 DOIs, and whether they came from the cache."""
    path = cache_file("works_by_doi", "|".join(sorted(dois)))
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["results"], True
    params = {
        "filter": "doi:" + "|".join(f"https://doi.org/{doi}" for doi in dois),
        "per-page": str(BATCH_SIZE),
        "select": WORK_FIELDS,
        "api_key": api_key,
    }
    results = get_json(params)["results"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"requested": dois, "results": results}, ensure_ascii=False), encoding="utf-8")
    return results, False


def csv_version(folder: Path) -> str:
    """Return the git commit of the cloned Retraction Watch data (its version), or 'unknown'."""
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return done.stdout.strip()


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as readable JSON, creating the folder when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Read the command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", default="Dentistry", help="research field to keep (default: Dentistry)")
    parser.add_argument("--csv", type=Path, default=RW_DIR / "retraction_watch.csv", help="Retraction Watch CSV")
    parser.add_argument("--limit", type=int, default=None, help="only fetch the first N papers (for a quick test)")
    return parser.parse_args()


def main() -> int:
    """Select the retracted papers, fetch them from OpenAlex and report; return a shell exit code."""
    args = parse_args()
    api_key = get_settings().openalex_api_key.get_secret_value()
    if not api_key:
        print("OPENALEX_API_KEY is empty. Put your key in .env first.")
        return 1
    if not args.csv.exists():
        print(f"Retraction Watch CSV not found: {args.csv}")
        return 1

    rows, stats = select_retracted_rows(args.csv, args.subject)
    print(f"Retraction Watch, field '{args.subject}': {stats}")
    write_json(CACHE_DIR / "retraction_watch_selection.json", rows)

    dois = [row["doi"] for row in rows][: args.limit]
    found: dict[str, dict[str, Any]] = {}
    unrequested: set[str] = set()
    for number, batch in enumerate(batches(dois, BATCH_SIZE), start=1):
        results, from_cache = fetch_works_by_doi(batch, api_key)
        for work in results:
            doi = normalize_doi(work.get("doi") or "")
            if doi in batch:
                found[doi] = work
            elif doi:
                unrequested.add(doi)
        print(f"batch {number}: {len(results)}/{len(batch)} papers ({'cache' if from_cache else 'downloaded'})")
        if not from_cache:
            time.sleep(0.2)

    missing = sorted(set(dois) - set(found))
    write_json(
        CACHE_DIR / "manifest.json",
        {
            "fetched_on": date.today().isoformat(),
            "subject": args.subject,
            "retraction_watch_commit": csv_version(RW_DIR),
            "filters": stats,
            "requested": len(dois),
            "found_in_openalex": len(found),
            "missing_in_openalex": missing,
            "returned_but_not_requested": sorted(unrequested),
        },
    )
    print(f"Found {len(found)} of {len(dois)} papers in OpenAlex; {len(missing)} missing (listed in manifest.json).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
