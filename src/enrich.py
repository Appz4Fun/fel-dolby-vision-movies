from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING

import httpx

from tmdb import (
    MovieResolver,
    StaticTmdbResolver,
    TmdbResolver,
    load_tmdb_api_key,
)
from models import UNKNOWN, FelRelease

if TYPE_CHECKING:
    from bluray import BlurayMatcher


__all__ = [
    "MovieResolver",
    "StaticTmdbResolver",
    "TmdbResolver",
    "load_tmdb_api_key",
    "EnrichmentSummary",
    "enrich_releases",
    "release_url_for",
]

TMDB_DETAIL_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w185"
TMDB_MOVIE_PAGE = "https://www.themoviedb.org/movie/{tmdb_id}"
IMDB_TITLE_PAGE = "https://www.imdb.com/title/{imdb_id}/"
DEFAULT_POSTER_DIR = Path("data/posters")

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


@dataclass(frozen=True)
class EnrichmentSummary:
    total: int
    resolved: int
    unresolved: int
    posters_downloaded: int
    failed: int
    bluray_matched: int = 0
    bluray_failed: int = 0


def release_url_for(tmdb_id: str, imdb_id: str) -> str:
    if tmdb_id:
        return TMDB_MOVIE_PAGE.format(tmdb_id=tmdb_id)
    if imdb_id:
        return IMDB_TITLE_PAGE.format(imdb_id=imdb_id)
    return ""


def _release_year(value: str) -> str:
    match = _YEAR_RE.search(value or "")
    return match.group(0) if match else ""


def _get_with_retry(client: httpx.Client, url: str, **kwargs: object) -> httpx.Response:
    """GET `url`, retrying transient errors (timeouts, connection errors, 5xx)."""
    for attempt in range(_MAX_ATTEMPTS):
        last = attempt == _MAX_ATTEMPTS - 1
        try:
            response = client.get(url, **kwargs)
        except httpx.HTTPError:
            if last:
                raise
            time.sleep(1.0 * (attempt + 1))
            continue
        if response.status_code in _RETRY_STATUSES and not last:
            time.sleep(1.0 * (attempt + 1))
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("unreachable")  # pragma: no cover


def fetch_tmdb_details(
    client: httpx.Client, api_key: str, tmdb_id: str
) -> dict[str, str]:
    response = _get_with_retry(
        client, TMDB_DETAIL_URL.format(tmdb_id=tmdb_id), params={"api_key": api_key}
    )
    data = response.json()
    companies = data.get("production_companies") or []
    studio = ""
    if companies and companies[0].get("name"):
        studio = str(companies[0]["name"])
    return {
        "poster_path": str(data.get("poster_path") or ""),
        "studio": studio,
        "release_date": str(data.get("release_date") or ""),
    }


def download_poster(client: httpx.Client, poster_path: str, dest: Path) -> bool:
    if not poster_path or dest.exists():
        return False
    response = _get_with_retry(client, f"{TMDB_IMAGE_BASE}{poster_path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.content)
    return True


def enrich_releases(
    releases: list[FelRelease],
    resolver: MovieResolver,
    *,
    client: httpx.Client,
    api_key: str,
    poster_dir: Path | str = DEFAULT_POSTER_DIR,
    bluray_resolver: BlurayMatcher | None = None,
) -> EnrichmentSummary:
    poster_dir = Path(poster_dir)
    resolved = unresolved = downloaded = failed = bluray_matched = bluray_failed = 0
    for release in releases:
        movie = None
        resolve_failed = False
        try:
            movie = resolver.resolve(
                release.movie_title, _release_year(release.release_date)
            )
        except httpx.HTTPError as exc:
            resolve_failed = True
            unresolved += 1
            print(f"enrich: resolve failed for {release.movie_title!r}: {exc}")
        if movie is None and not resolve_failed:
            unresolved += 1
        else:
            resolved += 1
            release.tmdb_id = movie.tmdb_id
            release.imdb_id = movie.imdb_id
            release.release_url = release_url_for(movie.tmdb_id, movie.imdb_id)
            try:
                details = fetch_tmdb_details(client, api_key, movie.tmdb_id)
                if details["studio"] and release.studio in ("", UNKNOWN):
                    release.studio = details["studio"]
                if details["release_date"] and "-" not in release.release_date:
                    release.release_date = details["release_date"]
                if details["poster_path"]:
                    dest = poster_dir / f"{movie.tmdb_id}.jpg"
                    if download_poster(client, details["poster_path"], dest):
                        downloaded += 1
                    release.poster_path = str(dest)
            except httpx.HTTPError as exc:
                failed += 1
                print(
                    f"enrich: TMDB detail/poster failed for "
                    f"{release.movie_title!r} (tmdb {movie.tmdb_id}): {exc}"
                )
        if bluray_resolver is not None:
            try:
                details = bluray_resolver.resolve(
                    release.movie_title, _release_year(release.release_date)
                )
            except httpx.HTTPError as exc:
                bluray_failed += 1
                print(
                    f"enrich: blu-ray lookup failed for {release.movie_title!r}: {exc}"
                )
                details = None
            if details is not None:
                bluray_matched += 1
                release.bluray_url = details.url
                release.bluray_release_date = details.bluray_release_date
                release.hdr_formats = list(details.hdr_formats)
                release.audio_formats = list(details.audio_formats)
                release.audio_languages = list(details.audio_languages)
                if "English" in details.audio_languages:
                    release.english_audio = "Yes"
    return EnrichmentSummary(
        len(releases),
        resolved,
        unresolved,
        downloaded,
        failed,
        bluray_matched,
        bluray_failed,
    )
