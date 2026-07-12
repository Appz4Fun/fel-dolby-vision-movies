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
    """original_title must be checked even when TMDB's `title` is localized.

    Uses a candidate whose *original_title* (not display title) equals the
    query, isolating that mechanism from year-only disambiguation (see
    test_best_tmdb_candidate_never_wins_on_year_alone_with_zero_overlap for
    why a zero-title-overlap candidate must not win via year coincidence).
    """
    candidate = _best_tmdb_candidate(
        "Ajeossi",
        "2010",
        [
            {
                "id": 101,
                "title": "The Man from Nowhere",
                "original_title": "Ajeossi",
                "release_date": "2010-08-04",
                "vote_count": 500,
            },
            {
                "id": 102,
                "title": "Unrelated Movie",
                "original_title": "Unrelated Movie",
                "release_date": "2010-01-01",
                "vote_count": 10,
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


def test_best_tmdb_candidate_never_wins_on_year_alone_with_zero_overlap():
    """Regression test: 'Sisu' [2023] previously resolved to TMDB id 935906
    ('Scrapper'), a completely unrelated British film that shares zero title
    tokens with the query and won only because its release year happened to
    match. A candidate with no title relevance at all must never be
    confidently returned, no matter its year or vote count, because
    real-world evidence sometimes labels a film by a non-primary release
    year (festival year, home-video year) that legitimately differs from
    TMDB's primary_release_year -- rejecting here lets the resolver retry
    without the year constraint instead of confidently returning garbage.
    """
    candidate = _best_tmdb_candidate(
        "Sisu",
        "2023",
        [
            {
                "id": 935906,
                "title": "Scrapper",
                "release_date": "2023-08-25",
                "vote_count": 800,
            }
        ],
    )

    assert candidate is None


def test_best_tmdb_candidate_prefers_popular_exact_match_despite_year_mismatch():
    """Regression test: 'Hamilton' [2020] (Reddit list year) and '1917'
    [2020] (home-video year) both previously lost to obscure same-titled
    works ('The Rise of Lewis Hamilton' F1 documentary; '2020: A 1917
    Parody', an 18-minute short) purely because those obscurities' TMDB
    release years happened to match the query while the real, hugely more
    popular films' years did not. A well-known film with an exact title
    match and heavy real-world engagement must win over a weak-overlap,
    near-zero-engagement candidate even when only the weak candidate's year
    lines up.
    """
    candidate = _best_tmdb_candidate(
        "1917",
        "2020",
        [
            {
                "id": 530915,
                "title": "1917",
                "original_title": "1917",
                "release_date": "2019-12-25",
                "vote_count": 15000,
            },
            {
                "id": 766967,
                "title": "2020: A 1917 Parody",
                "original_title": "2020: A 1917 Parody",
                "release_date": "2020-10-03",
                "vote_count": 2,
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 530915


def test_best_tmdb_candidate_rejects_weak_overlap_as_sole_candidate():
    """A weak, coincidental title overlap must not clear the acceptance
    threshold on its own just by combining the year-match bonus with a
    handful of votes.

    Code review on the original fix (PR #43) found that when the real
    "1917" isn't even in the year-constrained search's result set -- the
    same failure mode already proven for "Sisu" -> "Scrapper" -- the
    parody alone scored 18 (weak overlap) + 45 (year match) + 6 (a couple
    of votes) = 69, clearing 65 and never triggering the fallback search
    that would have found the real film. A weak overlap must stay
    ineligible for the engagement bonus so this can't happen.
    """
    candidate = _best_tmdb_candidate(
        "1917",
        "2020",
        [
            {
                "id": 766967,
                "title": "2020: A 1917 Parody",
                "original_title": "2020: A 1917 Parody",
                "release_date": "2020-10-03",
                "vote_count": 2,
            }
        ],
    )

    assert candidate is None


def test_best_tmdb_candidate_penalizes_undated_duplicate_over_dated_match():
    """A correctly-dated exact-title match must not lose to a same-titled,
    undated duplicate purely because the duplicate has more votes.

    Code review flagged that a candidate with no parseable release_date
    pays no year penalty, so a high-vote undated duplicate (e.g. a
    placeholder/announcement TMDB entry) could still out-score a
    correctly-dated match that simply hasn't accumulated many votes yet.
    """
    candidate = _best_tmdb_candidate(
        "Some Movie",
        "2022",
        [
            {
                "id": 1,
                "title": "Some Movie",
                "original_title": "Some Movie",
                "release_date": "2022-05-01",
                "vote_count": 0,
            },
            {
                "id": 2,
                "title": "Some Movie",
                "original_title": "Some Movie",
                "release_date": "",
                "vote_count": 9000,
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 1


def test_best_tmdb_candidate_prefers_popular_film_over_obscure_same_title():
    """Regression test: a 'Showing Up [2022]' reddit-list mention previously
    resolved to TMDB id 256559, an unrelated 2014 documentary that happens
    to share the exact same title (both `title` and `original_title`).
    TMDB dates the real Kelly Reichardt film to its 2023 US release despite
    a 2022 Cannes premiere, so *both* candidates' years miss the query year
    -- year cannot disambiguate this collision at all; only real vote
    counts can.
    """
    candidate = _best_tmdb_candidate(
        "Showing Up",
        "2022",
        [
            {
                "id": 256559,
                "title": "Showing Up",
                "original_title": "Showing Up",
                "release_date": "2014-01-01",
                "vote_count": 4,
            },
            {
                "id": 790416,
                "title": "Showing Up",
                "original_title": "Showing Up",
                "release_date": "2023-04-07",
                "vote_count": 450,
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 790416


def test_best_tmdb_candidate_tolerates_non_numeric_vote_count():
    """A malformed vote_count (unexpected API shape) must not crash scoring
    or block an otherwise-clear match; it's simply treated as no votes."""
    candidate = _best_tmdb_candidate(
        "Sisu",
        "2023",
        [
            {
                "id": 840326,
                "title": "Sisu",
                "original_title": "Sisu",
                "release_date": "2023-08-25",
                "vote_count": "unknown",
            }
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 840326


def test_best_tmdb_candidate_tolerates_non_finite_vote_count():
    """`int(float("inf"))` raises OverflowError, not TypeError/ValueError --
    a malformed non-finite vote_count must not crash scoring either."""
    candidate = _best_tmdb_candidate(
        "Sisu",
        "2023",
        [
            {
                "id": 840326,
                "title": "Sisu",
                "original_title": "Sisu",
                "release_date": "2023-08-25",
                "vote_count": float("inf"),
            }
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 840326


def test_resolver_falls_back_past_a_zero_relevance_year_coincidence(
    monkeypatch, tmp_path: Path
):
    """End-to-end regression test for the 'Sisu' -> 'Scrapper' mismatch.

    The year-constrained search's only result ('Scrapper') shares nothing
    with the query but its release year, so it must be rejected outright;
    the resolver should then retry without the year constraint, where the
    real Sisu is unambiguously the best match. Asserts the actual request
    sequence (year-constrained, then unconstrained), not just the final
    result, so this can't pass for the wrong reason if a bug made the
    resolver skip straight to an unconstrained search.
    """
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)
    search_year_params: list[tuple[str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            search_year_params.append(
                (
                    request.url.params.get("year"),
                    request.url.params.get("primary_release_year"),
                )
            )
            if request.url.params.get("year") == "2023":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": 935906,
                                "title": "Scrapper",
                                "original_title": "Scrapper",
                                "release_date": "2023-08-25",
                                "vote_count": 800,
                                "poster_path": "/scrapper.jpg",
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 840326,
                            "title": "Sisu",
                            "original_title": "Sisu",
                            "release_date": "2022-09-09",
                            "vote_count": 1400,
                            "poster_path": "/sisu.jpg",
                        },
                        {
                            "id": 935906,
                            "title": "Scrapper",
                            "original_title": "Scrapper",
                            "release_date": "2023-08-25",
                            "vote_count": 800,
                            "poster_path": "/scrapper.jpg",
                        },
                    ]
                },
            )
        if request.url.path.endswith("/external_ids"):
            return httpx.Response(200, json={"imdb_id": "tt14846026"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Sisu", "2023")

    assert result is not None
    assert result.tmdb_id == "840326"
    assert search_year_params == [("2023", "2023"), (None, None)]


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


def test_resolver_refetches_cache_records_from_a_stale_scorer_version(tmp_path):
    """A positive cache record decided under an older _SCORER_VERSION must be
    re-fetched, not served from a persistent (e.g. self-hosted runner) disk
    cache forever.

    Regression test for the "Sisu" -> "Scrapper" mismatch specifically: a
    record with `original_title` already present (so the legacy-original-
    title check alone wouldn't catch it) but missing the current scorer
    version must still be treated as stale.
    """
    cache_path = tmp_path / "tmdb_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "Sisu\x002023": {
                    "tmdb_id": "935906",
                    "title": "Scrapper",
                    "year": "2023",
                    "imdb_id": "",
                    "original_title": "Scrapper",
                }
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
                            "id": 840326,
                            "title": "Sisu",
                            "original_title": "Sisu",
                            "release_date": "2023-08-25",
                            "vote_count": 1400,
                        }
                    ]
                },
            )
        if request.url.path == "/3/movie/840326/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt14846026"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with TmdbResolver(
        "key", cache_path=cache_path, client=client, delay_seconds=0
    ) as resolver:
        movie = resolver.resolve("Sisu", "2023")

    assert requests, "stale-scorer-version record must be re-fetched, not cached"
    assert movie is not None
    assert movie.tmdb_id == "840326"

    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["Sisu\x002023"]["scorer_version"] == tmdb._SCORER_VERSION
    client.close()
