from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import threading
import time
from typing import Protocol, runtime_checkable
import urllib.parse

import httpx

from url_security import (
    PinnedNetworkBackend,
    Resolver,
    UnsafeURLError,
    ValidatedURL,
    ensure_safe_url_text,
    resolve_hostname,
    validate_public_url,
)


USER_AGENT = (
    "fel-dolby-vision-movies/0.1 (+https://github.com/Appz4Fun/fel-dolby-vision-movies)"
)
DEFAULT_TRUSTED_COOKIE_HOSTS = frozenset({"forum.blu-ray.com"})
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
MAX_REDIRECTS = 5
# Enforced after HTTP content decoding, including gzip/deflate expansion.
MAX_RESPONSE_BODY_BYTES = 5 * 1024 * 1024
RESPONSE_CHUNK_BYTES = 64 * 1024


class ResponseBodyTooLargeError(httpx.HTTPError):
    """Raised when decoded response content exceeds the hard safety limit."""


@runtime_checkable
class PinCapableTransport(Protocol):
    """Transport contract that pins each validated URL before network I/O."""

    def pin(self, url: ValidatedURL) -> None:
        raise NotImplementedError

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str
    from_cache: bool
    error: str | None = None


@dataclass
class _DomainRateState:
    lock: threading.Lock
    next_allowed: float = 0.0


class DomainRateLimiter:
    def __init__(self, delay_seconds: float = 1.0) -> None:
        self.delay_seconds = delay_seconds
        self._states: dict[str, _DomainRateState] = {}
        self._states_lock = threading.Lock()

    def wait(self, url: str) -> None:
        domain = urllib.parse.urlparse(url).netloc
        with self._states_lock:
            state = self._states.setdefault(
                domain, _DomainRateState(lock=threading.Lock())
            )
        with state.lock:
            now = time.monotonic()
            allowed_at = max(now, state.next_allowed)
            state.next_allowed = allowed_at + self.delay_seconds
            sleep_for = allowed_at - now
        if sleep_for > 0:
            time.sleep(sleep_for)


class Fetcher:
    def __init__(
        self,
        cache_dir: Path | str = ".cache/html",
        cache_ttl_seconds: int = 24 * 60 * 60,
        cookie_header: str | None = None,
        timeout_seconds: float = 20.0,
        retry_sleep_seconds: float = 1.0,
        trusted_cookie_hosts: set[str] | frozenset[str] | None = None,
        resolver: Resolver | None = None,
        transport: PinCapableTransport | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cookie_header = cookie_header
        self.retry_sleep_seconds = retry_sleep_seconds
        self.trusted_cookie_hosts = frozenset(
            host.casefold().rstrip(".")
            for host in (
                DEFAULT_TRUSTED_COOKIE_HOSTS
                if trusted_cookie_hosts is None
                else trusted_cookie_hosts
            )
        )
        self.resolver = resolver or resolve_hostname
        self.rate_limiter = DomainRateLimiter()
        selected_transport = PinnedHTTPTransport() if transport is None else transport
        if not isinstance(selected_transport, PinCapableTransport):
            raise TypeError("Fetcher transport must be pin-capable")
        self.transport = selected_transport
        self.client = httpx.Client(
            follow_redirects=False,
            timeout=timeout_seconds,
            trust_env=False,
            headers={"User-Agent": USER_AGENT},
            transport=self.transport,
        )

    def fetch(self, url: str, *, raise_on_error: bool = True) -> FetchResult:
        try:
            initial = validate_public_url(url, resolver=self.resolver)
        except UnsafeURLError as exc:
            return self._failed_result(url, exc, raise_on_error)

        credential_eligible = self._cookie_allowed(initial)
        cache_path = self._cache_path(initial.url, authenticated=credential_eligible)
        cached = self._read_fresh_cache(cache_path)
        if cached is not None:
            return FetchResult(url=initial.url, text=cached, from_cache=True)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._get_with_redirects(initial, credential_eligible)
                response.raise_for_status()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(response.text, encoding="utf-8")
                return FetchResult(
                    url=initial.url, text=response.text, from_cache=False
                )
            except UnsafeURLError as exc:
                return self._failed_result(initial.url, exc, raise_on_error)
            except httpx.HTTPError as exc:
                last_error = exc
                if _is_retryable_fetch_error(exc) and attempt < 2:
                    time.sleep(self.retry_sleep_seconds * (attempt + 1))
                    continue
                break
        message = f"failed to fetch {initial.url}"
        if last_error is not None:
            message = f"{message}: {last_error}"
        if raise_on_error:
            raise RuntimeError(message) from last_error  # pragma: no cover
        return FetchResult(url=initial.url, text="", from_cache=False, error=message)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Fetcher:
        return self  # pragma: no cover

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()  # pragma: no cover

    def _cache_path(self, url: str, *, authenticated: bool) -> Path:
        auth_namespace = "public"
        if authenticated and self.cookie_header:
            auth_digest = hashlib.sha256(self.cookie_header.encode("utf-8")).hexdigest()
            auth_namespace = f"auth:{auth_digest}"
        cache_key = f"{auth_namespace}\0{url}"
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.html"

    def _get_with_redirects(
        self, initial: ValidatedURL, credential_eligible: bool
    ) -> httpx.Response:
        current = initial
        credentials_active = credential_eligible
        seen = {initial.url}
        redirects = 0
        while True:
            self.transport.pin(current)
            headers: dict[str, str] = {}
            if credentials_active and self._cookie_allowed(current):
                assert self.cookie_header is not None
                headers["Cookie"] = self.cookie_header
            self.rate_limiter.wait(current.url)
            self.client.cookies.clear()
            response = self._send_single_request(current.url, headers)
            if response.status_code not in REDIRECT_STATUS_CODES:
                return response
            location = response.headers.get("Location")
            if not location:
                return response
            if redirects >= MAX_REDIRECTS:
                raise UnsafeURLError(f"redirect limit exceeded ({MAX_REDIRECTS})")
            ensure_safe_url_text(location)
            redirect_url = urllib.parse.urljoin(current.url, location)
            next_url = validate_public_url(redirect_url, resolver=self.resolver)
            if current.url.startswith("https://") and next_url.url.startswith(
                "http://"
            ):
                raise UnsafeURLError("HTTPS redirect downgrade is not allowed")
            if next_url.url in seen:
                raise UnsafeURLError("redirect loop is not allowed")
            seen.add(next_url.url)
            redirects += 1
            credentials_active = credentials_active and self._cookie_allowed(next_url)
            current = next_url

    def _send_single_request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        """Send one hop without HTTPX pre-parsing the Location header."""
        request = self.client.build_request("GET", url, headers=headers)
        response = self.transport.handle_request(request)
        response.request = request
        try:
            chunks: list[bytes] = []
            decoded_size = 0
            for chunk in response.iter_bytes(chunk_size=RESPONSE_CHUNK_BYTES):
                decoded_size += len(chunk)
                if decoded_size > MAX_RESPONSE_BODY_BYTES:
                    raise ResponseBodyTooLargeError(
                        f"decoded response body exceeds {MAX_RESPONSE_BODY_BYTES} bytes"
                    )
                chunks.append(chunk)
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                request=request,
                extensions=response.extensions,
            )
        finally:
            response.close()

    def _cookie_allowed(self, url: ValidatedURL) -> bool:
        return bool(
            self.cookie_header
            and url.url.startswith("https://")
            and url.hostname in self.trusted_cookie_hosts
        )

    @staticmethod
    def _failed_result(url: str, error: Exception, raise_on_error: bool) -> FetchResult:
        message = f"failed to fetch {url}: {error}"
        if raise_on_error:
            raise RuntimeError(message) from error
        return FetchResult(url=url, text="", from_cache=False, error=message)

    def _read_fresh_cache(self, cache_path: Path) -> str | None:
        if not cache_path.exists():
            return None
        age = time.time() - cache_path.stat().st_mtime
        if age > self.cache_ttl_seconds:
            return None  # pragma: no cover - stale-cache eviction
        return cache_path.read_text(encoding="utf-8")


class PinnedHTTPTransport(httpx.HTTPTransport):
    """HTTPX transport whose DNS connection target is pinned after validation.

    Requests keep their original URL, Host header, and TLS SNI hostname. Only
    the underlying TCP destination is replaced with the validated IP address.
    """

    def __init__(self) -> None:
        super().__init__(trust_env=False)
        self._pinned_backend = PinnedNetworkBackend(self._pool._network_backend)
        self._pool._network_backend = self._pinned_backend

    def pin(self, url: ValidatedURL) -> None:
        self._pinned_backend.pin(url.hostname, url.resolved_ips)


def _is_retryable_fetch_error(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS_CODES
    return isinstance(exc, httpx.TransportError)
