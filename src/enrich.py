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
    TmdbMovie,
    TmdbResolver,
    load_tmdb_api_key,
)
from merge import (
    TMDB_ORIGINAL_TITLE_KEY,
    TMDB_TITLE_KEY,
    canonical_title_key,
    has_edition_descriptor,
)
from models import UNKNOWN, FelRelease
from normalize import normalize_fel_title

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
    # Native title resolves to a separate TMDB record from the canonical English
    # entry, so pin it to the English title shared by the canonical row.
    ("le grand bleu", "1988"): _LookupCandidate("The Big Blue", "1988"),
    # Source-list misspelling ("Notting Hilll"); map to the real title so it
    # enriches to the same id as the canonical row instead of duplicating it.
    ("notting hilll", "1999"): _LookupCandidate("Notting Hill", "1999"),
    # Some FEL list sources label "1917" by its home-video/rerelease year
    # (2020, matching the US 4K Blu-ray) rather than TMDB's 2019 theatrical
    # year. An unpinned year search misses the real film (whose TMDB primary
    # release year is 2019) and can instead match an unrelated same-titled
    # work that happens to have a 2020 release date (e.g. TMDB id 766967,
    # "2020: A 1917 Parody", an amateur short film) -- pin the search year so
    # it resolves to the real film and merges with the canonical row instead
    # of creating a duplicate.
    ("1917", "2020"): _LookupCandidate("1917", "2019"),
    # Reddit FEL list romanizations / sequel aliases pinned to the canonical
    # English titles so each row enriches to the same TMDB id as (and merges
    # with) the canonical catalog entry.
    ("train to busan 2", "2020"): _LookupCandidate("Peninsula", "2020"),
    # Reddit's "Obsession [2025]" labels the 2026-dated film by its festival
    # year; an unpinned 2025 search matches an unrelated same-titled French
    # film (TMDB 1502633) that happens to carry a 2025 release date, so pin
    # the search year the same way the "1917" home-video mislabel is pinned.
    ("obsession", "2025"): _LookupCandidate("Obsession", "2026"),
    ("ryu to sobakasu no hime", "2021"): _LookupCandidate("Belle", "2021"),
    ("long ma jing shen", "2023"): _LookupCandidate("Ride On", "2023"),
    ("rio 70", "1969"): _LookupCandidate("The Girl from Rio", "1969"),
}


_AKA_SPLIT_RE = re.compile(r"\bAKA\b", re.IGNORECASE)
_TRAILING_YEAR_RE = re.compile(r"[\[(]\s*(?:19|20)\d{2}\s*[\])].*$")


def _aka_titles_from_quote(quote: str) -> list[str]:
    """Pull the English alternate(s) out of a "Native AKA English [year]" quote.

    Reddit FEL lists frequently title foreign films by their romanized native
    name with the English title after "AKA"; ``normalize_fel_title`` keeps the
    left side, so the English alternate is offered here as a fallback resolution
    candidate (after the native title) to recover the canonical TMDB identity.
    """
    parts = _AKA_SPLIT_RE.split(quote or "")
    if len(parts) < 2:
        return []
    akas: list[str] = []
    for part in parts[1:]:
        cleaned = _TRAILING_YEAR_RE.sub("", part).split(",")[0]
        title = normalize_fel_title(cleaned)
        if title and title not in akas:
            akas.append(title)
    return akas


def _resolution_candidates(release: FelRelease, year: str) -> list[_LookupCandidate]:
    candidates = _lookup_candidates(release.movie_title, year)
    seen = {(candidate.title, candidate.year) for candidate in candidates}
    for aka in _aka_titles_from_quote(release.fel_evidence.quote):
        for candidate in _lookup_candidates(aka, year):
            key = (candidate.title, candidate.year)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
    return candidates


def _record_tmdb_title_pair(release: FelRelease, movie: TmdbMovie) -> None:
    """Record TMDB's canonical/original title pair for foreign-language films.

    Reconciliation treats the recorded pair as deterministic proof that a row
    titled by the film's original (native) title and a row titled by its
    canonical (English) title are the same film, so sources that never spell
    out a "Native AKA English" quote still collapse into one entry.
    """
    if not movie.original_title:
        return
    if canonical_title_key(movie.title) == canonical_title_key(movie.original_title):
        return
    release.additional_characteristics.setdefault(TMDB_TITLE_KEY, movie.title)
    release.additional_characteristics.setdefault(
        TMDB_ORIGINAL_TITLE_KEY, movie.original_title
    )


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
            for candidate in _resolution_candidates(release, release_year):
                if not candidate.year:
                    # Never guess a specific release for a yearless title; that
                    # would assert a one-to-one FEL correlation the evidence does
                    # not support (e.g. ambiguous "Halloween II" sheet rows).
                    continue
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
            elif movie.matched_alternative_title and not has_edition_descriptor(
                release.movie_title
            ):
                # The row is titled by a TMDB alternative title (romanized
                # native name, sequel alias); adopt the canonical TMDB title
                # so reconciliation can merge it with the canonical catalog
                # row. Edition-descriptor titles keep their source spelling:
                # TMDB lists edition names among alternative titles, and
                # renaming those would collapse a distinct physical edition
                # into the base film.
                release.movie_title = movie.title
            release.tmdb_id = movie.tmdb_id
            release.imdb_id = movie.imdb_id
            release.release_url = release_url_for(movie.tmdb_id, movie.imdb_id)
            _record_tmdb_title_pair(release, movie)
            try:
                details = fetch_tmdb_details(client, api_key, movie.tmdb_id)
                if details["studio"] and release.studio in ("", UNKNOWN):
                    release.studio = details["studio"]
                # Only adopt TMDB's full date when its year matches the year that
                # resolved the movie, so enrichment never drifts a row out of the
                # year its FEL evidence proves (e.g. a [2025] quote -> 2026 date).
                candidate_year = movie_candidate.year if movie_candidate else ""
                if (
                    details["release_date"]
                    and "-" not in release.release_date
                    and _release_year(details["release_date"]) == candidate_year
                ):
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
                for candidate in _resolution_candidates(
                    release, _release_year(release.release_date)
                ):
                    if not candidate.year:
                        continue
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
