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
                "vote_count": 40,
                "popularity": 0.16,
            },
            {
                "id": 293670,
                "title": "The Wailing",
                "original_title": "Goksung (The Wailing)",
                "release_date": "2016-05-12",
                "vote_count": 40,
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


def test_best_tmdb_candidate_prefers_voted_film_over_zero_vote_year_coincidence():
    """Regression test: 'Obsession' [2025] (Reddit list year) matched an
    obscure zero-vote same-titled entry whose TMDB year happened to equal
    the query year, instead of the real film whose primary release drifted
    to 2026 (festival premiere vs. wide release). A year coincidence only
    counts for candidates with real votes, so engagement decides here."""
    candidate = _best_tmdb_candidate(
        "Obsession",
        "2025",
        [
            {
                "id": 1502633,
                "title": "Obsession",
                "original_title": "Obsession",
                "release_date": "2025-03-13",
                "vote_count": 0,
                "poster_path": "/phantom.jpg",
            },
            {
                "id": 1339713,
                "title": "Obsession",
                "original_title": "Obsession",
                "release_date": "2026-05-15",
                "vote_count": 300,
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 1339713


def test_best_tmdb_candidate_treats_one_year_drift_as_neutral():
    """A single year of drift between the source-reported year and TMDB's
    primary release year is routine (festival vs. wide release, theatrical
    vs. home video), so it must not be penalized like a real mismatch."""
    candidate = _best_tmdb_candidate(
        "The Premiere",
        "2021",
        [
            {
                "id": 1,
                "title": "The Premiere",
                "original_title": "The Premiere",
                "release_date": "2022-02-01",
                "vote_count": 5,
            },
            {
                "id": 2,
                "title": "The Premiere",
                "original_title": "The Premiere",
                "release_date": "2024-06-01",
                "vote_count": 3000,
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 1


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


def test_alternative_title_rescue_order_filters_far_years_and_ranks_by_votes():
    near_small = {"id": 1, "release_date": "2021-07-16", "vote_count": 10}
    near_big = {"id": 2, "release_date": "2022-01-01", "vote_count": 500}
    far = {"id": 3, "release_date": "1990-01-01", "vote_count": 9000}
    undated = {"id": 4, "release_date": "", "vote_count": 50}

    order = tmdb._alternative_title_rescue_order(
        "2021", [near_small, near_big, far, undated]
    )

    # The far-year stranger is never checked, and any correctly-dated
    # candidate outranks an undated one regardless of votes, so a high-vote
    # undated placeholder cannot bypass the missing-date penalty via rescue.
    assert [candidate["id"] for candidate in order] == [2, 1, 4]


def test_alternative_title_rescue_order_keeps_all_years_for_yearless_query():
    far = {"id": 3, "release_date": "1990-01-01", "vote_count": 9000}

    order = tmdb._alternative_title_rescue_order("", [far])

    assert [candidate["id"] for candidate in order] == [3]


def test_matching_alternative_title_normalizes_diacritics():
    records = [
        {"iso_3166_1": "JP", "title": "Ryū to Sobakasu no Hime", "type": "romaji"},
        {"iso_3166_1": "US", "title": "The Dragon and the Freckled Princess"},
        {"title": None},
    ]

    assert (
        tmdb._matching_alternative_title("ryu to sobakasu no hime", records)
        == "Ryū to Sobakasu no Hime"
    )
    assert tmdb._matching_alternative_title("something else", records) == ""
    assert tmdb._matching_alternative_title("", records) == ""


def test_resolver_rescues_romanized_query_via_alternative_titles(
    monkeypatch, tmp_path: Path
):
    """A romanized native-title query ('Ryu to sobakasu no hime') finds the
    right film through TMDB's search index, which matches alternative titles
    -- but the scorer only sees the English display title ('Belle') and the
    CJK original title, so every candidate it was handed scores below
    acceptance. The resolver must then confirm near-year, engaged candidates
    against /alternative_titles instead of giving up, and must not spend a
    lookup on the far-year same-titled stranger."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)
    alt_title_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 99,
                            "title": "Belle",
                            "original_title": "Belle",
                            "release_date": "1990-01-01",
                            "vote_count": 5000,
                            "poster_path": "/other.jpg",
                        },
                        {
                            "id": 776503,
                            "title": "Belle",
                            "original_title": "竜とそばかすの姫",
                            "release_date": "2021-07-16",
                            "vote_count": 1200,
                            "poster_path": "/belle.jpg",
                        },
                    ]
                },
            )
        if request.url.path.endswith("/alternative_titles"):
            alt_title_requests.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "titles": [
                        {
                            "iso_3166_1": "JP",
                            "title": "Ryū to Sobakasu no Hime",
                            "type": "romaji",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/external_ids"):
            return httpx.Response(200, json={"imdb_id": "tt13651628"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Ryu to sobakasu no hime", "2021")

    assert result is not None
    assert result.tmdb_id == "776503"
    assert result.title == "Belle"
    assert result.imdb_id == "tt13651628"
    assert result.matched_alternative_title == "Ryū to Sobakasu no Hime"
    assert alt_title_requests == ["/3/movie/776503/alternative_titles"]

    # The matched alternative title must survive the cache round-trip so
    # enrichment can retitle the row deterministically on cached replays.
    cached = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert (
        cached["Ryu to sobakasu no hime\x002021"]["matched_alternative_title"]
        == "Ryū to Sobakasu no Hime"
    )


def test_resolver_returns_none_when_alternative_titles_do_not_match(
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
                            "title": "Unrelated",
                            "original_title": "Unrelated",
                            "release_date": "2021-05-01",
                            "vote_count": 80,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/alternative_titles"):
            return httpx.Response(200, json={"titles": [{"title": "Still Unrelated"}]})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    assert resolver.resolve("Long ma jing shen", "2021") is None
    # Negative decisions are version-stamped so a future scorer/rescue change
    # re-fetches them instead of serving a stale "no match" forever.
    cached = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert cached["Long ma jing shen\x002021"] == {
        "scorer_version": tmdb._SCORER_VERSION
    }


def test_resolver_refetches_bare_none_negative_cache_records(tmp_path):
    """A pre-versioning negative record (bare null) must be re-fetched: the
    'no match' decision may have been made before the alternative-title
    rescue existed, so on persistent runner caches it would otherwise keep
    a now-resolvable title unresolved forever. A negative decided under the
    current version is still served from cache."""
    cache_path = tmp_path / "tmdb_cache.json"
    cache_path.write_text(
        json.dumps({"Long ma jing shen\x002023": None}), encoding="utf-8"
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with TmdbResolver(
        "key", cache_path=cache_path, client=client, delay_seconds=0
    ) as resolver:
        assert resolver.resolve("Long ma jing shen", "2023") is None
    assert requests, "bare-None negative must be re-fetched, not served stale"

    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["Long ma jing shen\x002023"] == {
        "scorer_version": tmdb._SCORER_VERSION
    }

    requests.clear()
    with TmdbResolver(
        "key", cache_path=cache_path, client=client, delay_seconds=0
    ) as fresh:
        assert fresh.resolve("Long ma jing shen", "2023") is None
    assert requests == [], "current-version negative must be served from cache"
    client.close()


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


def test_series_title_from_season_descriptor_strips_season_suffixes():
    assert (
        tmdb._series_title_from_season_descriptor("Ahsoka: The Complete First Season")
        == "Ahsoka"
    )
    assert (
        tmdb._series_title_from_season_descriptor(
            "The Mandalorian: The Complete Third Season"
        )
        == "The Mandalorian"
    )
    assert (
        tmdb._series_title_from_season_descriptor(
            "Game of Thrones: The Complete Eighth Season"
        )
        == "Game of Thrones"
    )
    assert tmdb._series_title_from_season_descriptor("Andor: Season 2") == "Andor"
    # Compact labels, a form merge.py already treats as an edition
    # descriptor, must reach the TV fallback too.
    assert (
        tmdb._series_title_from_season_descriptor("Game of Thrones S01")
        == "Game of Thrones"
    )
    assert tmdb._series_title_from_season_descriptor("Andor: S2") == "Andor"


def test_series_title_from_season_descriptor_rejects_non_season_titles():
    assert tmdb._series_title_from_season_descriptor("Oppenheimer") == ""
    # A bare descriptor leaves no series name worth searching for.
    assert tmdb._series_title_from_season_descriptor("Season 5") == ""
    assert tmdb._series_title_from_season_descriptor("") == ""
    # An embedded season-like substring is not a descriptor.
    assert tmdb._series_title_from_season_descriptor("Marvels01") == ""


def test_is_first_season_title_detects_only_first_seasons():
    assert tmdb._is_first_season_title("Ahsoka: The Complete First Season") is True
    assert tmdb._is_first_season_title("Andor: Season 1") is True
    assert tmdb._is_first_season_title("Game of Thrones S01") is True
    assert (
        tmdb._is_first_season_title("The Mandalorian: The Complete Third Season")
        is False
    )
    assert tmdb._is_first_season_title("Andor: Season 2") is False
    assert tmdb._is_first_season_title("Game of Thrones S02") is False
    assert tmdb._is_first_season_title("Oppenheimer") is False


def test_tv_candidate_as_movie_maps_tv_fields_onto_movie_keys():
    mapped = tmdb._tv_candidate_as_movie(
        {
            "id": 114461,
            "name": "Ahsoka",
            "original_name": "Ahsoka",
            "first_air_date": "2023-08-22",
            "vote_count": 1200,
        }
    )

    assert mapped["id"] == 114461
    assert mapped["title"] == "Ahsoka"
    assert mapped["original_title"] == "Ahsoka"
    assert mapped["release_date"] == "2023-08-22"


def test_resolver_resolves_season_titles_via_tv_search(monkeypatch, tmp_path: Path):
    """A reddit-list TV season row ("Ahsoka: The Complete First Season") is a
    series disc by construction, so the resolver must query /3/search/tv
    with the season descriptor stripped, take IMDb ids from
    /3/tv/{id}/external_ids, and mark the result as TV so enrichment can
    emit a /tv/ release URL."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)
    tv_queries: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/3/search/tv":
            tv_queries.append(request.url.params.get("query"))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 114461,
                            "name": "Ahsoka",
                            "original_name": "Ahsoka",
                            "first_air_date": "2023-08-22",
                            "vote_count": 1200,
                            "poster_path": "/ahsoka.jpg",
                        }
                    ]
                },
            )
        if request.url.path == "/3/tv/114461/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt13622776"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Ahsoka: The Complete First Season", "2023")

    assert result is not None
    assert result.tmdb_id == "114461"
    assert result.title == "Ahsoka"
    assert result.year == "2023"
    assert result.imdb_id == "tt13622776"
    assert result.media_type == "tv"
    assert tv_queries == ["Ahsoka"]

    # media_type must survive the cache round-trip so cached replays still
    # enrich the row with a /tv/ release URL instead of a movie page.
    cached = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    record = cached["Ahsoka: The Complete First Season\x002023"]
    assert record["media_type"] == "tv"


def test_resolver_tv_fallback_tolerates_season_vs_premiere_year_drift(
    monkeypatch, tmp_path: Path
):
    """'The Mandalorian: The Complete Third Season' [2023] must resolve to a
    series whose first_air_date is 2019: a season disc's year is the
    season's, not the premiere's. The TV search must therefore never pin
    first_air_date_year (that would exclude the right series from the result
    set entirely), and the scorer's year-drift penalty must be absorbable by
    an exact-name, well-engaged match."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/3/search/tv":
            if request.url.params.get("first_air_date_year"):
                return httpx.Response(200, json={"results": []})
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 82856,
                            "name": "The Mandalorian",
                            "original_name": "The Mandalorian",
                            "first_air_date": "2019-11-12",
                            "vote_count": 5000,
                            "poster_path": "/mando.jpg",
                        }
                    ]
                },
            )
        if request.url.path == "/3/tv/82856/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt8111088"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("The Mandalorian: The Complete Third Season", "2023")

    assert result is not None
    assert result.tmdb_id == "82856"
    assert result.media_type == "tv"


def test_resolver_tv_fallback_prefers_year_matching_series_on_name_collision(
    monkeypatch, tmp_path: Path
):
    """Two same-named series (an original and its reboot) can only be told
    apart by year, so the TV fallback must keep scoring with the query year
    instead of dropping it: for a first-season disc the disc year matches
    the reboot's premiere, and ignoring year would let the older, more-voted
    original win on engagement alone."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/3/search/tv":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 1,
                            "name": "Reboot Show",
                            "original_name": "Reboot Show",
                            "first_air_date": "1959-10-02",
                            "vote_count": 2000,
                            "poster_path": "/original.jpg",
                        },
                        {
                            "id": 2,
                            "name": "Reboot Show",
                            "original_name": "Reboot Show",
                            "first_air_date": "2019-04-01",
                            "vote_count": 500,
                            "poster_path": "/reboot.jpg",
                        },
                    ]
                },
            )
        if request.url.path == "/3/tv/2/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt2583620"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Reboot Show: The Complete First Season", "2019")

    assert result is not None
    assert result.tmdb_id == "2"


def test_resolver_consults_tv_before_weak_movie_hits_for_season_titles(
    monkeypatch, tmp_path: Path
):
    """A short season title ("Andor: Season 2") shares its base token with a
    same-named film, and weak overlap (23) plus the year-match bonus (45)
    clears the movie acceptance threshold (65) -- so if TV were only a
    fallback, the movie path would steal the row and never consult
    /3/search/tv. Season-descriptor titles must resolve against TV first."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 901,
                            "title": "Andor",
                            "original_title": "Andor",
                            "release_date": "2022-06-01",
                            "vote_count": 120,
                            "poster_path": "/film.jpg",
                        }
                    ]
                },
            )
        if request.url.path == "/3/search/tv":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 83867,
                            "name": "Andor",
                            "original_name": "Andor",
                            "first_air_date": "2022-09-21",
                            "vote_count": 900,
                            "poster_path": "/andor.jpg",
                        }
                    ]
                },
            )
        if request.url.path == "/3/tv/83867/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt9253284"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Andor: Season 2", "2022")

    assert result is not None
    assert result.tmdb_id == "83867"
    assert result.media_type == "tv"
    assert "/3/search/movie" not in paths


def test_resolver_tv_fallback_ignores_year_for_later_season_titles(
    monkeypatch, tmp_path: Path
):
    """For a later-season disc the row year says nothing about any series
    premiere ("Season 5" of a 1959 original airs decades after 1959), so a
    premiere-year coincidence must not hand the disc to a same-named reboot
    over the far-more-engaged original: later seasons score yearless and let
    engagement decide, exactly like every other popularity-decided collision."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/3/search/tv":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 1,
                            "name": "Reboot Show",
                            "original_name": "Reboot Show",
                            "first_air_date": "1959-10-02",
                            "vote_count": 2000,
                            "poster_path": "/original.jpg",
                        },
                        {
                            "id": 2,
                            "name": "Reboot Show",
                            "original_name": "Reboot Show",
                            "first_air_date": "2019-04-01",
                            "vote_count": 500,
                            "poster_path": "/reboot.jpg",
                        },
                    ]
                },
            )
        if request.url.path == "/3/tv/1/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt0052520"})
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    result = resolver.resolve("Reboot Show: The Complete Fifth Season", "2019")

    assert result is not None
    assert result.tmdb_id == "1"


def test_resolver_does_not_tv_search_titles_without_season_descriptor(
    monkeypatch, tmp_path: Path
):
    """The TV fallback exists only for season-disc rows; an unresolvable
    movie title must keep resolving to None without spending a /3/search/tv
    request that could mismatch a same-named series."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    assert resolver.resolve("Some Unreleased Film", "2023") is None
    assert "/3/search/tv" not in paths


def test_resolver_tv_fallback_rejects_unengaged_same_named_series(
    monkeypatch, tmp_path: Path
):
    """A zero-vote, posterless TV entry is subject to the same audience-
    engagement gate as movie candidates; a season title must stay unresolved
    rather than match TV catalog noise."""
    monkeypatch.setattr(tmdb.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/3/search/tv":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 7,
                            "name": "Ahsoka",
                            "original_name": "Ahsoka",
                            "first_air_date": "2023-08-22",
                            "vote_count": 0,
                            "poster_path": None,
                        }
                    ]
                },
            )
        return httpx.Response(404)  # pragma: no cover - unreached in this test

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = TmdbResolver(
        api_key="x", cache_path=tmp_path / "cache.json", client=client
    )

    assert resolver.resolve("Ahsoka: The Complete First Season", "2023") is None


def test_resolver_refetches_pre_tv_fallback_negative_cache_records(tmp_path):
    """A season-title negative decided before the TV fallback existed must be
    re-fetched, not served forever from a persistent (self-hosted runner)
    cache: the title is resolvable now that /3/search/tv is consulted."""
    cache_path = tmp_path / "tmdb_cache.json"
    cache_path.write_text(
        json.dumps(
            {"Ahsoka: The Complete First Season\x002023": {"scorer_version": "3"}}
        ),
        encoding="utf-8",
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/3/search/tv":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 114461,
                            "name": "Ahsoka",
                            "original_name": "Ahsoka",
                            "first_air_date": "2023-08-22",
                            "vote_count": 1200,
                            "poster_path": "/ahsoka.jpg",
                        }
                    ]
                },
            )
        if request.url.path == "/3/tv/114461/external_ids":
            return httpx.Response(200, json={"imdb_id": "tt13622776"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with TmdbResolver(
        "key", cache_path=cache_path, client=client, delay_seconds=0
    ) as resolver:
        movie = resolver.resolve("Ahsoka: The Complete First Season", "2023")

    assert requests, "pre-TV-fallback negative must be re-fetched, not served stale"
    assert movie is not None
    assert movie.tmdb_id == "114461"
    assert movie.media_type == "tv"
    client.close()


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
    # Bare-None negatives predate version stamping; they are dropped at load
    # so titles the newer scorer/rescue could now resolve get re-fetched.
    assert "Unknown Movie\x002099" not in cached

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
