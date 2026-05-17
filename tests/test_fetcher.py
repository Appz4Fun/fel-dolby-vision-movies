from pathlib import Path
import threading

import httpx
import respx

from fel_dolby_vision_movies.fetcher import DomainRateLimiter, Fetcher


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


def test_domain_rate_limiter_serializes_same_domain_waits(monkeypatch):
    sleeps = []
    limiter = DomainRateLimiter(delay_seconds=1.0)
    second_get_started = threading.Event()

    class RacingLastSeen(dict):
        def __init__(self) -> None:
            super().__init__()
            self.get_count = 0

        def get(self, key, default=None):
            self.get_count += 1
            if self.get_count == 1:
                second_get_started.wait(timeout=0.05)
            else:
                second_get_started.set()
            return super().get(key, default)

    limiter._last_seen = RacingLastSeen()

    monkeypatch.setattr(
        "fel_dolby_vision_movies.fetcher.time.monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        "fel_dolby_vision_movies.fetcher.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    barrier = threading.Barrier(2)

    def wait_once():
        barrier.wait()
        limiter.wait("https://example.test/thread")

    first = threading.Thread(target=wait_once)
    second = threading.Thread(target=wait_once)
    first.start()
    second.start()
    first.join()
    second.join()

    assert sleeps == [1.0]
