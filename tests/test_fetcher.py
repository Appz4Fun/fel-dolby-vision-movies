from pathlib import Path
import gzip
import threading

import httpx
import pytest
import respx

import fetcher as fetcher_module
from fetcher import DomainRateLimiter, Fetcher, PinnedHTTPTransport


PUBLIC_IP = "93.184.216.34"


class SafeMockTransport(httpx.MockTransport):
    def __init__(self, handler) -> None:
        super().__init__(handler)
        self.pinned = []

    def pin(self, url) -> None:
        self.pinned.append(url)


class TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks, error=None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    def __iter__(self):
        yield from self.chunks
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def public_test_dns(monkeypatch):
    monkeypatch.setattr(
        fetcher_module,
        "resolve_hostname",
        lambda _hostname: (PUBLIC_IP,),
    )


@respx.mock
def test_fetcher_uses_project_user_agent_and_cookie(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        return_value=httpx.Response(200, text="<html>FEL</html>")
    )
    fetcher = Fetcher(
        cache_dir=tmp_path,
        cookie_header="session=secret",
        trusted_cookie_hosts={"example.test"},
    )
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
    private_fetcher = Fetcher(
        cache_dir=tmp_path,
        cookie_header="session=secret",
        trusted_cookie_hosts={"example.test"},
    )

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
def test_fetcher_does_not_retry_permanent_404(tmp_path: Path):
    route = respx.get("https://example.test/missing").mock(
        return_value=httpx.Response(404, text="missing")
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)

    result = html_fetcher.fetch("https://example.test/missing", raise_on_error=False)

    assert result.error is not None
    assert "404" in result.error
    assert route.call_count == 1


def test_fetcher_retries_transport_failures(tmp_path: Path):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("temporary connection failure", request=request)
        return httpx.Response(200, text="recovered")

    transport = SafeMockTransport(handler)
    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        retry_sleep_seconds=0,
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=transport,
    )

    assert html_fetcher.fetch("https://example.test/thread").text == "recovered"
    assert calls == 3


def test_fetcher_pins_all_validated_ips_without_reresolving(tmp_path: Path):
    resolver_calls = []

    def resolver(hostname):
        resolver_calls.append(hostname)
        return (PUBLIC_IP, "1.1.1.1")

    def handler(request):
        assert request.url.host == "example.test"
        assert request.headers["Host"] == "example.test"
        return httpx.Response(200, text="pinned")

    transport = SafeMockTransport(handler)
    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        resolver=resolver,
        transport=transport,
    )

    assert html_fetcher.fetch("https://example.test/thread").text == "pinned"
    assert resolver_calls == ["example.test"]
    assert transport.pinned[0].resolved_ips == (PUBLIC_IP, "1.1.1.1")


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


@respx.mock
def test_fetcher_raises_detailed_failed_fetch_message(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        return_value=httpx.Response(503, text="temporarily unavailable")
    )
    fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)

    with pytest.raises(RuntimeError, match="503 Service Unavailable"):
        fetcher.fetch("https://example.test/thread")

    assert route.call_count == 3


def test_domain_rate_limiter_serializes_same_domain_waits(monkeypatch):
    sleeps = []
    limiter = DomainRateLimiter(delay_seconds=1.0)

    monkeypatch.setattr(
        "fetcher.time.monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        "fetcher.time.sleep",
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


def test_domain_rate_limiter_does_not_block_unrelated_domains(monkeypatch):
    limiter = DomainRateLimiter(delay_seconds=1.0)
    limiter.wait("https://one.example/first")
    sleep_started = threading.Event()
    release_sleep = threading.Event()
    unrelated_done = threading.Event()

    def blocking_sleep(_seconds):
        sleep_started.set()
        release_sleep.wait(timeout=1)

    monkeypatch.setattr("fetcher.time.sleep", blocking_sleep)
    delayed = threading.Thread(
        target=limiter.wait, args=("https://one.example/second",)
    )
    delayed.start()
    assert sleep_started.wait(timeout=1)

    def wait_for_unrelated_domain():
        limiter.wait("https://two.example/first")
        unrelated_done.set()

    unrelated = threading.Thread(target=wait_for_unrelated_domain)
    unrelated.start()
    try:
        assert unrelated_done.wait(timeout=0.1)
    finally:
        release_sleep.set()
        delayed.join(timeout=1)
        unrelated.join(timeout=1)


def test_fetcher_enforces_five_mib_decoded_body_limit(tmp_path: Path, monkeypatch):
    assert fetcher_module.MAX_RESPONSE_BODY_BYTES == 5 * 1024 * 1024
    monkeypatch.setattr(fetcher_module, "MAX_RESPONSE_BODY_BYTES", 8)
    stream = TrackingStream([b"12345678", b"9"])
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=stream)

    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        retry_sleep_seconds=0,
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=SafeMockTransport(handler),
    )

    result = html_fetcher.fetch("https://example.test/large", raise_on_error=False)

    assert result.error is not None
    assert "decoded response body exceeds" in result.error
    assert calls == 1
    assert stream.closed is True


def test_fetcher_caps_compressed_expansion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fetcher_module, "MAX_RESPONSE_BODY_BYTES", 64)
    stream = TrackingStream([gzip.compress(b"x" * 65)])
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
        )

    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        retry_sleep_seconds=0,
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=SafeMockTransport(handler),
    )

    result = html_fetcher.fetch("https://example.test/compressed", raise_on_error=False)

    assert result.error is not None
    assert "decoded response body exceeds" in result.error
    assert calls == 1
    assert stream.closed is True


def test_fetcher_closes_response_on_decode_failure_without_retry(tmp_path: Path):
    stream = TrackingStream([b"not-gzip"])
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
        )

    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        retry_sleep_seconds=0,
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=SafeMockTransport(handler),
    )

    result = html_fetcher.fetch("https://example.test/bad-gzip", raise_on_error=False)

    assert result.error is not None
    assert calls == 1
    assert stream.closed is True


def test_fetcher_closes_each_response_before_retrying_read_failure(tmp_path: Path):
    streams = []
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            stream = TrackingStream(
                [b"partial"],
                httpx.ReadError("temporary read failure", request=request),
            )
            streams.append(stream)
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, text="recovered")

    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        retry_sleep_seconds=0,
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=SafeMockTransport(handler),
    )

    assert html_fetcher.fetch("https://example.test/read").text == "recovered"
    assert calls == 3
    assert all(stream.closed for stream in streams)


@respx.mock
def test_fetcher_scopes_cookie_to_exact_trusted_https_host(tmp_path: Path):
    trusted = respx.get("https://forum.blu-ray.com/thread").mock(
        return_value=httpx.Response(200, text="trusted")
    )
    subdomain = respx.get("https://sub.forum.blu-ray.com/thread").mock(
        return_value=httpx.Response(200, text="subdomain")
    )
    insecure = respx.get("http://forum.blu-ray.com/thread").mock(
        return_value=httpx.Response(200, text="insecure")
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, cookie_header="session=secret")

    html_fetcher.fetch("https://forum.blu-ray.com/thread")
    html_fetcher.fetch("https://sub.forum.blu-ray.com/thread")
    html_fetcher.fetch("http://forum.blu-ray.com/thread")

    assert trusted.calls.last.request.headers["Cookie"] == "session=secret"
    assert "Cookie" not in subdomain.calls.last.request.headers
    assert "Cookie" not in insecure.calls.last.request.headers


@respx.mock
def test_fetcher_does_not_gain_cookie_on_untrusted_to_trusted_redirect(
    tmp_path: Path,
):
    origin = respx.get("https://attacker.test/start").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://forum.blu-ray.com/private"},
        )
    )
    destination = respx.get("https://forum.blu-ray.com/private").mock(
        return_value=httpx.Response(200, text="public view")
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, cookie_header="session=secret")

    assert html_fetcher.fetch("https://attacker.test/start").text == "public view"

    assert "Cookie" not in origin.calls.last.request.headers
    assert "Cookie" not in destination.calls.last.request.headers


@respx.mock
def test_fetcher_does_not_regain_cookie_after_redirecting_through_untrusted_host(
    tmp_path: Path,
):
    first = respx.get("https://forum.blu-ray.com/start").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://attacker.test/bounce"}
        )
    )
    middle = respx.get("https://attacker.test/bounce").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://forum.blu-ray.com/private"}
        )
    )
    last = respx.get("https://forum.blu-ray.com/private").mock(
        return_value=httpx.Response(200, text="public view")
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, cookie_header="session=secret")

    assert html_fetcher.fetch("https://forum.blu-ray.com/start").text == "public view"

    assert first.calls.last.request.headers["Cookie"] == "session=secret"
    assert "Cookie" not in middle.calls.last.request.headers
    assert "Cookie" not in last.calls.last.request.headers


@respx.mock
def test_fetcher_rejects_https_downgrade_redirect(tmp_path: Path):
    route = respx.get("https://example.test/start").mock(
        return_value=httpx.Response(
            302, headers={"Location": "http://example.test/insecure"}
        )
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)

    result = html_fetcher.fetch("https://example.test/start", raise_on_error=False)

    assert route.call_count == 1
    assert result.error is not None
    assert "HTTPS redirect downgrade" in result.error


@respx.mock
def test_fetcher_rejects_control_characters_before_resolving_redirect(tmp_path: Path):
    route = respx.get("https://example.test/start").mock(
        return_value=httpx.Response(
            302, headers={"Location": "\thttps://attacker.test/normalized"}
        )
    )
    destination = respx.get("https://attacker.test/normalized").mock(
        return_value=httpx.Response(200, text="must not fetch")
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)

    result = html_fetcher.fetch("https://example.test/start", raise_on_error=False)

    assert route.call_count == 1
    assert destination.call_count == 0
    assert result.error is not None
    assert "control characters" in result.error


@respx.mock
def test_fetcher_rejects_redirect_to_private_dns_target(tmp_path: Path):
    route = respx.get("https://example.test/start").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://private.test/admin"}
        )
    )
    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        retry_sleep_seconds=0,
        resolver=lambda hostname: (
            ("127.0.0.1",) if hostname == "private.test" else (PUBLIC_IP,)
        ),
    )

    result = html_fetcher.fetch("https://example.test/start", raise_on_error=False)

    assert route.call_count == 1
    assert result.error is not None
    assert "not globally routable" in result.error


@respx.mock
def test_fetcher_rejects_redirect_loops(tmp_path: Path):
    route = respx.get("https://example.test/start").mock(
        return_value=httpx.Response(302, headers={"Location": "/start"})
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)

    result = html_fetcher.fetch("https://example.test/start", raise_on_error=False)

    assert route.call_count == 1
    assert result.error is not None
    assert "redirect loop" in result.error


@respx.mock
def test_fetcher_rejects_more_than_five_redirects(tmp_path: Path):
    routes = [
        respx.get(f"https://example.test/{index}").mock(
            return_value=httpx.Response(302, headers={"Location": f"/{index + 1}"})
        )
        for index in range(6)
    ]
    html_fetcher = Fetcher(
        cache_dir=tmp_path,
        retry_sleep_seconds=0,
    )
    html_fetcher.rate_limiter.delay_seconds = 0

    result = html_fetcher.fetch("https://example.test/0", raise_on_error=False)

    assert result.error is not None
    assert "redirect limit exceeded (5)" in result.error
    assert [route.call_count for route in routes] == [1, 1, 1, 1, 1, 1]


@respx.mock
def test_fetcher_reports_redirect_without_location_as_error(tmp_path: Path):
    route = respx.get("https://example.test/start").mock(
        return_value=httpx.Response(302, text="no destination")
    )
    html_fetcher = Fetcher(cache_dir=tmp_path, retry_sleep_seconds=0)

    result = html_fetcher.fetch("https://example.test/start", raise_on_error=False)

    assert result.error is not None
    assert "302 Found" in result.error
    assert route.call_count == 1


@respx.mock
def test_fetcher_validates_dns_before_reading_cache(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        return_value=httpx.Response(200, text="cached")
    )
    safe_fetcher = Fetcher(cache_dir=tmp_path)
    assert safe_fetcher.fetch("https://example.test/thread").text == "cached"

    rebound_fetcher = Fetcher(
        cache_dir=tmp_path,
        resolver=lambda _hostname: ("127.0.0.1",),
        retry_sleep_seconds=0,
    )
    result = rebound_fetcher.fetch("https://example.test/thread", raise_on_error=False)

    assert result.error is not None
    assert "not globally routable" in result.error
    assert route.call_count == 1


@respx.mock
def test_untrusted_cookie_fetch_uses_public_cache_namespace(tmp_path: Path):
    route = respx.get("https://example.test/thread").mock(
        return_value=httpx.Response(200, text="public")
    )
    cookie_fetcher = Fetcher(cache_dir=tmp_path, cookie_header="session=secret")
    public_fetcher = Fetcher(cache_dir=tmp_path)

    assert cookie_fetcher.fetch("https://example.test/thread").text == "public"
    assert public_fetcher.fetch("https://example.test/thread").text == "public"
    assert route.call_count == 1


def test_fetcher_disables_environment_proxy_configuration(tmp_path: Path):
    html_fetcher = Fetcher(cache_dir=tmp_path)

    assert html_fetcher.client._trust_env is False


def test_fetcher_rejects_unpinned_http_transport_injection(tmp_path: Path):
    unsafe_transport = httpx.HTTPTransport(trust_env=False)
    try:
        with pytest.raises(TypeError, match="pin-capable"):
            Fetcher(cache_dir=tmp_path, transport=unsafe_transport)
    finally:
        unsafe_transport.close()


def test_pinned_http_transport_installs_pinned_network_backend():
    transport = PinnedHTTPTransport()
    try:
        assert transport._pool._network_backend is transport._pinned_backend
    finally:
        transport.close()


def test_pinned_http_transport_fails_clearly_without_network_backend(monkeypatch):
    def init_without_network_backend(self, **_kwargs):
        self._pool = object()

    monkeypatch.setattr(httpx.HTTPTransport, "__init__", init_without_network_backend)

    with pytest.raises(RuntimeError, match="_network_backend"):
        PinnedHTTPTransport()


def test_fetcher_raises_for_unsafe_url_by_default(tmp_path: Path):
    html_fetcher = Fetcher(cache_dir=tmp_path)

    with pytest.raises(RuntimeError, match="not globally routable"):
        html_fetcher.fetch("http://127.0.0.1/admin")
