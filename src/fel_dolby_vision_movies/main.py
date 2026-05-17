from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

from fel_dolby_vision_movies import artifacts, discovery, fetcher
from fel_dolby_vision_movies import parser as fel_parser
from fel_dolby_vision_movies.models import FelRelease
from fel_dolby_vision_movies import sources


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "search-for-sources":
        return _search_for_sources(args.sources)
    if args.command == "scrape-for-titles":
        return _scrape_for_titles(args.sources, args.output_dir, args.cache_dir)
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
        return _scrape_for_titles(args.sources, args.output_dir, args.cache_dir)
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
    return parser


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


def _scrape_for_titles(source_path: Path, output_dir: Path, cache_dir: Path) -> int:
    if not source_path.exists():
        print(f"scrape failed; sources file not found: {source_path}")
        return 1

    source_urls = sources.read_source_urls(source_path)
    if not source_urls:
        print(f"scrape failed; no sources found in {source_path}")
        return 1

    releases: list[FelRelease] = []
    errors: list[tuple[str, str]] = []
    fetched_count = 0

    with fetcher.Fetcher(
        cache_dir=cache_dir,
        cookie_header=os.environ.get("FORUM_COOKIE_HEADER"),
    ) as html_fetcher:
        for url in source_urls:
            try:
                result = html_fetcher.fetch(url)
            except Exception as exc:  # pragma: no cover - exact network errors vary
                errors.append((url, exc.__class__.__name__))
                continue
            fetched_count += 1
            releases.extend(fel_parser.parse_fel_releases(result.text, result.url))

    unique_releases = _dedupe_releases(releases)
    if not unique_releases:
        print(
            f"scrape failed; "
            f"sources={len(source_urls)} "
            f"fetched={fetched_count} "
            f"releases=0 "
            f"errors={len(errors)} "
            f"reason=no releases found"
        )
        return 1

    sorted_releases = artifacts.publish_outputs(unique_releases, output_dir=output_dir)

    print(
        f"scrape complete; "
        f"sources={len(source_urls)} "
        f"fetched={fetched_count} "
        f"releases={len(sorted_releases)} "
        f"errors={len(errors)} "
        f"output_dir={output_dir}"
    )
    return 0


def _dedupe_releases(releases: list[FelRelease]) -> list[FelRelease]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[FelRelease] = []
    for release in releases:
        key = (
            release.movie_title.lower(),
            release.source_url,
            release.fel_evidence.evidence_type,
            release.fel_evidence.quote,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(release)
    return unique


if __name__ == "__main__":
    raise SystemExit(main())
