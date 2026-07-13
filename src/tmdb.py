from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import re
import time
import unicodedata
from typing import Any, Protocol

import httpx


TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_TV_SEARCH_URL = "https://api.themoviedb.org/3/search/tv"
TMDB_EXTERNAL_IDS_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}/external_ids"
TMDB_TV_EXTERNAL_IDS_URL = "https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids"
TMDB_ALT_TITLES_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}/alternative_titles"
DEFAULT_CACHE_PATH = Path(".cache/tmdb_fel_cleanup.json")


@dataclass(frozen=True)
class TmdbMovie:
    tmdb_id: str
    title: str
    year: str
    imdb_id: str = ""
    original_title: str = ""
    # Set when resolution succeeded only via the alternative-title rescue:
    # the TMDB alternative title that matched the query. Enrichment uses it
    # to retitle the source row to the canonical TMDB title so the row can
    # merge with the canonical catalog entry.
    matched_alternative_title: str = ""
    # "movie" or "tv". TMDB movie and TV ids are separate namespaces, so
    # enrichment must know which one an id belongs to before building page
    # URLs or calling detail endpoints.
    media_type: str = "movie"

    @classmethod
    def from_dict(cls, record: dict[str, str]) -> TmdbMovie:
        return cls(
            tmdb_id=record["tmdb_id"],
            title=record["title"],
            year=record["year"],
            imdb_id=record.get("imdb_id", ""),
            original_title=record.get("original_title", ""),
            matched_alternative_title=record.get("matched_alternative_title", ""),
            media_type=record.get("media_type", "movie"),
        )


class MovieResolver(Protocol):
    def resolve(self, title: str, year: str) -> TmdbMovie | None: ...


class StaticTmdbResolver:
    def __init__(self, records: dict[tuple[str, str], dict[str, str]]) -> None:
        self.records = records

    def resolve(self, title: str, year: str) -> TmdbMovie | None:
        record = self.records.get((title, year))
        if record is None:
            return None
        return TmdbMovie.from_dict(record)


class TmdbResolver:  # pragma: no cover - exercised via live TMDB calls only
    def __init__(
        self,
        api_key: str,
        cache_path: Path = DEFAULT_CACHE_PATH,
        client: httpx.Client | None = None,
        delay_seconds: float = 0.025,
    ) -> None:
        self.api_key = api_key
        self.cache_path = cache_path
        self.client = client or httpx.Client(timeout=httpx.Timeout(20.0))
        self._owns_client = client is None
        self.delay_seconds = delay_seconds
        self.cache: dict[str, dict[str, str] | None] = self._read_cache()

    def resolve(self, title: str, year: str) -> TmdbMovie | None:
        cache_key = f"{title}\0{year}"
        if cache_key in self.cache:
            return _movie_from_cache_record(self.cache[cache_key])

        result = self._search(title, year)
        self.cache[cache_key] = _movie_to_cache_record(result)
        self._write_cache()
        time.sleep(self.delay_seconds)
        return result

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> TmdbResolver:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _search(self, title: str, year: str) -> TmdbMovie | None:
        candidates = self._fetch_candidates(title, year)
        best = _best_tmdb_candidate(title, year, candidates)
        if best is None and year:
            fallback = self._fetch_candidates(title, "")
            best = _best_tmdb_candidate(title, "", fallback)
            candidates = list(
                {str(c.get("id", "")): c for c in candidates + fallback}.values()
            )
        matched_alternative_title = ""
        if best is None:
            rescue = self._rescue_by_alternative_title(title, year, candidates)
            if rescue is not None:
                best, matched_alternative_title = rescue
        if best is None:
            return self._search_tv(title, year)
        tmdb_id = str(best.get("id", ""))
        external = self._external_ids(tmdb_id)
        return TmdbMovie(
            tmdb_id=tmdb_id,
            title=str(best.get("title") or best.get("name") or title).strip(),
            year=_year_from_date(str(best.get("release_date") or "")) or year,
            imdb_id=str(external.get("imdb_id") or ""),
            original_title=str(best.get("original_title") or "").strip(),
            matched_alternative_title=matched_alternative_title,
        )

    def _fetch_candidates(self, title: str, year: str) -> list[dict[str, Any]]:
        params = {
            "api_key": self.api_key,
            "query": title,
            "include_adult": "false",
        }
        if re.fullmatch(r"(?:19|20)\d{2}", year):
            params["year"] = year
            params["primary_release_year"] = year
        response = self.client.get(TMDB_SEARCH_URL, params=params)
        response.raise_for_status()
        candidates = response.json().get("results", [])
        return [c for c in candidates if _has_audience_engagement(c)]

    def _rescue_by_alternative_title(
        self, title: str, year: str, candidates: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], str] | None:
        """Confirm low-scoring candidates against TMDB's alternative titles."""
        # Search finds films by alternative titles (romanized native names,
        # sequel aliases), but the scorer only sees `title`/`original_title`,
        # so a query like "Ryu to sobakasu no hime" rejects the very candidate
        # ("Belle") the search index matched for it. When nothing scores, an
        # exact alternative-title hit on a near-year, engaged candidate is
        # accepted before giving up; the matched title is returned alongside
        # the candidate so enrichment can retitle the source row canonically.
        query_key = _canonical_title_key(title)
        for candidate in _alternative_title_rescue_order(year, candidates):
            response = self.client.get(
                TMDB_ALT_TITLES_URL.format(tmdb_id=str(candidate.get("id", ""))),
                params={"api_key": self.api_key},
            )
            response.raise_for_status()
            records = response.json().get("titles", [])
            matched = _matching_alternative_title(query_key, records)
            if matched:
                return candidate, matched
        return None

    def _search_tv(self, title: str, year: str) -> TmdbMovie | None:
        """Resolve a TV-season title against /3/search/tv as a last resort."""
        # Reddit FEL lists carry TV season discs ("Ahsoka: The Complete
        # First Season") that movie search can never match. Only titles
        # bearing a season descriptor take this path, and only after every
        # movie-side fallback failed, so a movie title can never drift onto
        # a same-named series. The search is deliberately not pinned with
        # first_air_date_year: a season disc's year is the season's, not the
        # series premiere's (Mandalorian S3 is 2023, first_air_date 2019),
        # so pinning would exclude the right series from the result set.
        # Only a *first*-season disc's year approximates a premiere year, so
        # only first seasons keep the year in scoring, where the year-match
        # bonus disambiguates same-named original/reboot series pairs. For
        # later seasons the row year says nothing about any premiere -- a
        # premiere-year coincidence must not hand the disc to a same-named
        # reboot over the far-more-engaged right series, so they score
        # yearless and engagement decides.
        series_title = _series_title_from_season_descriptor(title)
        if not series_title:
            return None
        candidates = self._fetch_tv_candidates(series_title)
        query_year = year if _is_first_season_title(title) else ""
        best = _best_tmdb_candidate(series_title, query_year, candidates)
        if best is None:
            return None
        return self._tv_movie_from_candidate(best, series_title, year)

    def _fetch_tv_candidates(self, series_title: str) -> list[dict[str, Any]]:
        response = self.client.get(
            TMDB_TV_SEARCH_URL,
            params={
                "api_key": self.api_key,
                "query": series_title,
                "include_adult": "false",
            },
        )
        response.raise_for_status()
        candidates = [
            _tv_candidate_as_movie(candidate)
            for candidate in response.json().get("results", [])
        ]
        return [c for c in candidates if _has_audience_engagement(c)]

    def _tv_movie_from_candidate(
        self, best: dict[str, Any], series_title: str, year: str
    ) -> TmdbMovie:
        tmdb_id = str(best.get("id", ""))
        external = self._external_ids(tmdb_id, TMDB_TV_EXTERNAL_IDS_URL)
        return TmdbMovie(
            tmdb_id=tmdb_id,
            title=str(best.get("title") or series_title).strip(),
            year=_year_from_date(str(best.get("release_date") or "")) or year,
            imdb_id=str(external.get("imdb_id") or ""),
            original_title=str(best.get("original_title") or "").strip(),
            media_type="tv",
        )

    def _external_ids(
        self, tmdb_id: str, url_template: str = TMDB_EXTERNAL_IDS_URL
    ) -> dict[str, Any]:
        if not tmdb_id:
            return {}
        response = self.client.get(
            url_template.format(tmdb_id=tmdb_id),
            params={"api_key": self.api_key},
        )
        response.raise_for_status()
        return dict(response.json())

    def _read_cache(self) -> dict[str, dict[str, str] | None]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): value if isinstance(value, dict) else None
            for key, value in data.items()
            if not _is_legacy_cache_record(value)
        }

    def _write_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self.cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


# TV season discs on the reddit lists name the series plus a season
# descriptor ("Ahsoka: The Complete First Season", "Andor: Season 2"). The
# descriptor is stripped before querying /3/search/tv; a title that is
# nothing but a descriptor leaves no series name worth searching for.
_SEASON_DESCRIPTOR_RE = re.compile(
    r"\s*[:\-–—]?\s*"
    r"(?:the\s+complete\s+(?P<ordinal>\w+)\s+season|season\s+(?P<number>\d+))\s*$",
    re.IGNORECASE,
)

_FIRST_SEASON_TOKENS = frozenset({"first", "1st", "1"})


def _series_title_from_season_descriptor(title: str) -> str:
    """Series name with the trailing season descriptor removed, or ""."""
    if not _SEASON_DESCRIPTOR_RE.search(title or ""):
        return ""
    return _SEASON_DESCRIPTOR_RE.sub("", title).strip()


def _is_first_season_title(title: str) -> bool:
    """True only when the season descriptor names the first season."""
    match = _SEASON_DESCRIPTOR_RE.search(title or "")
    if match is None:
        return False
    token = (match.group("ordinal") or match.group("number") or "").casefold()
    return token in _FIRST_SEASON_TOKENS


def _tv_candidate_as_movie(candidate: dict[str, Any]) -> dict[str, Any]:
    """Map a /3/search/tv result onto the movie-shaped keys the scorer reads."""
    mapped = dict(candidate)
    mapped["title"] = candidate.get("name") or ""
    mapped["original_title"] = candidate.get("original_name") or ""
    mapped["release_date"] = candidate.get("first_air_date") or ""
    return mapped


# Bumped whenever _best_tmdb_candidate's match weights change materially
# or a new resolution capability ships (alternative-title rescue, TV-season
# fallback): season-title negatives cached before /3/search/tv was consulted
# must be re-fetched, not served stale forever from persistent runner caches.
# A positive cache record written under an older version was a decision
# made with different (possibly incorrect) weights -- e.g. self-hosted
# runners persist .cache/tmdb_fel_cleanup.json across workflow runs, so
# without this a match this exact commit fixes (like "Sisu" resolving to
# the unrelated "Scrapper") would keep being served from cache forever
# instead of being re-scored under the corrected logic.
_SCORER_VERSION = "4"


def _is_legacy_cache_record(value: object) -> bool:
    """Report whether a cache record predates the current scorer version."""
    # A record decided under different match weights (or before a resolution
    # feature like the alternative-title rescue existed) is dropped at load
    # time and re-fetched on demand rather than being served stale until the
    # cache file is deleted. That includes negatives: a bare-None "no match"
    # record (the pre-versioning negative format) may name a title the
    # current rescue can now resolve, so only negatives version-stamped by
    # the current scorer (dicts without a tmdb_id) stay valid.
    return not isinstance(value, dict) or value.get("scorer_version") != _SCORER_VERSION


def load_tmdb_api_key(env_path: Path = Path(".env")) -> str:
    file_value: str | None = None
    try:
        from dotenv import dotenv_values, load_dotenv

        if env_path.exists():
            values = dotenv_values(env_path)
            if "TMDB_API_KEY" in values:
                file_value = values["TMDB_API_KEY"] or ""
        load_dotenv(env_path)
    except Exception:  # pragma: no cover - dotenv import/parse failures
        pass
    api_key = (
        file_value if file_value is not None else os.environ.get("TMDB_API_KEY", "")
    ).strip()
    if not api_key:
        raise RuntimeError("TMDB_API_KEY is required to resolve TMDB metadata")
    return api_key


# A same-titled TMDB entry can share a query's release year by pure
# coincidence (many titles -- "Sisu", "1917", "Hamilton" -- collide with
# obscure shorts, documentaries, or parodies). At the original +80/-90
# weights, that year coincidence alone was enough to clear the acceptance
# threshold even with zero title relevance, or to outrank a real film whose
# source-reported year legitimately differs from TMDB's primary release
# year (festival vs. wide release, theatrical vs. home-video). Keeping the
# bonus/penalty modest means year only ever refines among plausible title
# matches -- see _engagement_bonus for how real-world popularity, not year,
# now does the heavy lifting for disambiguating same-titled collisions.
# The bonus additionally requires at least one real vote: a zero-vote
# poster-only entry ("Obsession" 2025, a phantom same-titled record) must
# not beat a heavily-voted film on year coincidence alone. A single year
# of drift is scored as neutral rather than a mismatch, because
# festival-vs-wide-release and theatrical-vs-home-video drift is routine
# in the FEL source lists.
_YEAR_MATCH_BONUS = 45
_YEAR_MISMATCH_PENALTY = -45
# A candidate with no parseable release date at all is unconfirmed in a way
# no real vote pile can repair (films with genuine audiences have dates), so
# the penalty must exceed the maximum engagement bonus (50). Otherwise an
# undated duplicate/placeholder TMDB entry could out-rank the correctly-dated
# real film purely by accumulating votes, now that a zero-vote dated match no
# longer earns the year bonus to defend itself.
_YEAR_MISSING_PENALTY = -55

# Only a candidate whose title is a strong, plausible match for the query
# can earn credit for real-world popularity. A weak, coincidental overlap
# (e.g. "1917" matching one of four tokens in "2020: A 1917 Parody") must
# not be able to combine a year-coincidence bonus with even a handful of
# votes to clear the acceptance threshold on its own -- that reintroduces
# the exact false-positive class this scorer exists to prevent.
_ENGAGEMENT_ELIGIBILITY_THRESHOLD = 70


def _best_tmdb_candidate(
    query_title: str, query_year: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    best: tuple[int, float, dict[str, Any]] | None = None
    query_key = _canonical_title_key(query_title)
    for candidate in candidates:
        title = str(candidate.get("title") or candidate.get("name") or "")
        original_title = str(candidate.get("original_title") or "")
        release_year = _year_from_date(str(candidate.get("release_date") or ""))
        title_key = _canonical_title_key(title)
        original_key = _canonical_title_key(original_title)
        title_score = max(
            _title_score(query_key, title_key), _title_score(query_key, original_key)
        )
        score = title_score
        if query_year and release_year == query_year:
            if _vote_count(candidate) > 0:
                score += _YEAR_MATCH_BONUS
        elif query_year and release_year:
            if abs(int(release_year) - int(query_year)) > 1:
                score += _YEAR_MISMATCH_PENALTY
        elif query_year and not release_year:
            score += _YEAR_MISSING_PENALTY
        if title_key == query_key:
            score += 20
        if original_key == query_key:
            score += 20
        if title_score >= _ENGAGEMENT_ELIGIBILITY_THRESHOLD:
            score += _engagement_bonus(candidate)
        popularity = _candidate_popularity(candidate)
        if best is None or (score, popularity) > (best[0], best[1]):
            best = (score, popularity, candidate)
    if best is None or best[0] < 65:
        return None  # pragma: no cover - low-score branch
    return best[2]


def _candidate_popularity(candidate: dict[str, Any]) -> float:
    value = candidate.get("popularity")
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _vote_count(candidate: dict[str, Any]) -> int:
    value = candidate.get("vote_count")
    try:
        return int(value) if value else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _engagement_bonus(candidate: dict[str, Any]) -> int:
    """Score bonus from real vote counts, log-scaled and capped at 50.

    A title with thousands of votes is overwhelmingly more likely to be the
    release audiences actually mean than a same-titled entry with a
    handful, so this weights real engagement into the score directly
    instead of only using it (via _candidate_popularity) as a last-resort
    tiebreaker between otherwise-equal scores.
    """
    vote_count = _vote_count(candidate)
    if vote_count <= 0:
        return 0
    return min(50, round(12 * math.log10(vote_count + 1)))


def _has_audience_engagement(candidate: dict[str, Any]) -> bool:
    """True if anyone has ever voted on or TMDB has poster art for this title.

    TMDB's catalog includes a long tail of amateur/student shorts and other
    never-released-to-market entries that can still win an exact title+year
    text match purely by coincidence (many films share generic titles like
    "Obsession" across decades). A candidate with zero votes and no poster
    is far more likely to be one of those than a movie that actually
    reached a physical/Blu-ray home-video market, so require at least one
    of those signals before it is eligible to be scored as a match.
    """
    return bool(candidate.get("vote_count")) or bool(candidate.get("poster_path"))


# Each rescue lookup costs one API call, so only the few most-engaged
# plausible candidates are confirmed before giving up.
_ALT_TITLE_RESCUE_LIMIT = 3


def _alternative_title_rescue_order(
    query_year: str, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Candidates worth confirming against /alternative_titles, best first."""
    # Only candidates whose known year sits within one year of the query
    # survive -- the rescue exists for exact alternative-title hits on the
    # film the source actually listed, and a far-off year marks a same-titled
    # stranger. Ranking by vote count checks the real film before obscure
    # hangers-on.
    eligible = []
    for candidate in candidates:
        release_year = _year_from_date(str(candidate.get("release_date") or ""))
        if query_year and release_year and abs(int(release_year) - int(query_year)) > 1:
            continue
        eligible.append(candidate)
    # Dated candidates outrank undated ones regardless of votes, so a
    # high-vote undated placeholder cannot bypass the missing-date penalty
    # by winning the rescue instead.
    eligible.sort(
        key=lambda candidate: (
            bool(_year_from_date(str(candidate.get("release_date") or ""))),
            _vote_count(candidate),
        ),
        reverse=True,
    )
    return eligible[:_ALT_TITLE_RESCUE_LIMIT]


def _matching_alternative_title(query_key: str, records: list[dict[str, Any]]) -> str:
    if not query_key:
        return ""
    for record in records:
        title = str(record.get("title") or "")
        if _canonical_title_key(title) == query_key:
            return title
    return ""


def _title_score(left: str, right: str) -> int:
    if not left or not right:
        return 0
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
    ordered = 1.0 if left == right else 0.0
    return round((overlap * 70) + (ordered * 30))


def _canonical_title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = normalized.casefold().replace("&", " and ")
    normalized = re.sub(r"[’`´]", "'", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return _normalize_space(normalized)


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _year_from_date(value: str) -> str:
    match = re.match(r"((?:19|20)\d{2})", value)
    return match.group(1) if match else ""


def _movie_from_cache_record(
    record: dict[str, str] | None,
) -> TmdbMovie | None:  # pragma: no cover
    if record is None or not record.get("tmdb_id"):
        return None
    return TmdbMovie.from_dict(record)


def _movie_to_cache_record(
    movie: TmdbMovie | None,
) -> dict[str, str]:  # pragma: no cover
    if movie is None:
        # Version-stamped negative: refetched when the scorer version bumps,
        # unlike the legacy bare-None format which cached "no match" forever.
        return {"scorer_version": _SCORER_VERSION}
    return {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "imdb_id": movie.imdb_id,
        "original_title": movie.original_title,
        "matched_alternative_title": movie.matched_alternative_title,
        "media_type": movie.media_type,
        "scorer_version": _SCORER_VERSION,
    }
