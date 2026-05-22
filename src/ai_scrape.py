"""AI-assisted (codex) source discovery and FEL extraction.

Runs on top of the deterministic Python scrape: the codex model is asked to
discover new FEL source pages and to extract FEL releases from fetched pages.
AI-found releases are tagged ``evidence_type="ai-extracted"`` and merged into
``data/releases.json`` through the normal merge/enrich/publish machinery.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.parse

import httpx

from compare import AIClient, AISettings, FoundCandidate
import fetcher
import google_sheets
from models import UNKNOWN, FelEvidence, FelRelease, release_from_dict
import sources


_DISCOVERY_SYSTEM = (
    "You locate web pages that catalog Dolby Vision Profile 7 FEL physical "
    "media (4K UHD Blu-ray) releases. Reply with JSON only."
)
_DISCOVERY_USER = (
    "List forum threads, wikis, community spreadsheets, and curated lists that "
    "track confirmed Dolby Vision Profile 7 FEL Blu-ray releases. Return a JSON "
    "array of up to 25 direct URL strings and nothing else."
)


def _parse_url_list(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("urls") or data.get("items") or []
    if not isinstance(data, list):
        return []
    return [item.strip() for item in data if isinstance(item, str) and item.strip()]


def _candidate_to_release(candidate: FoundCandidate, collected_at: str) -> FelRelease:
    return FelRelease(
        movie_title=candidate.title,
        release_date=candidate.year or UNKNOWN,
        fel_evidence=FelEvidence(
            source_url=candidate.source_url,
            quote=candidate.evidence or candidate.label,
            evidence_type="ai-extracted",
        ),
        source_label="codex-ai",
        collected_at=collected_at,
    )


def ai_discover_sources(ai_client: AIClient, existing_urls: list[str]) -> list[str]:
    """Ask the AI for FEL source URLs; return well-formed, not-yet-known ones."""
    try:
        text = ai_client.complete(_DISCOVERY_SYSTEM, _DISCOVERY_USER)
    except httpx.HTTPError as exc:
        print(f"ai-scrape: source discovery failed: {exc}")
        return []
    known = set(existing_urls)
    discovered: list[str] = []
    for raw_url in _parse_url_list(text):
        parsed = urllib.parse.urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if raw_url not in known and raw_url not in discovered:
            discovered.append(raw_url)
    return discovered


def ai_extract_releases(
    ai_client: AIClient, pages: list[tuple[str, str]]
) -> list[FelRelease]:
    """Extract FEL releases from already-fetched (source_url, html) pages."""
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    releases: list[FelRelease] = []
    for source_url, text in pages:
        try:
            candidates = ai_client.extract_candidates(source_url, text)
        except httpx.HTTPError as exc:
            print(f"ai-scrape: extraction failed for {source_url}: {exc}")
            continue
        for candidate in candidates:
            if candidate.title:
                releases.append(_candidate_to_release(candidate, collected_at))
    return releases


def ai_scrape_releases(
    source_urls: list[str], cache_dir: Path, ai_client: AIClient
) -> list[FelRelease]:
    pages: list[tuple[str, str]] = []
    with fetcher.Fetcher(
        cache_dir=cache_dir,
        cookie_header=os.environ.get("FORUM_COOKIE_HEADER"),
    ) as html_fetcher:
        for source_url in source_urls:
            fetch_url = source_url
            if "docs.google.com/spreadsheets/" in source_url:
                fetch_url = google_sheets.google_sheet_csv_url(source_url)
            result = html_fetcher.fetch(fetch_url, raise_on_error=False)
            if result.error:
                continue
            pages.append((source_url, result.text))
    return ai_extract_releases(ai_client, pages)


def _google_sheets_path_for(source_path: Path) -> Path:
    if source_path == Path("data/forums.txt"):
        return Path("data/google_sheets.txt")
    return source_path.with_name("google_sheets.txt")


def _load_existing_releases(output_dir: Path) -> list[FelRelease]:
    """Load releases already published by the deterministic scrape, if any.

    In the daily pipeline the ``run`` command writes (and enriches) this file
    before ``ai-scrape`` runs, so AI-discovered releases must be *merged into*
    it rather than replacing it.
    """
    releases_path = output_dir / "data" / "releases.json"
    if not releases_path.exists():
        return []
    raw = json.loads(releases_path.read_text(encoding="utf-8"))
    return [release_from_dict(item) for item in raw]


def run_ai_scrape(source_path: Path, output_dir: Path, cache_dir: Path) -> int:
    import artifacts
    import main
    from merge import canonical_key, dedupe_releases

    try:
        settings = AISettings.from_env()
    except RuntimeError:
        print("ai-scrape skipped; OPENAI_API_KEY / CODEX_API_KEY is not configured")
        return 0

    forum_urls = sources.read_source_urls(source_path)
    sheet_urls = sources.read_source_urls(_google_sheets_path_for(source_path))
    existing_urls = list(dict.fromkeys([*forum_urls, *sheet_urls]))

    with AIClient(settings) as ai_client:
        discovered = ai_discover_sources(ai_client, existing_urls)
        if discovered:
            sources.merge_confirmed_sources(source_path, discovered)
        all_urls = list(dict.fromkeys([*existing_urls, *discovered]))
        ai_releases = ai_scrape_releases(all_urls, cache_dir, ai_client)

    # Enrich only the AI-discovered releases; entries already in releases.json
    # were enriched by the deterministic ``run`` step earlier in the pipeline.
    unique_ai = dedupe_releases(ai_releases, canonical_key)
    main._enrich_if_possible(unique_ai)

    # Merge into (never replace) the existing database before publishing.
    existing_releases = _load_existing_releases(output_dir)
    merged = dedupe_releases([*existing_releases, *unique_ai], canonical_key)
    sorted_releases = artifacts.publish_outputs(merged, output_dir=output_dir)
    print(
        "ai-scrape complete; "
        f"discovered_sources={len(discovered)} "
        f"ai_releases={len(ai_releases)} "
        f"existing_releases={len(existing_releases)} "
        f"releases={len(sorted_releases)}"
    )
    return 0
