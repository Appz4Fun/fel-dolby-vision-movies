from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

import ai_scrape
import artifacts
import compare
import discovery
import enrich
import fel_ingest
import fetcher
import google_sheets
import list_sources
import parser as fel_parser
from merge import canonical_key, dedupe_releases
from models import FelRelease, release_from_dict
import reddit_source
import sources


DEFAULT_SCRAPE_WORKERS = 6


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "search-for-sources":
        return _search_for_sources(args.sources)
    if args.command == "scrape-for-titles":
        return _scrape_for_titles(
            args.sources, args.output_dir, args.cache_dir, args.workers, False
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
            args.sources,
            args.output_dir,
            args.cache_dir,
            args.workers,
            args.re_enrich,
        )
    if args.command == "migrate":
        return run_migration(args.fel, args.raw_fel, args.output_dir, args.report)
    if args.command == "ai-scrape":
        return ai_scrape.run_ai_scrape(args.sources, args.output_dir, args.cache_dir)
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
        default=Path("data/sources_needs_evidence.txt"),
        help="path to the source registry",
    )
    for command in ("scrape-for-titles", "run"):
        scrape = subparsers.add_parser(command, help=argparse.SUPPRESS)
        scrape.add_argument(
            "--sources",
            type=Path,
            default=Path("data/sources_needs_evidence.txt"),
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
    subparsers.choices["run"].add_argument(
        "--re-enrich",
        action="store_true",
        help="re-run TMDB + blu-ray enrichment over all existing releases",
    )
    compare_found = subparsers.add_parser(
        "compare-found",
        help="compare AI-assisted extraction with the deterministic parser",
    )
    compare_found.add_argument(
        "--sources",
        type=Path,
        default=Path("data/sources_needs_evidence.txt"),
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
    ai_scrape_parser = subparsers.add_parser(
        "ai-scrape",
        help="AI-assisted source discovery and FEL extraction via codex",
    )
    ai_scrape_parser.add_argument(
        "--sources", type=Path, default=Path("data/sources_needs_evidence.txt")
    )
    ai_scrape_parser.add_argument("--output-dir", type=Path, default=Path("."))
    ai_scrape_parser.add_argument("--cache-dir", type=Path, default=Path(".cache/html"))
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
    source_path: Path,
    output_dir: Path,
    cache_dir: Path,
    workers: int,
    re_enrich: bool = False,
) -> int:
    if not source_path.exists():
        print(f"scrape failed; sources file not found: {source_path}")
        return 1

    needs_evidence_urls = sources.read_source_urls(source_path)
    always_fel_urls = sources.read_source_urls(_always_fel_path_for(source_path))
    source_jobs = [
        *[
            _SourceJob(url=url, strictness="needs-evidence")
            for url in needs_evidence_urls
        ],
        *[_SourceJob(url=url, strictness="always-fel") for url in always_fel_urls],
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
    if re_enrich:
        releases_path = Path(output_dir) / "data" / "releases.json"
        if releases_path.exists():
            existing = [
                release_from_dict(item)
                for item in json.loads(releases_path.read_text(encoding="utf-8"))
            ]
            unique_releases = dedupe_releases(
                [*existing, *unique_releases], canonical_key
            )
    _enrich_if_possible(unique_releases)
    sorted_releases = artifacts.publish_outputs(unique_releases, output_dir=output_dir)

    print(
        f"scrape complete; "
        f"sources={len(source_jobs)} "
        f"always_fel={len(always_fel_urls)} "
        f"needs_evidence={len(needs_evidence_urls)} "
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

    import bluray

    with enrich.TmdbResolver(api_key=api_key) as resolver:
        with bluray.BlurayResolver() as bluray_resolver:
            with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                summary = enrich.enrich_releases(
                    releases,
                    resolver,
                    client=client,
                    api_key=api_key,
                    bluray_resolver=bluray_resolver,
                )
    print(
        "enrichment complete; "
        f"resolved={summary.resolved} "
        f"unresolved={summary.unresolved} "
        f"posters_downloaded={summary.posters_downloaded} "
        f"failed={summary.failed} "
        f"bluray_matched={summary.bluray_matched} "
        f"bluray_failed={summary.bluray_failed}"
    )


def run_migration(
    fel_path: Path,
    raw_fel_path: Path,
    output_dir: Path,
    report_path: Path,
) -> int:
    fel_text = fel_path.read_text(encoding="utf-8") if fel_path.exists() else ""
    raw_fel_text = (
        raw_fel_path.read_text(encoding="utf-8") if raw_fel_path.exists() else ""
    )
    fel_txt_rows = sum(1 for line in fel_text.splitlines() if line.strip())
    fel_releases = fel_ingest.parse_fel_txt(fel_text)
    raw_fel_releases = fel_ingest.parse_raw_fel_txt(raw_fel_text)
    ingested = [*fel_releases, *raw_fel_releases]

    unique = dedupe_releases(ingested, canonical_key)
    _enrich_if_possible(unique)
    sorted_releases = artifacts.publish_outputs(unique, output_dir=output_dir)

    by_key = {canonical_key(release): release for release in sorted_releases}
    resolved = _write_migration_report(report_path, ingested, by_key)

    print(
        "migration complete; "
        f"fel_txt_rows={fel_txt_rows} "
        f"fel_ingested={len(fel_releases)} "
        f"fel_dropped={fel_txt_rows - len(fel_releases)} "
        f"raw_fel_ingested={len(raw_fel_releases)} "
        f"tmdb_resolved={resolved} "
        f"tmdb_unresolved={len(ingested) - resolved} "
        f"releases={len(sorted_releases)} "
        f"report={report_path}"
    )
    return 0


def _write_migration_report(
    report_path: Path,
    ingested: list[FelRelease],
    by_key: dict[tuple[str, str], FelRelease],
) -> int:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = 0
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["input_title", "input_year", "tmdb_resolved", "tmdb_id"])
        for release in ingested:
            final = by_key.get(canonical_key(release))
            tmdb_id = final.tmdb_id if final is not None else ""
            if tmdb_id:
                resolved += 1
            writer.writerow(
                [
                    release.movie_title,
                    release.release_date,
                    "yes" if tmdb_id else "no",
                    tmdb_id,
                ]
            )
    return resolved


@dataclass(frozen=True)
class _SourceJob:
    url: str
    strictness: str


@dataclass(frozen=True)
class _SourceScrapeResult:
    url: str
    releases: list[FelRelease]
    error: str = ""


def _scrape_source(
    source_job: _SourceJob, html_fetcher: fetcher.Fetcher
) -> _SourceScrapeResult:
    """Fetch and parse one source. Parser is chosen by domain, strictness by file.

    always-fel sources yield every listed title; needs-evidence sources only
    yield titles backed by a direct FEL marker on the page.
    """
    url = source_job.url
    always_fel = source_job.strictness == "always-fel"
    domain = urlparse(url).netloc
    try:
        if "docs.google.com" in domain:
            text = html_fetcher.fetch(google_sheets.google_sheet_csv_url(url)).text
            if always_fel:
                releases = google_sheets.parse_always_fel_sheet(text, url)
            else:
                releases = google_sheets.parse_google_sheet_releases(text, url)
        elif "reddit.com" in domain:
            releases = reddit_source.parse_reddit_releases(
                html_fetcher.fetch(url).text, url
            )
        elif "github.com" in domain:
            readme = html_fetcher.fetch(_github_readme_url(url)).text
            releases = list_sources.parse_github_md_list(readme, url)
        elif "web.archive.org" in domain:
            releases = list_sources.parse_discourse_list(
                html_fetcher.fetch(url).text, url
            )
        elif "letterboxd.com" in domain:
            releases = _scrape_letterboxd(url, html_fetcher)
        else:
            releases = fel_parser.parse_fel_releases(html_fetcher.fetch(url).text, url)
    except Exception as exc:  # pragma: no cover - exact network/parser errors vary
        return _SourceScrapeResult(url=url, releases=[], error=exc.__class__.__name__)
    return _SourceScrapeResult(url=url, releases=releases)


def _github_readme_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) >= 2:
        return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/HEAD/README.md"
    return url


def _scrape_letterboxd(url: str, html_fetcher: fetcher.Fetcher) -> list[FelRelease]:
    first_page = html_fetcher.fetch(url).text
    releases = list_sources.parse_letterboxd_list(first_page, url)
    for page in range(2, list_sources.letterboxd_page_count(first_page) + 1):
        page_html = html_fetcher.fetch(
            f"{url.rstrip('/')}/page/{page}/", raise_on_error=False
        ).text
        if page_html:
            releases.extend(list_sources.parse_letterboxd_list(page_html, url))
    return releases


def _always_fel_path_for(source_path: Path) -> Path:
    return source_path.with_name("sources_always_fel.txt")


if __name__ == "__main__":
    raise SystemExit(main())
