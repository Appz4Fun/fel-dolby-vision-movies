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
import time
import urllib.parse

import httpx

from compare import AIClient, AISettings, FoundCandidate, validate_ai_candidates
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
AI_EXTRACTION_ATTEMPTS = 3
AI_EXTRACTION_RETRY_BASE_DELAY_SECONDS = 1.0
AI_EXTRACTION_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


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
            continue  # pragma: no cover - malformed AI-discovered URL skip
        if raw_url not in known and raw_url not in discovered:
            discovered.append(raw_url)
    return discovered


def ai_extract_releases(
    ai_client: AIClient, pages: list[tuple[str, str]]
) -> list[FelRelease]:
    """Extract FEL releases from already-fetched (source_url, html) pages."""
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    releases: list[FelRelease] = []
    rejection_reasons: list[str] = []
    for source_url, text in pages:
        try:
            candidates = validate_ai_candidates(_extract_candidates_with_retries(ai_client, source_url, text), text, rejection_reasons)
        except httpx.HTTPError as exc:
            print(f"ai-scrape: extraction failed for {source_url}: {exc}")
            continue
        for candidate in candidates:
            if candidate.title and candidate.evidence:
                releases.append(_candidate_to_release(candidate, collected_at))
    if rejection_reasons:
        counts = {reason: rejection_reasons.count(reason) for reason in sorted(set(rejection_reasons))}
        print("ai-scrape rejected candidates: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return releases


def _extract_candidates_with_retries(
    ai_client: AIClient, source_url: str, text: str
) -> list[FoundCandidate]:
    last_error: httpx.HTTPError | None = None
    for attempt in range(AI_EXTRACTION_ATTEMPTS):
        try:
            return ai_client.extract_candidates(source_url, text)
        except httpx.HTTPError as exc:
            last_error = exc
            if not _is_retryable_extraction_error(exc):
                raise
            if attempt < AI_EXTRACTION_ATTEMPTS - 1:
                time.sleep(AI_EXTRACTION_RETRY_BASE_DELAY_SECONDS * (2**attempt))
    assert last_error is not None
    raise last_error


def _is_retryable_extraction_error(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in AI_EXTRACTION_RETRYABLE_STATUS_CODES
    return True


def ai_scrape_releases(
    source_urls: list[str], cache_dir: Path, ai_client: AIClient
) -> list[FelRelease]:
    pages: list[tuple[str, str]] = []
    with fetcher.Fetcher(
        cache_dir=cache_dir,
        cookie_header=os.environ.get("FORUM_COOKIE_HEADER"),
    ) as html_fetcher:
        for source_url in source_urls:
            fetch_url = _fetch_url_for_ai_source(source_url)
            result = html_fetcher.fetch(fetch_url, raise_on_error=False)
            if result.error:
                print(f"ai-scrape: fetch failed for {source_url}: {result.error}")
                continue
            pages.append((source_url, result.text))
    return ai_extract_releases(ai_client, pages)


def _fetch_url_for_ai_source(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    path_parts = [part for part in parsed.path.split("/") if part]
    is_standard_sheet_path = len(path_parts) >= 3 and path_parts[:2] == [
        "spreadsheets",
        "d",
    ]
    is_session_scoped_sheet_path = (
        len(path_parts) >= 5
        and path_parts[:2] == ["spreadsheets", "u"]
        and path_parts[2].isdigit()
        and path_parts[3] == "d"
    )
    if (
        hostname == "docs.google.com"
        or (hostname is not None and hostname.endswith(".docs.google.com"))
    ) and (is_standard_sheet_path or is_session_scoped_sheet_path):
        return google_sheets.google_sheet_csv_url(url)
    return url


def _is_google_doc_url(url: str) -> bool:
    hostname = urllib.parse.urlparse(url).hostname
    return hostname == "docs.google.com" or (
        hostname is not None and hostname.endswith(".docs.google.com")
    )


def _always_fel_path_for(source_path: Path) -> Path:
    return source_path.with_name("sources_always_fel.txt")


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


def run_ai_scrape(
    source_path: Path, output_dir: Path, cache_dir: Path, review_output_path: Path | None = None
) -> int:  # pragma: no cover - CLI entrypoint, requires live AI
    import artifacts
    import main

    try:
        settings = AISettings.from_env()
    except RuntimeError:
        if review_output_path is not None:
            import artifacts
            artifacts.write_empty_review_output(review_output_path)
        print("ai-scrape skipped; OPENAI_API_KEY / CODEX_API_KEY is not configured")
        return 0

    needs_evidence_urls = sources.read_source_urls(source_path)
    always_fel_urls = sources.read_source_urls(_always_fel_path_for(source_path))
    existing_urls = list(dict.fromkeys([*needs_evidence_urls, *always_fel_urls]))

    with AIClient(settings) as ai_client:
        discovered = ai_discover_sources(ai_client, existing_urls)
        if discovered:
            sources.merge_confirmed_sources(source_path, discovered)
        all_urls = list(dict.fromkeys([*existing_urls, *discovered]))
        ai_releases = ai_scrape_releases(all_urls, cache_dir, ai_client)

    # Enrich only the AI-discovered releases; entries already in releases.json
    # were enriched by the deterministic ``run`` step earlier in the pipeline.
    # Shared artifact reconciliation owns identity and deduplication.
    main._enrich_if_possible(ai_releases)
    existing_releases = _load_existing_releases(output_dir)
    sorted_releases = artifacts.publish_outputs(
        ai_releases, output_dir=output_dir, review_output_path=review_output_path
    )
    print(
        "ai-scrape complete; "
        f"discovered_sources={len(discovered)} "
        f"ai_releases={len(ai_releases)} "
        f"existing_releases={len(existing_releases)} "
        f"releases={len(sorted_releases)}"
    )
    return 0
