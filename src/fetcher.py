from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import threading
import time
from urllib.parse import urlparse

import httpx


USER_AGENT = (
    "fel-dolby-vision-movies/0.1 (+https://github.com/Appz4Fun/fel-dolby-vision-movies)"
)


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str
    from_cache: bool
    error: str | None = None


class DomainRateLimiter:
    def __init__(self, delay_seconds: float = 1.0) -> None:
        self.delay_seconds = delay_seconds
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        domain = urlparse(url).netloc
        with self._lock:
            now = time.monotonic()
            last_seen = self._last_seen.get(domain)
            if last_seen is not None:
                sleep_for = self.delay_seconds - (now - last_seen)
                if sleep_for > 0:
                    time.sleep(sleep_for)
            self._last_seen[domain] = time.monotonic()


class Fetcher:
    def __init__(
        self,
        cache_dir: Path | str = ".cache/html",
        cache_ttl_seconds: int = 24 * 60 * 60,
        cookie_header: str | None = None,
        timeout_seconds: float = 20.0,
        retry_sleep_seconds: float = 1.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cookie_header = cookie_header
        self.retry_sleep_seconds = retry_sleep_seconds
        self.rate_limiter = DomainRateLimiter()
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT},
        )

    def fetch(self, url: str, *, raise_on_error: bool = True) -> FetchResult:
        cache_path = self._cache_path(url)
        cached = self._read_fresh_cache(cache_path)
        if cached is not None:
            return FetchResult(url=url, text=cached, from_cache=True)

        headers: dict[str, str] = {}
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header

        last_error: Exception | None = None
        for attempt in range(3):
            self.rate_limiter.wait(url)
            try:
                response = self.client.get(url, headers=headers)
                if response.status_code in {429, 500, 502, 503, 504}:
                    if attempt < 2:
                        time.sleep(self.retry_sleep_seconds * (attempt + 1))
                        continue
                response.raise_for_status()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(response.text, encoding="utf-8")
                return FetchResult(url=url, text=response.text, from_cache=False)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 2:  # pragma: no cover - retry path
                    time.sleep(self.retry_sleep_seconds * (attempt + 1))
                    continue
        message = f"failed to fetch {url}"
        if last_error is not None:
            message = f"{message}: {last_error}"
        if raise_on_error:
            raise RuntimeError(message) from last_error  # pragma: no cover
        return FetchResult(url=url, text="", from_cache=False, error=message)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Fetcher:
        return self  # pragma: no cover

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()  # pragma: no cover

    def _cache_path(self, url: str) -> Path:
        auth_namespace = "public"
        if self.cookie_header:
            auth_digest = hashlib.sha256(self.cookie_header.encode("utf-8")).hexdigest()
            auth_namespace = f"auth:{auth_digest}"
        cache_key = f"{auth_namespace}\0{url}"
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.html"

    def _read_fresh_cache(self, cache_path: Path) -> str | None:
        if not cache_path.exists():
            return None
        age = time.time() - cache_path.stat().st_mtime
        if age > self.cache_ttl_seconds:
            return None  # pragma: no cover - stale-cache eviction
        return cache_path.read_text(encoding="utf-8")
