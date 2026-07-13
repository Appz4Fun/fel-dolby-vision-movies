from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import trakt_sync


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url=trakt_sync.TRAKT_BASE_URL
    )


def test_refresh_access_token_returns_tokens_and_rotates_refresh():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 7776000,
                "token_type": "Bearer",
            },
        )

    with _mock_client(handler) as client:
        tokens = trakt_sync.refresh_access_token(
            http=client,
            client_id="cid",
            client_secret="csecret",
            refresh_token="old-refresh",
        )

    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "rotated-refresh"
    assert captured["url"].endswith("/oauth/token")
    assert captured["body"] == {
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
        "client_id": "cid",
        "client_secret": "csecret",
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
    }


def test_refresh_access_token_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktAuthError):
            trakt_sync.refresh_access_token(
                http=client,
                client_id="cid",
                client_secret="csecret",
                refresh_token="old-refresh",
            )


def test_fetch_list_imdb_ids_returns_set_of_imdb_ids():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers["Authorization"]
        captured["api_key"] = request.headers["trakt-api-key"]
        captured["api_version"] = request.headers["trakt-api-version"]
        return httpx.Response(
            200,
            json=[
                {"movie": {"ids": {"trakt": 1, "imdb": "tt0001", "tmdb": 100}}},
                {"movie": {"ids": {"trakt": 2, "imdb": "tt0002", "tmdb": 200}}},
                {"movie": {"ids": {"trakt": 3, "imdb": None, "tmdb": 300}}},
            ],
        )

    with _mock_client(handler) as client:
        ids = trakt_sync.fetch_list_imdb_ids(
            http=client,
            user="yellowbrick242",
            slug="xbmc4lyfe-fel-content",
            access_token="atok",
            client_id="cid",
        )

    assert ids == {"tt0001", "tt0002"}
    assert (
        captured["path"]
        == "/users/yellowbrick242/lists/xbmc4lyfe-fel-content/items/movies"
    )
    assert captured["auth"] == "Bearer atok"
    assert captured["api_key"] == "cid"
    assert captured["api_version"] == "2"


def test_fetch_list_imdb_ids_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktError):
            trakt_sync.fetch_list_imdb_ids(
                http=client,
                user="x",
                slug="y",
                access_token="atok",
                client_id="cid",
            )


def test_extract_imdb_ids_keeps_valid_skips_invalid():
    releases = [
        {"movie_title": "Good", "imdb_id": "tt0001"},
        {"movie_title": "Numeric", "imdb_id": "0002"},  # missing tt prefix → skip
        {"movie_title": "Empty", "imdb_id": ""},  # empty → skip
        {"movie_title": "None", "imdb_id": None},  # None → skip
        {"movie_title": "Also good", "imdb_id": "tt12345"},
    ]

    valid, skipped = trakt_sync.extract_imdb_ids(releases)

    assert valid == ["tt0001", "tt12345"]
    assert skipped == ["Numeric", "Empty", "None"]


def test_compute_diff_returns_sorted_add_and_remove_lists():
    current = {"tt0001", "tt0002", "tt0099"}
    desired = {"tt0002", "tt0003", "tt0004"}

    to_add, to_remove = trakt_sync.compute_diff(current=current, desired=desired)

    assert to_add == ["tt0003", "tt0004"]
    assert to_remove == ["tt0001", "tt0099"]


def test_add_items_batches_in_chunks_of_500_and_posts_correct_payload():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"added": {"movies": 0}})

    ids = [f"tt{i:07d}" for i in range(1200)]

    with _mock_client(handler) as client:
        trakt_sync.add_items(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
            imdb_ids=ids,
        )

    assert len(requests) == 3
    bodies = [json.loads(r.content) for r in requests]
    assert all(r.url.path == "/users/u/lists/s/items" for r in requests)
    assert len(bodies[0]["movies"]) == 500
    assert len(bodies[1]["movies"]) == 500
    assert len(bodies[2]["movies"]) == 200
    assert bodies[0]["movies"][0] == {"ids": {"imdb": "tt0000000"}}


def test_remove_items_posts_to_remove_endpoint():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"deleted": {"movies": 0}})

    with _mock_client(handler) as client:
        trakt_sync.remove_items(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
            imdb_ids=["tt0001", "tt0002"],
        )

    assert len(captured) == 1
    assert captured[0].url.path == "/users/u/lists/s/items/remove"
    assert json.loads(captured[0].content) == {
        "movies": [{"ids": {"imdb": "tt0001"}}, {"ids": {"imdb": "tt0002"}}]
    }


def test_add_items_with_empty_list_makes_no_requests():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={})

    with _mock_client(handler) as client:
        trakt_sync.add_items(
            http=client,
            user="u",
            slug="s",
            access_token="a",
            client_id="c",
            imdb_ids=[],
        )

    assert requests == []


def test_add_items_raises_trakt_error_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "unprocessable"})

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktError):
            trakt_sync.add_items(
                http=client,
                user="u",
                slug="s",
                access_token="atok",
                client_id="cid",
                imdb_ids=["tt0001"],
            )


def _build_handler(
    *,
    list_imdb_ids: list[str],
    new_refresh: str = "rotated",
    record: list[httpx.Request] | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "atok",
                    "refresh_token": new_refresh,
                    "expires_in": 7776000,
                    "token_type": "Bearer",
                },
            )
        if request.url.path.endswith("/items/movies") and request.method == "GET":
            return httpx.Response(
                200,
                json=[{"movie": {"ids": {"imdb": i}}} for i in list_imdb_ids],
            )
        if request.url.path.endswith("/items") and request.method == "POST":
            return httpx.Response(201, json={"added": {"movies": 0}})
        if request.url.path.endswith("/items/remove") and request.method == "POST":
            return httpx.Response(200, json={"deleted": {"movies": 0}})
        return httpx.Response(404)

    return handler


def test_run_sync_adds_removes_and_writes_rotated_token(tmp_path: Path):
    releases = tmp_path / "releases.json"
    releases.write_text(
        json.dumps(
            [
                {"movie_title": "A", "imdb_id": "tt0001"},
                {"movie_title": "B", "imdb_id": "tt0002"},
                {"movie_title": "C", "imdb_id": "tt0003"},
            ]
        )
    )
    token_out = tmp_path / "new-refresh"
    requests: list[httpx.Request] = []
    handler = _build_handler(list_imdb_ids=["tt0002", "tt0099"], record=requests)

    with _mock_client(handler) as client:
        summary = trakt_sync.run_sync(
            http=client,
            client_id="cid",
            client_secret="csec",
            refresh_token="old",
            user="u",
            slug="s",
            releases_path=releases,
            refresh_token_out=token_out,
            dry_run=False,
            allow_empty=False,
        )

    assert summary.added == 2  # tt0001, tt0003
    assert summary.removed == 1  # tt0099
    assert summary.unchanged == 1  # tt0002
    assert summary.skipped == []
    assert token_out.read_text() == "rotated"

    methods_paths = [(r.method, r.url.path) for r in requests]
    assert ("POST", "/oauth/token") in methods_paths
    assert ("GET", "/users/u/lists/s/items/movies") in methods_paths
    assert ("POST", "/users/u/lists/s/items") in methods_paths
    assert ("POST", "/users/u/lists/s/items/remove") in methods_paths


def test_run_sync_dry_run_makes_no_mutating_calls(tmp_path: Path):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([{"movie_title": "A", "imdb_id": "tt0001"}]))
    requests: list[httpx.Request] = []
    handler = _build_handler(list_imdb_ids=[], record=requests)

    with _mock_client(handler) as client:
        summary = trakt_sync.run_sync(
            http=client,
            client_id="cid",
            client_secret="csec",
            refresh_token="old",
            user="u",
            slug="s",
            releases_path=releases,
            refresh_token_out=None,
            dry_run=True,
            allow_empty=False,
        )

    assert summary.added == 1
    assert summary.removed == 0
    mutating = [
        r for r in requests if r.method == "POST" and r.url.path != "/oauth/token"
    ]
    assert mutating == []


def test_run_sync_refuses_empty_releases_without_allow_empty(tmp_path: Path):
    releases = tmp_path / "releases.json"
    releases.write_text("[]")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "atok",
                "refresh_token": "r",
                "expires_in": 1,
                "token_type": "Bearer",
            },
        )

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktError, match="empty"):
            trakt_sync.run_sync(
                http=client,
                client_id="cid",
                client_secret="csec",
                refresh_token="old",
                user="u",
                slug="s",
                releases_path=releases,
                refresh_token_out=None,
                dry_run=False,
                allow_empty=False,
            )


def test_run_sync_does_not_write_token_when_out_is_none(tmp_path: Path):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([{"movie_title": "A", "imdb_id": "tt0001"}]))
    handler = _build_handler(list_imdb_ids=["tt0001"])

    with _mock_client(handler) as client:
        trakt_sync.run_sync(
            http=client,
            client_id="cid",
            client_secret="csec",
            refresh_token="old",
            user="u",
            slug="s",
            releases_path=releases,
            refresh_token_out=None,
            dry_run=False,
            allow_empty=False,
        )
    # Only the releases file should be present — no token file written.
    assert [p.name for p in sorted(tmp_path.iterdir())] == ["releases.json"]


def test_run_sync_persists_rotated_token_even_when_mutations_fail(tmp_path: Path):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([{"movie_title": "A", "imdb_id": "tt0001"}]))
    token_out = tmp_path / "new-refresh"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "atok",
                    "refresh_token": "FRESH",
                    "expires_in": 1,
                    "token_type": "Bearer",
                },
            )
        if request.url.path.endswith("/items/movies") and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/items") and request.method == "POST":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(404)

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktError):
            trakt_sync.run_sync(
                http=client,
                client_id="cid",
                client_secret="csec",
                refresh_token="old",
                user="u",
                slug="s",
                releases_path=releases,
                refresh_token_out=token_out,
                dry_run=False,
                allow_empty=False,
            )

    # Token must be persisted even though mutations failed — otherwise the
    # workflow can't rotate the now-invalidated old refresh token.
    assert token_out.read_text() == "FRESH"


# ---------- 429 rate-limit handling ----------


def test_refresh_access_token_raises_rate_limit_with_retry_after_from_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text=(
                "REFRESH_TOKEN_API_GET_LIMIT rate limit exceeded. "
                "Please wait 2489 seconds then retry your request."
            ),
        )

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktRateLimitError) as exc:
            trakt_sync.refresh_access_token(
                http=client,
                client_id="cid",
                client_secret="csec",
                refresh_token="r",
            )

    assert exc.value.retry_after_seconds == 2489
    assert "rate limit" in str(exc.value).lower()


def test_refresh_access_token_raises_rate_limit_with_retry_after_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"}, text="too many")

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktRateLimitError) as exc:
            trakt_sync.refresh_access_token(
                http=client,
                client_id="cid",
                client_secret="csec",
                refresh_token="r",
            )

    assert exc.value.retry_after_seconds == 60


def test_refresh_access_token_rate_limit_without_parseable_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktRateLimitError) as exc:
            trakt_sync.refresh_access_token(
                http=client,
                client_id="cid",
                client_secret="csec",
                refresh_token="r",
            )

    assert exc.value.retry_after_seconds is None


def test_trakt_rate_limit_error_is_a_trakt_error():
    assert issubclass(trakt_sync.TraktRateLimitError, trakt_sync.TraktError)


def test_post_movies_retries_once_on_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="slow down")
        return httpx.Response(201, json={"added": {"movies": 1}})

    with _mock_client(handler) as client:
        trakt_sync.add_items(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
            imdb_ids=["tt0001"],
        )

    assert call_count["n"] == 2
    assert sleeps == [2.0]


def test_post_movies_raises_rate_limit_after_second_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"}, text="still limited")

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktRateLimitError) as exc:
            trakt_sync.add_items(
                http=client,
                user="u",
                slug="s",
                access_token="atok",
                client_id="cid",
                imdb_ids=["tt0001"],
            )

    assert exc.value.retry_after_seconds == 1


def test_fetch_list_imdb_ids_raises_rate_limit_after_second_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"}, text="still limited")

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktRateLimitError) as exc:
            trakt_sync.fetch_list_imdb_ids(
                http=client,
                user="u",
                slug="s",
                access_token="atok",
                client_id="cid",
            )

    assert exc.value.retry_after_seconds == 1


def test_fetch_list_imdb_ids_retries_once_on_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(
            200,
            json=[{"movie": {"ids": {"imdb": "tt0001"}}}],
        )

    with _mock_client(handler) as client:
        ids = trakt_sync.fetch_list_imdb_ids(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
        )

    assert ids == {"tt0001"}
    assert call_count["n"] == 2
    assert sleeps == [3.0]


def test_retry_sleep_is_capped_at_max(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Trakt could return a very long Retry-After; we must not block CI for that.
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(201)

    with _mock_client(handler) as client:
        trakt_sync.add_items(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
            imdb_ids=["tt0001"],
        )

    assert sleeps == [float(trakt_sync.MAX_RETRY_SLEEP_SECONDS)]


def test_retry_sleep_uses_default_when_no_retry_after(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, text="too many")
        return httpx.Response(201)

    with _mock_client(handler) as client:
        trakt_sync.add_items(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
            imdb_ids=["tt0001"],
        )

    assert sleeps == [float(trakt_sync.DEFAULT_RETRY_SLEEP_SECONDS)]


# ---------- pagination ----------


def test_fetch_list_imdb_ids_sends_pagination_query_params():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(dict(request.url.params))
        return httpx.Response(
            200,
            json=[{"movie": {"ids": {"imdb": "tt0001"}}}],
            headers={"X-Pagination-Page-Count": "1"},
        )

    with _mock_client(handler) as client:
        trakt_sync.fetch_list_imdb_ids(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
        )

    assert captured == [{"page": "1", "limit": str(trakt_sync.LIST_FETCH_PAGE_LIMIT)}]


def test_fetch_list_imdb_ids_follows_pagination_across_multiple_pages():
    pages_data = {
        "1": [{"movie": {"ids": {"imdb": f"tt{i:04d}"}}} for i in range(1, 101)],
        "2": [{"movie": {"ids": {"imdb": f"tt{i:04d}"}}} for i in range(101, 201)],
        "3": [{"movie": {"ids": {"imdb": f"tt{i:04d}"}}} for i in range(201, 251)],
    }
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        requested_pages.append(page)
        return httpx.Response(
            200,
            json=pages_data[page],
            headers={
                "X-Pagination-Page": page,
                "X-Pagination-Limit": "100",
                "X-Pagination-Page-Count": "3",
                "X-Pagination-Item-Count": "250",
            },
        )

    with _mock_client(handler) as client:
        ids = trakt_sync.fetch_list_imdb_ids(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
        )

    assert requested_pages == ["1", "2", "3"]
    assert len(ids) == 250
    assert "tt0001" in ids
    assert "tt0250" in ids


def test_fetch_list_imdb_ids_stops_after_last_page_per_header():
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        requested_pages.append(page)
        # Returns a FULL page (== LIST_FETCH_PAGE_LIMIT items) but header says
        # this is the only page — pagination must trust the header even though
        # the response is full.
        items = [
            {"movie": {"ids": {"imdb": f"tt{i:04d}"}}}
            for i in range(trakt_sync.LIST_FETCH_PAGE_LIMIT)
        ]
        return httpx.Response(
            200,
            json=items,
            headers={"X-Pagination-Page-Count": "1"},
        )

    with _mock_client(handler) as client:
        trakt_sync.fetch_list_imdb_ids(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
        )

    assert requested_pages == ["1"]


def test_fetch_list_imdb_ids_stops_on_partial_page_when_header_missing():
    """Trakt convention: a response smaller than the requested limit means
    we're on the last page. Defensive fallback when X-Pagination-Page-Count
    is absent (e.g. proxy stripped headers)."""
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        requested_pages.append(page)
        return httpx.Response(
            200,
            json=[{"movie": {"ids": {"imdb": "tt0001"}}}],
            # No X-Pagination-Page-Count header.
        )

    with _mock_client(handler) as client:
        ids = trakt_sync.fetch_list_imdb_ids(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
        )

    assert ids == {"tt0001"}
    assert requested_pages == ["1"]


def test_fetch_list_imdb_ids_429_retry_per_page(monkeypatch):
    """A 429 on page 2 must retry that page, not abort the whole pagination."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    page2_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        if page == "1":
            return httpx.Response(
                200,
                json=[{"movie": {"ids": {"imdb": "tt0001"}}}],
                headers={"X-Pagination-Page-Count": "2"},
            )
        # page 2 — first call 429, retry succeeds
        page2_calls["n"] += 1
        if page2_calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(
            200,
            json=[{"movie": {"ids": {"imdb": "tt0002"}}}],
            headers={"X-Pagination-Page-Count": "2"},
        )

    with _mock_client(handler) as client:
        ids = trakt_sync.fetch_list_imdb_ids(
            http=client,
            user="u",
            slug="s",
            access_token="atok",
            client_id="cid",
        )

    assert ids == {"tt0001", "tt0002"}
    assert page2_calls["n"] == 2  # one 429 + one success


def test_fetch_list_imdb_ids_fails_closed_at_max_pages(monkeypatch):
    """If pagination never terminates within MAX_LIST_FETCH_PAGES, we must
    raise rather than return an incomplete set — otherwise the sync would
    re-POST the "missing" entries forever and removal correctness breaks."""
    monkeypatch.setattr(trakt_sync, "MAX_LIST_FETCH_PAGES", 3)
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        requested_pages.append(page)
        # Always returns a full page with a header that lies about pages.
        items = [
            {"movie": {"ids": {"imdb": f"tt{int(page):04d}-{i:03d}"}}}
            for i in range(trakt_sync.LIST_FETCH_PAGE_LIMIT)
        ]
        return httpx.Response(
            200,
            json=items,
            headers={"X-Pagination-Page-Count": "9999"},
        )

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktError, match="exceeded 3 pages"):
            trakt_sync.fetch_list_imdb_ids(
                http=client,
                user="u",
                slug="s",
                access_token="atok",
                client_id="cid",
            )

    # Loop must stop at the cap — no extra requests beyond MAX_LIST_FETCH_PAGES.
    assert requested_pages == ["1", "2", "3"]


def test_extract_imdb_ids_skips_tv_series_rows():
    """A TV season row carries the *series* IMDb id (a show id). The Trakt
    sync mirrors the catalog through movie-only endpoints, so posting a show
    id under "movies" can never match -- Trakt reports it not-found and the
    diff would re-add it on every scheduled run forever. TV rows are
    identified by the persisted media_type field and skipped; no release
    URL sniffing is involved, and legacy rows without the field stay
    eligible movie rows."""
    releases = [
        {
            "movie_title": "Fight Club",
            "imdb_id": "tt0137523",
            "media_type": "movie",
            "release_url": "https://www.themoviedb.org/movie/550",
        },
        {
            "movie_title": "Game of Thrones: The Complete Eighth Season",
            "imdb_id": "tt0944947",
            "media_type": "tv",
        },
        {
            "movie_title": "Legacy Row Without Media Type",
            "imdb_id": "tt0068646",
        },
    ]

    valid, skipped = trakt_sync.extract_imdb_ids(releases)

    assert valid == ["tt0137523", "tt0068646"]
    assert skipped == ["Game of Thrones: The Complete Eighth Season"]
