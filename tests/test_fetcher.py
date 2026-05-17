from pathlib import Path

import httpx
import respx

from fel_dolby_vision_movies.fetcher import Fetcher


@respx.mock
def test_fetcher_uses_project_user_agent_and_cookie(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        return_value=httpx.Response(200, text="<html>FEL</html>")
    )
    fetcher = Fetcher(cache_dir=tmp_path, cookie_header="session=secret")
    result = fetcher.fetch("https://example.test/thread")
    assert result.text == "<html>FEL</html>"
    request = route.calls.last.request
    assert "fel-dolby-vision-movies" in request.headers["User-Agent"]
    assert request.headers["Cookie"] == "session=secret"


@respx.mock
def test_fetcher_reads_fresh_cache_without_second_request(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        return_value=httpx.Response(200, text="first")
    )
    fetcher = Fetcher(cache_dir=tmp_path, cache_ttl_seconds=3600)
    assert fetcher.fetch("https://example.test/thread").text == "first"
    assert fetcher.fetch("https://example.test/thread").text == "first"
    assert route.call_count == 1


@respx.mock
def test_fetcher_separates_authenticated_and_unauthenticated_cache(tmp_path: Path):
    url = "https://example.test/thread"
    route = respx.get(url).mock(
        side_effect=[
            httpx.Response(200, text="public"),
            httpx.Response(200, text="private"),
        ]
    )

    public_fetcher = Fetcher(cache_dir=tmp_path)
    private_fetcher = Fetcher(cache_dir=tmp_path, cookie_header="session=secret")

    assert public_fetcher.fetch(url).text == "public"
    assert private_fetcher.fetch(url).text == "private"
    assert route.call_count == 2


def test_fetcher_close_closes_underlying_client(tmp_path: Path):
    fetcher = Fetcher(cache_dir=tmp_path)

    fetcher.close()

    assert fetcher.client.is_closed


@respx.mock
def test_fetcher_retries_transient_500(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        side_effect=[
            httpx.Response(500, text="bad"),
            httpx.Response(200, text="good"),
        ]
    )
    fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)
    assert fetcher.fetch("https://example.test/thread").text == "good"
    assert route.call_count == 2


@respx.mock
def test_fetcher_can_record_failed_fetch_without_raising(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        return_value=httpx.Response(503, text="temporarily unavailable")
    )
    fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)

    result = fetcher.fetch("https://example.test/thread", raise_on_error=False)

    assert result.url == "https://example.test/thread"
    assert result.text == ""
    assert result.from_cache is False
    assert result.error is not None
    assert "503" in result.error
    assert route.call_count == 3
