from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import time
import unicodedata
from typing import Any, Protocol

import httpx


TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_EXTERNAL_IDS_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}/external_ids"
DEFAULT_CACHE_PATH = Path(".cache/tmdb_fel_cleanup.json")


@dataclass(frozen=True)
class TmdbMovie:
    tmdb_id: str
    title: str
    year: str
    imdb_id: str = ""
    original_title: str = ""


class MovieResolver(Protocol):
    def resolve(self, title: str, year: str) -> TmdbMovie | None: ...


class StaticTmdbResolver:
    def __init__(self, records: dict[tuple[str, str], dict[str, str]]) -> None:
        self.records = records

    def resolve(self, title: str, year: str) -> TmdbMovie | None:
        record = self.records.get((title, year))
        if record is None:
            return None
        return TmdbMovie(
            tmdb_id=record["tmdb_id"],
            title=record["title"],
            year=record["year"],
            imdb_id=record.get("imdb_id", ""),
            original_title=record.get("original_title", ""),
        )


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
        best = self._search_candidates(title, year)
        if best is None and year:
            best = self._search_candidates(title, "")
        if best is None:
            return None
        tmdb_id = str(best.get("id", ""))
        external = self._external_ids(tmdb_id)
        return TmdbMovie(
            tmdb_id=tmdb_id,
            title=str(best.get("title") or best.get("name") or title).strip(),
            year=_year_from_date(str(best.get("release_date") or "")) or year,
            imdb_id=str(external.get("imdb_id") or ""),
            original_title=str(best.get("original_title") or "").strip(),
        )

    def _search_candidates(self, title: str, year: str) -> dict[str, Any] | None:
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
        return _best_tmdb_candidate(title, year, candidates)

    def _external_ids(self, tmdb_id: str) -> dict[str, Any]:
        if not tmdb_id:
            return {}
        response = self.client.get(
            TMDB_EXTERNAL_IDS_URL.format(tmdb_id=tmdb_id),
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
        }

    def _write_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self.cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


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
        score = max(
            _title_score(query_key, title_key), _title_score(query_key, original_key)
        )
        if query_year and release_year == query_year:
            score += 80
        elif query_year and release_year:
            score -= 90
        if title_key == query_key:
            score += 20
        if original_key == query_key:
            score += 20
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
    if record is None:
        return None
    return TmdbMovie(
        tmdb_id=record["tmdb_id"],
        title=record["title"],
        year=record["year"],
        imdb_id=record.get("imdb_id", ""),
        original_title=record.get("original_title", ""),
    )


def _movie_to_cache_record(
    movie: TmdbMovie | None,
) -> dict[str, str] | None:  # pragma: no cover
    if movie is None:
        return None
    return {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "imdb_id": movie.imdb_id,
        "original_title": movie.original_title,
    }
