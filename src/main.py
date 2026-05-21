from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

import artifacts
import compare
import csv
import discovery
import enrich
import fel_cleanup
import fel_ingest
import fetcher
import google_sheets
import parser as fel_parser
from merge import canonical_key, dedupe_releases
from models import FelEvidence, FelRelease
import reddit_source
import sources


DEFAULT_SCRAPE_WORKERS = 6
DEFAULT_GOOGLE_SHEETS_PATH = Path("google_sheets.txt")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "search-for-sources":
        return _search_for_sources(args.sources)
    if args.command == "scrape-for-titles":
        return _scrape_for_titles(
            args.sources, args.output_dir, args.cache_dir, args.workers
        )
    if args.command == "compare-found":
        summary = compare.compare_found(
            args.sources,
            args.output_dir,
            args.cache_dir,
            args.workers,
            args.use_ai,
            args.ai_limit,
        )
        print(
            "compare complete; "
            f"AI_found={summary['AI_found']} "
            f"PY_found={summary['PY_found']} "
            f"overlap={summary['overlap']} "
            f"AI_only={summary['AI_only']} "
            f"PY_only={summary['PY_only']} "
            f"output_dir={args.output_dir}"
        )
        return 0
    if args.command == "clean-fel":
        return _clean_fel(args.input, args.output, args.report, args.cache, args.env)
    if args.command == "run":
        search_exit_code = _search_for_sources(args.sources)
        if search_exit_code != 0:
            existing_urls = sources.read_source_urls(args.sources)
            if not existing_urls:
                print(
                    "source discovery failed and no usable sources file is available; "
                    f"sources={args.sources}"
                )
                return search_exit_code
            print(
                "source discovery failed; scraping existing sources; "
                f"sources={len(existing_urls)}"
            )
        return _scrape_for_titles(
            args.sources, args.output_dir, args.cache_dir, args.workers
        )
    if args.command == "migrate":
        return run_migration(args.fel, args.raw_fel, args.output_dir, args.report)
    parser.error(f"unknown command: {args.command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fel-dolby-vision-movies",
        description="Discover and publish Dolby Vision Profile 7 FEL source data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser(
        "search-for-sources",
        help="discover candidate forum/list sources",
    )
    search.add_argument(
        "--sources",
        type=Path,
        default=Path("forums.txt"),
        help="path to the source registry",
    )
    for command in ("scrape-for-titles", "run"):
        scrape = subparsers.add_parser(command, help=argparse.SUPPRESS)
        scrape.add_argument(
            "--sources",
            type=Path,
            default=Path("forums.txt"),
            help=argparse.SUPPRESS,
        )
        scrape.add_argument(
            "--output-dir",
            type=Path,
            default=Path("."),
            help=argparse.SUPPRESS,
        )
        scrape.add_argument(
            "--cache-dir",
            type=Path,
            default=Path(".cache/html"),
            help=argparse.SUPPRESS,
        )
        scrape.add_argument(
            "--workers",
            type=_positive_int,
            default=DEFAULT_SCRAPE_WORKERS,
            help=argparse.SUPPRESS,
        )
    compare_found = subparsers.add_parser(
        "compare-found",
        help="compare AI-assisted extraction with the deterministic parser",
    )
    compare_found.add_argument(
        "--sources",
        type=Path,
        default=Path("forums.txt"),
        help="path to the source registry",
    )
    compare_found.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="directory for comparison artifacts",
    )
    compare_found.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/html"),
        help="directory for fetched source cache",
    )
    compare_found.add_argument(
        "--workers",
        type=_positive_int,
        default=DEFAULT_SCRAPE_WORKERS,
        help="deterministic scraper worker count",
    )
    compare_found.add_argument(
        "--use-ai",
        action="store_true",
        help="call the configured OpenAI-compatible API for AI extraction",
    )
    compare_found.add_argument(
        "--ai-limit",
        type=_positive_int,
        default=None,
        help="maximum number of source pages to send to the AI API",
    )
    clean_fel = subparsers.add_parser(
        "clean-fel",
        help="canonicalize and merge FEL.txt rows with TMDB movie metadata",
    )
    clean_fel.add_argument("--input", type=Path, default=Path("FEL.txt"))
    clean_fel.add_argument("--output", type=Path, default=Path("FEL.txt"))
    clean_fel.add_argument(
        "--report", type=Path, default=fel_cleanup.DEFAULT_REPORT_PATH
    )
    clean_fel.add_argument("--cache", type=Path, default=fel_cleanup.DEFAULT_CACHE_PATH)
    clean_fel.add_argument("--env", type=Path, default=Path(".env"))
    migrate = subparsers.add_parser(
        "migrate",
        help="one-time merge of FEL.txt + raw_fel.txt into releases.json",
    )
    migrate.add_argument("--fel", type=Path, default=Path("FEL.txt"))
    migrate.add_argument("--raw-fel", type=Path, default=Path("raw_fel.txt"))
    migrate.add_argument("--output-dir", type=Path, default=Path("."))
    migrate.add_argument(
        "--report", type=Path, default=Path("data/migration_report.csv")
    )
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _search_for_sources(source_path: Path) -> int:
    existing_urls = sources.read_source_urls(source_path)
    existing_url_set = set(existing_urls)
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    result = discovery.discover_source_candidates(api_key=api_key)
    new_urls = [url for url in result.candidate_urls if url not in existing_url_set]

    if new_urls:
        sources.merge_confirmed_sources(source_path, result.candidate_urls)

    if result.brave_available:
        status = "Brave available"
    else:
        status = "Brave unavailable: BRAVE_SEARCH_API_KEY is not set"
    print(
        f"{status}; "
        f"existing={len(existing_urls)} "
        f"queries={len(result.queries)} "
        f"raw={result.raw_url_count} "
        f"rejected={result.rejected_url_count} "
        f"candidates={len(result.candidate_urls)} "
        f"added={len(new_urls)}"
    )
    errors = getattr(result, "errors", [])
    if errors:
        print(f"errors={len(errors)}")
    return 0


def _scrape_for_titles(
    source_path: Path, output_dir: Path, cache_dir: Path, workers: int
) -> int:
    if not source_path.exists():
        print(f"scrape failed; sources file not found: {source_path}")
        return 1

    forum_urls = sources.read_source_urls(source_path)
    google_sheet_urls = sources.read_source_urls(_google_sheets_path_for(source_path))
    source_jobs = [
        *[_SourceJob(url=url, source_type="forum") for url in forum_urls],
        *[_SourceJob(url=url, source_type="google-sheet") for url in google_sheet_urls],
    ]
    if not source_jobs:
        print(f"scrape failed; no sources found in {source_path}")
        return 1

    releases: list[FelRelease] = []
    errors: list[tuple[str, str]] = []
    fetched_count = 0

    with fetcher.Fetcher(
        cache_dir=cache_dir,
        cookie_header=os.environ.get("FORUM_COOKIE_HEADER"),
    ) as html_fetcher:
        worker_count = min(workers, len(source_jobs))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for scrape_result in executor.map(
                lambda job: _scrape_source(job, html_fetcher), source_jobs
            ):
                if scrape_result.error:
                    errors.append((scrape_result.url, scrape_result.error))
                    continue
                fetched_count += 1
                releases.extend(scrape_result.releases)

    unique_releases = dedupe_releases(releases, canonical_key)
    _enrich_if_possible(unique_releases)
    sorted_releases = artifacts.publish_outputs(unique_releases, output_dir=output_dir)

    print(
        f"scrape complete; "
        f"sources={len(source_jobs)} "
        f"forums={len(forum_urls)} "
        f"google_sheets={len(google_sheet_urls)} "
        f"fetched={fetched_count} "
        f"releases={len(sorted_releases)} "
        f"errors={len(errors)} "
        f"output_dir={output_dir}"
    )
    return 0


def _enrich_if_possible(releases: list[FelRelease]) -> None:
    try:
        api_key = enrich.load_tmdb_api_key()
    except RuntimeError:
        print("TMDB enrichment skipped; TMDB_API_KEY is not configured")
        return
    import httpx

    with enrich.TmdbResolver(api_key=api_key) as resolver:
        with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
            summary = enrich.enrich_releases(
                releases, resolver, client=client, api_key=api_key
            )
    print(
        "enrichment complete; "
        f"resolved={summary.resolved} "
        f"unresolved={summary.unresolved} "
        f"posters_downloaded={summary.posters_downloaded}"
    )


def _clean_fel(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    cache_path: Path,
    env_path: Path,
) -> int:
    api_key = fel_cleanup.load_tmdb_api_key(env_path)
    with fel_cleanup.TmdbResolver(api_key=api_key, cache_path=cache_path) as resolver:
        summary = fel_cleanup.clean_fel_file(
            input_path, output_path, report_path, resolver
        )
    print(
        "FEL cleanup complete; "
        f"input_rows={summary.input_rows} "
        f"output_rows={summary.output_rows} "
        f"dropped={summary.dropped_rows} "
        f"resolved={summary.resolved_rows} "
        f"unresolved={summary.unresolved_rows} "
        f"merged={summary.merged_rows} "
        f"report={report_path}"
    )
    return 0


def run_migration(
    fel_path: Path,
    raw_fel_path: Path,
    output_dir: Path,
    report_path: Path,
) -> int:
    ingested: list[FelRelease] = []
    if fel_path.exists():
        ingested.extend(fel_ingest.parse_fel_txt(fel_path.read_text(encoding="utf-8")))
    if raw_fel_path.exists():
        ingested.extend(
            fel_ingest.parse_raw_fel_txt(raw_fel_path.read_text(encoding="utf-8"))
        )
    input_titles = [(r.movie_title, r.release_date) for r in ingested]

    unique = dedupe_releases(ingested, canonical_key)
    _enrich_if_possible(unique)
    sorted_releases = artifacts.publish_outputs(unique, output_dir=output_dir)

    by_key = {canonical_key(r): r for r in sorted_releases}
    _write_migration_report(report_path, input_titles, by_key)

    matched = sum(
        1
        for title, year in input_titles
        if canonical_key(_title_probe(title, year)) in by_key
    )
    print(
        "migration complete; "
        f"input_titles={len(input_titles)} "
        f"matched={matched} "
        f"unmatched={len(input_titles) - matched} "
        f"releases={len(sorted_releases)} "
        f"report={report_path}"
    )
    return 0


def _title_probe(title: str, year: str) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=year,
        fel_evidence=FelEvidence(source_url="", quote="", evidence_type="probe"),
    )


def _write_migration_report(
    report_path: Path,
    input_titles: list[tuple[str, str]],
    by_key: dict[tuple[str, str], FelRelease],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["input_title", "input_year", "matched", "tmdb_id"])
        for title, year in input_titles:
            release = by_key.get(canonical_key(_title_probe(title, year)))
            writer.writerow(
                [
                    title,
                    year,
                    "yes" if release is not None else "no",
                    release.tmdb_id if release is not None else "",
                ]
            )


@dataclass(frozen=True)
class _SourceJob:
    url: str
    source_type: str


@dataclass(frozen=True)
class _SourceScrapeResult:
    url: str
    releases: list[FelRelease]
    error: str = ""


def _scrape_source(
    source_job: _SourceJob, html_fetcher: fetcher.Fetcher
) -> _SourceScrapeResult:
    try:
        fetch_url = source_job.url
        if source_job.source_type == "google-sheet":
            fetch_url = google_sheets.google_sheet_csv_url(source_job.url)
        result = html_fetcher.fetch(fetch_url)
        if source_job.source_type == "google-sheet":
            releases = google_sheets.parse_google_sheet_releases(
                result.text, source_job.url
            )
        else:
            if "reddit.com" in urlparse(source_job.url).netloc:
                releases = reddit_source.parse_reddit_releases(result.text, result.url)
            else:
                releases = fel_parser.parse_fel_releases(result.text, result.url)
    except Exception as exc:  # pragma: no cover - exact network/parser errors vary
        return _SourceScrapeResult(
            url=source_job.url, releases=[], error=exc.__class__.__name__
        )
    return _SourceScrapeResult(url=source_job.url, releases=releases)


def _google_sheets_path_for(source_path: Path) -> Path:
    if source_path == Path("forums.txt"):
        return DEFAULT_GOOGLE_SHEETS_PATH
    return source_path.with_name("google_sheets.txt")


if __name__ == "__main__":
    raise SystemExit(main())
