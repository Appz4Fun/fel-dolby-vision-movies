import json
from pathlib import Path

import httpx
import pytest

import tmdb
from tmdb import (
    TmdbMovie,
    TmdbResolver,
    _best_tmdb_candidate,
    _has_audience_engagement,
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


def test_has_audience_engagement_rejects_zero_vote_posterless_candidate():
    assert (
        _has_audience_engagement({"id": 1, "vote_count": 0, "poster_path": None})
        is False
    )
    assert _has_audience_engagement({"id": 1}) is False


def test_has_audience_engagement_accepts_any_votes_or_poster():
    assert (
        _has_audience_engagement({"id": 1, "vote_count": 3, "poster_path": None})
        is True
    )
    assert (
        _has_audience_engagement(
            {"id": 1, "vote_count": 0, "poster_path": "/poster.jpg"}
        )
        is True
    )


def test_resolver_rejects_title_year_match_with_no_audience_engagement(
    monkeypatch, tmp_path: Path
):
    """Regression test for a title-only, imdb-less FEL sighting mismatching.

    A bare "Obsession [2025]" reddit-list mention previously resolved to
    TMDB id 1436161: an 18-minute, $1,000-budget short film with zero votes
    and no poster that happens to share both the exact title and release
    year of the query. Such a candidate is far too obscure to plausibly be
    the subject of a Dolby Vision FEL Blu-ray release, so the resolver must
    not confidently match it just because the text and year line up.
    """
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            if request.url.params.get("year") == "2025":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": 1436161,
                                "title": "Obsession",
                                "original_title": "Obsession",
                                "release_date": "2025-03-28",
                                "popularity": 0.6,
                                "vote_count": 0,
                                "poster_path": None,
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Obsession", "2025")

    assert result is None


def test_resolver_accepts_title_year_match_with_at_least_one_vote(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 42,
                            "title": "Obsession",
                            "original_title": "Obsession",
                            "release_date": "2025-03-28",
                            "popularity": 1.2,
                            "vote_count": 3,
                            "poster_path": None,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/external_ids"):
            return httpx.Response(200, json={"imdb_id": "tt9999999"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Obsession", "2025")

    assert result is not None
    assert result.tmdb_id == "42"
    assert result.imdb_id == "tt9999999"


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
                            "vote_count": 154,
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
