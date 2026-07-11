import json
from pathlib import Path

import httpx
import pytest

from tmdb import (
    TmdbMovie,
    TmdbResolver,
    _best_tmdb_candidate,
    load_tmdb_api_key,
)


def test_best_tmdb_candidate_matches_original_title_when_display_title_differs():
    candidate = _best_tmdb_candidate(
        "Ajeossi",
        "2010",
        [
            {
                "id": 101,
                "title": "The Man from Nowhere",
                "original_title": "아저씨",
                "release_date": "2010-08-04",
            },
            {
                "id": 102,
                "title": "Ajeossi",
                "original_title": "Ajeossi",
                "release_date": "2005-01-01",
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 101


def test_best_tmdb_candidate_breaks_score_ties_by_popularity():
    candidate = _best_tmdb_candidate(
        "Resident Evil",
        "",
        [
            {
                "id": 1423191,
                "title": "Resident Evil",
                "original_title": "Resident Evil",
                "release_date": "2026-09-09",
                "popularity": 4.6,
            },
            {
                "id": 1576,
                "title": "Resident Evil",
                "original_title": "Resident Evil",
                "release_date": "2002-03-15",
                "popularity": 5.5,
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 1576


def test_best_tmdb_candidate_uses_popularity_for_equal_alias_scores():
    candidate = _best_tmdb_candidate(
        "Goksung",
        "2016",
        [
            {
                "id": 1413713,
                "title": "The Stranger",
                "original_title": "Goksung (The Wailing)",
                "release_date": "2016-09-23",
                "popularity": 0.16,
            },
            {
                "id": 293670,
                "title": "The Wailing",
                "original_title": "Goksung (The Wailing)",
                "release_date": "2016-05-12",
                "popularity": 12.7,
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 293670


def test_load_tmdb_api_key_reads_dotenv_without_printing_secret(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("TMDB_API_KEY=secret-tmdb-key\n", encoding="utf-8")

    assert load_tmdb_api_key(env_path) == "secret-tmdb-key"


def test_load_tmdb_api_key_requires_value_without_echoing_secret(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("TMDB_API_KEY=\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="TMDB_API_KEY"):
        load_tmdb_api_key(env_path)


def test_resolver_refetches_legacy_cache_records_missing_original_title(tmp_path):
    cache_path = tmp_path / "tmdb_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "Les rivières pourpres\x002000": {
                    "tmdb_id": "60670",
                    "title": "The Crimson Rivers",
                    "year": "2000",
                    "imdb_id": "tt0228786",
                },
                "Unknown Movie\x002099": None,
            }
        ),
        encoding="utf-8",
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 60670,
                            "title": "The Crimson Rivers",
                            "original_title": "Les rivières pourpres",
                            "release_date": "2000-09-27",
                        }
                    ]
                },
            )
        if request.url.path == "/3/movie/60670/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt0228786"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with TmdbResolver(
        "key", cache_path=cache_path, client=client, delay_seconds=0
    ) as resolver:
        movie = resolver.resolve("Les rivières pourpres", "2000")

    assert movie == TmdbMovie(
        tmdb_id="60670",
        title="The Crimson Rivers",
        year="2000",
        imdb_id="tt0228786",
        original_title="Les rivières pourpres",
    )
    assert requests, "legacy record without original_title must be re-fetched"

    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert (
        cached["Les rivières pourpres\x002000"]["original_title"]
        == "Les rivières pourpres"
    )
    assert cached["Unknown Movie\x002099"] is None

    requests.clear()
    with TmdbResolver(
        "key", cache_path=cache_path, client=client, delay_seconds=0
    ) as fresh:
        assert fresh.resolve("Les rivières pourpres", "2000") == movie
    assert requests == [], "rewritten record must be served from cache"
    client.close()
