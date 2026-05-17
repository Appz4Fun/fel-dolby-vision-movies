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
