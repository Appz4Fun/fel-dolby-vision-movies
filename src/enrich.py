from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import httpx

from fel_cleanup import (  # relocated into this module in a later task
    MovieResolver,
    StaticTmdbResolver,
    TmdbResolver,
    load_tmdb_api_key,
)
from models import UNKNOWN, FelRelease


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

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


@dataclass(frozen=True)
class EnrichmentSummary:
    total: int
    resolved: int
    unresolved: int
    posters_downloaded: int


def release_url_for(tmdb_id: str, imdb_id: str) -> str:
    if tmdb_id:
        return TMDB_MOVIE_PAGE.format(tmdb_id=tmdb_id)
    if imdb_id:
        return IMDB_TITLE_PAGE.format(imdb_id=imdb_id)
    return ""


def _release_year(value: str) -> str:
    match = _YEAR_RE.search(value or "")
    return match.group(0) if match else ""


def fetch_tmdb_details(
    client: httpx.Client, api_key: str, tmdb_id: str
) -> dict[str, str]:
    response = client.get(
        TMDB_DETAIL_URL.format(tmdb_id=tmdb_id), params={"api_key": api_key}
    )
    response.raise_for_status()
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
    response = client.get(f"{TMDB_IMAGE_BASE}{poster_path}")
    response.raise_for_status()
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
) -> EnrichmentSummary:
    poster_dir = Path(poster_dir)
    resolved = unresolved = downloaded = 0
    for release in releases:
        movie = resolver.resolve(release.movie_title, _release_year(release.release_date))
        if movie is None:
            unresolved += 1
            continue
        resolved += 1
        release.tmdb_id = movie.tmdb_id
        release.imdb_id = movie.imdb_id
        release.release_url = release_url_for(movie.tmdb_id, movie.imdb_id)
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
    return EnrichmentSummary(len(releases), resolved, unresolved, downloaded)
