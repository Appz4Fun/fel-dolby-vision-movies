"""One-off import of hdencode_fel_scan.csv findings into data/releases.json.

Mirrors the daily GHA refresh pipeline (fel_ingest -> enrich -> artifacts) but
sources rows from a local hdencode scanner CSV instead of scraped forum pages.
Run manually: PYTHONPATH=src python3 scripts/hdencode_import.py
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, "src")

import httpx

import artifacts
import bluray
import enrich
from models import FelEvidence, FelRelease, release_from_dict

CSV_PATH = Path("/Users/allen/git/hdencode-fel-scanner/hdencode_fel_scan.csv")
RELEASES_PATH = Path("data/releases.json")
SOURCE_LABEL = "hdencode"
EVIDENCE_TYPE = "hdencode-fel-scan"


def load_csv_rows() -> list[dict[str, str]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["is_fel"].strip().lower() == "true"]
    # de-dupe by imdb_id, keep first occurrence
    seen: set[str] = set()
    unique_rows = []
    for row in rows:
        imdb = row["imdb_id"].strip()
        if not imdb or imdb in seen:
            continue
        seen.add(imdb)
        unique_rows.append(row)
    return unique_rows


def main() -> int:
    collected_at = datetime.now(timezone.utc).isoformat()
    rows = load_csv_rows()

    existing_raw = json.loads(RELEASES_PATH.read_text(encoding="utf-8"))
    existing = [release_from_dict(item) for item in existing_raw]
    existing_by_imdb = {r.imdb_id: r for r in existing if r.imdb_id}

    new_rows = [r for r in rows if r["imdb_id"].strip() not in existing_by_imdb]
    dupe_rows = [r for r in rows if r["imdb_id"].strip() in existing_by_imdb]

    print(f"csv unique fel rows: {len(rows)}")
    print(f"new: {len(new_rows)}  dupes(extra-evidence): {len(dupe_rows)}")

    # --- Step 1: add hdencode url as extra evidence source on existing dupes ---
    patched = 0
    for row in dupe_rows:
        release = existing_by_imdb[row["imdb_id"].strip()]
        ac = release.additional_characteristics
        urls = list(ac.get("source_urls", []))
        if row["url"] not in urls:
            urls.append(row["url"])
            ac["source_urls"] = urls
            patched += 1
    print(f"patched dupe evidence: {patched}")
    RELEASES_PATH.write_text(
        json.dumps([r.to_dict() for r in existing], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # --- Step 2: build new FelRelease rows from CSV ---
    new_releases: list[FelRelease] = []
    for row in new_rows:
        title = row["imdb_title"].strip() or row["title"].strip()
        year = row["imdb_year"].strip()
        quote = f"{row['reason'].strip()} | {row['evidence'].strip()}"
        new_releases.append(
            FelRelease(
                movie_title=title,
                release_date=year or "Unknown",
                fel_evidence=FelEvidence(
                    source_url=row["url"].strip(),
                    quote=quote,
                    evidence_type=EVIDENCE_TYPE,
                ),
                imdb_id=row["imdb_id"].strip(),
                source_label=SOURCE_LABEL,
                collected_at=collected_at,
            )
        )

    # --- Step 3: enrich (TMDB + blu-ray.com) ---
    api_key = enrich.load_tmdb_api_key()
    with enrich.TmdbResolver(api_key=api_key) as resolver:
        with bluray.BlurayResolver() as bluray_resolver:
            with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                summary = enrich.enrich_releases(
                    new_releases,
                    resolver,
                    client=client,
                    api_key=api_key,
                    bluray_resolver=bluray_resolver,
                )
    print(
        "enrichment complete; "
        f"resolved={summary.resolved} unresolved={summary.unresolved} "
        f"posters_downloaded={summary.posters_downloaded} failed={summary.failed} "
        f"bluray_matched={summary.bluray_matched} bluray_failed={summary.bluray_failed}"
    )

    Path("/tmp/hdencode_new_releases.json").write_text(
        json.dumps([r.to_dict() for r in new_releases], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # --- Step 4: merge into releases.json via the real publish pipeline ---
    artifacts.publish_outputs(new_releases, output_dir=".")
    print("published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
