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


@dataclass(frozen=True)
class _LookupCandidate:
    title: str
    year: str
    canonical_title: str = ""


_LOOKUP_ALIASES: dict[tuple[str, str], _LookupCandidate] = {
    ("bug", "2006"): _LookupCandidate("Bug", "2007", "Bug"),
    ("burning sea", ""): _LookupCandidate("The Burning Sea", "2021"),
    ("burning sea", "2021"): _LookupCandidate("The Burning Sea", "2021"),
    ("chou tin dik tong wah", "1987"): _LookupCandidate("An Autumn's Tale", "1987"),
    ("goksung", "2016"): _LookupCandidate("The Wailing", "2016"),
    ("halloween", "2022"): _LookupCandidate("Halloween Ends", "2022"),
    ("hellboy iii rise of the blood queen", "2019"): _LookupCandidate(
        "Hellboy", "2019"
    ),
    ("long men ke zhen", "1967"): _LookupCandidate("Dragon Inn", "1967"),
    ("pat garrett and billy the kid 50th anniversary cut", "1973"): (
        _LookupCandidate("Pat Garrett & Billy the Kid", "1973")
    ),
    ("resident evil", ""): _LookupCandidate("Resident Evil", "2002"),
    ("the captain", "2017"): _LookupCandidate("Der Hauptmann", "2017"),
    ("the last emperor criterion collection", "1987"): _LookupCandidate(
        "The Last Emperor", "1987"
    ),
    ("the vvitch", "2015"): _LookupCandidate("The Witch", "2016"),
    ("van wilder", ""): _LookupCandidate("National Lampoon's Van Wilder", "2002"),
    ("van wilder", "2002"): _LookupCandidate("National Lampoon's Van Wilder", "2002"),
    ("xia nu", "1971"): _LookupCandidate("A Touch of Zen", "1970"),
    ("yip man", "2008"): _LookupCandidate("Ip Man", "2008"),
}


def release_url_for(tmdb_id: str, imdb_id: str) -> str:
    if tmdb_id:
        return TMDB_MOVIE_PAGE.format(tmdb_id=tmdb_id)
    if imdb_id:
        return IMDB_TITLE_PAGE.format(imdb_id=imdb_id)
    return ""


def _release_year(value: str) -> str:
    match = _YEAR_RE.search(value or "")
    return match.group(0) if match else ""


def _lookup_key(title: str, year: str) -> tuple[str, str]:
    normalized = title.casefold().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized, year


def _lookup_candidates(title: str, year: str) -> list[_LookupCandidate]:
    original = _LookupCandidate(title, year)
    alias = _LOOKUP_ALIASES.get(_lookup_key(title, year))
    candidates = [candidate for candidate in (alias, original) if candidate is not None]
    unique: list[_LookupCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate.title, candidate.year)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _get_with_retry(client: httpx.Client, url: str, **kwargs: object) -> httpx.Response:
    """GET `url`, retrying transient errors (timeouts, connection errors, 5xx)."""
    for attempt in range(_MAX_ATTEMPTS):
        last = attempt == _MAX_ATTEMPTS - 1
        try:
            response = client.get(url, **kwargs)
        except httpx.HTTPError:  # pragma: no cover - network retry path
            if last:
                raise
            time.sleep(1.0 * (attempt + 1))
            continue
        if (
            response.status_code in _RETRY_STATUSES and not last
        ):  # pragma: no cover - 5xx retry path
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
        return False  # pragma: no cover - skip when poster already cached
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
        movie_candidate: _LookupCandidate | None = None
        resolve_failed = False
        release_year = _release_year(release.release_date)
        try:
            for candidate in _lookup_candidates(release.movie_title, release_year):
                movie = resolver.resolve(candidate.title, candidate.year)
                if movie is not None:
                    movie_candidate = candidate
                    break
        except httpx.HTTPError as exc:  # pragma: no cover - TMDB resolve failure path
            resolve_failed = True
            unresolved += 1
            print(f"enrich: resolve failed for {release.movie_title!r}: {exc}")
        if movie is None:
            if not resolve_failed:
                unresolved += 1
        else:
            resolved += 1
            if (
                movie_candidate is not None
                and (movie_candidate.canonical_title or movie_candidate.title)
                != release.movie_title
            ):
                release.movie_title = movie_candidate.canonical_title or movie.title
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
                details = None
                for candidate in _lookup_candidates(
                    release.movie_title, _release_year(release.release_date)
                ):
                    details = bluray_resolver.resolve(candidate.title, candidate.year)
                    if details is not None:
                        break
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
                # FEL-confirmed releases are Dolby Vision Profile 7 discs by
                # definition. blu-ray.com sometimes lists only the base HDR10
                # layer; preserve "Dolby Vision" when fel_confirmed is true so
                # the bluray scrape can't silently downgrade validated FEL data.
                hdr = list(details.hdr_formats)
                if release.fel_confirmed and not any(
                    h.lower() == "dolby vision" for h in hdr
                ):
                    hdr = [
                        "Dolby Vision"
                    ] + hdr  # pragma: no cover - DV-preserve safeguard
                release.hdr_formats = hdr
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
