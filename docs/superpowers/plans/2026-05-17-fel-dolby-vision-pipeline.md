# FEL Dolby Vision Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live-first Python pipeline that discovers, scrapes, validates, and publishes confirmed Dolby Vision Profile 7 FEL movie releases.

**Architecture:** Use focused modules under `src/fel_dolby_vision_movies/`. Live fetching and discovery run through `httpx`, cache snapshots under ignored `.cache/`, parse HTML with BeautifulSoup, publish canonical data to `data/releases.json`, generate Markdown and a static Pages dashboard, and gate CI with deterministic tests and benchmarks before scrape/deploy.

**Tech Stack:** Python 3.11, uv, requirements files, httpx, beautifulsoup4, python-dotenv, pytest, pytest-cov, respx, ruff, GitHub Actions, GitHub Pages artifact deployment.

---

## File Structure

- Create `requirements.txt`: runtime dependencies.
- Create `requirements-dev.txt`: test, coverage, HTTP mocking, and lint dependencies.
- Modify `justfile`: replace placeholder commands and add `test`, `lint`, `run`, and `ci`.
- Create `src/fel_dolby_vision_movies/__init__.py`: package marker and version.
- Create `src/fel_dolby_vision_movies/models.py`: release, evidence, source, fetch result dataclasses.
- Create `src/fel_dolby_vision_movies/normalize.py`: audio and text normalization.
- Create `src/fel_dolby_vision_movies/fetcher.py`: HTTP client, cache, rate-limit, retries, cookie-header handling.
- Create `src/fel_dolby_vision_movies/sources.py`: `forums.txt` load/save and confirmed-source merge policy.
- Create `src/fel_dolby_vision_movies/discovery.py`: Brave Search and in-page source discovery.
- Create `src/fel_dolby_vision_movies/parser.py`: strict FEL correlation and metadata extraction.
- Create `src/fel_dolby_vision_movies/artifacts.py`: `data/releases.json`, `README.md`, and `links.md` generation.
- Create `src/fel_dolby_vision_movies/dashboard.py`: static GitHub Pages dashboard generation.
- Create `src/fel_dolby_vision_movies/benchmark.py`: deterministic benchmark runner.
- Create `src/fel_dolby_vision_movies/main.py`: CLI entrypoint for `search-for-sources`, `scrape-for-titles`, and `run`.
- Create `tests/`: deterministic tests and fixtures.
- Create `.github/workflows/daily-run.yml`: production CI/scrape/Pages workflow.

## Task 1: Project Bootstrap And Command Contract

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `src/fel_dolby_vision_movies/__init__.py`
- Modify: `justfile`

- [ ] **Step 1: Replace dependency files**

Create `requirements.txt` with:

```text
beautifulsoup4>=4.12,<5
httpx>=0.27,<1
python-dotenv>=1,<2
```

Create `requirements-dev.txt` with:

```text
-r requirements.txt
pytest>=8,<9
pytest-cov>=5,<7
respx>=0.21,<1
ruff>=0.6,<1
```

- [ ] **Step 2: Create package marker**

Create `src/fel_dolby_vision_movies/__init__.py` with:

```python
"""FEL Dolby Vision movie discovery and publishing pipeline."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Replace `justfile` command contract**

Replace `justfile` with:

```make
set dotenv-load := true
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

export PYTHONPATH := "src"

default:
    @just --list

search-for-sources:
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main search-for-sources --sources forums.txt

scrape-for-titles:
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main scrape-for-titles --sources forums.txt

run:
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main run --sources forums.txt

test:
    uv run --with-requirements requirements-dev.txt pytest --cov=src/fel_dolby_vision_movies --cov-report=term-missing

lint:
    uv run --with-requirements requirements-dev.txt ruff check src tests
    uv run --with-requirements requirements-dev.txt ruff format --check src tests

ci:
    just lint
    just test
    uv run --with-requirements requirements-dev.txt python -m fel_dolby_vision_movies.benchmark tests/fixtures/benchmark_cases.json
```

- [ ] **Step 4: Verify command discovery**

Run:

```bash
just --list
```

Expected: output includes `search-for-sources`, `scrape-for-titles`, `run`, `test`, `lint`, and `ci`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt requirements-dev.txt src/fel_dolby_vision_movies/__init__.py justfile
git commit -m "chore: bootstrap Python project commands"
```

## Task 2: Models And Audio Normalization

**Files:**
- Create: `src/fel_dolby_vision_movies/models.py`
- Create: `src/fel_dolby_vision_movies/normalize.py`
- Create: `tests/test_models_normalize.py`

- [ ] **Step 1: Write failing normalization and model tests**

Create `tests/test_models_normalize.py` with:

```python
from fel_dolby_vision_movies.models import FelEvidence, FelRelease
from fel_dolby_vision_movies.normalize import normalize_audio, normalize_title


def test_normalize_audio_known_aliases():
    assert normalize_audio("Dolby TrueHD Atmos") == ["TrueHD Atmos"]
    assert normalize_audio("Atmos (TrueHD)") == ["TrueHD Atmos"]
    assert normalize_audio("Dolby Digital Plus Atmos / E-AC3 Atmos") == ["DD+ Atmos"]
    assert normalize_audio("DTS-HD Master Audio 7.1") == ["DTS-HD MA"]
    assert normalize_audio("DTS-X") == ["DTS:X"]


def test_normalize_audio_preserves_multiple_distinct_formats():
    assert normalize_audio("English TrueHD Atmos; Japanese DTS-HD MA") == [
        "TrueHD Atmos",
        "DTS-HD MA",
    ]


def test_unknown_audio_returns_cleaned_value():
    assert normalize_audio("PCM 2.0 Mono") == ["PCM 2.0 Mono"]


def test_title_normalization_collapses_spacing():
    assert normalize_title("  The   Matrix\tReloaded  ") == "The Matrix Reloaded"


def test_fel_release_publish_gate_and_unknowns():
    evidence = FelEvidence(
        source_url="https://example.test/thread",
        quote="The Matrix is Profile 7 FEL",
        evidence_type="sentence",
    )
    release = FelRelease(movie_title="The Matrix", fel_evidence=evidence)
    assert release.fel_confirmed is True
    assert release.release_date == "Unknown"
    assert release.studio == "Unknown"
    assert release.english_audio == "Unknown"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_models_normalize.py -q
```

Expected: fails because `fel_dolby_vision_movies.models` and `normalize` do not exist.

- [ ] **Step 3: Implement dataclasses**

Create `src/fel_dolby_vision_movies/models.py` with dataclasses matching these public fields:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


UNKNOWN = "Unknown"


@dataclass(frozen=True)
class FelEvidence:
    source_url: str
    quote: str
    evidence_type: str
    location: str = UNKNOWN


@dataclass
class FelRelease:
    movie_title: str
    fel_evidence: FelEvidence
    release_date: str = UNKNOWN
    studio: str = UNKNOWN
    audio_formats: list[str] = field(default_factory=list)
    english_audio: str = UNKNOWN
    additional_characteristics: dict[str, Any] = field(default_factory=dict)
    source_label: str = UNKNOWN
    collected_at: str = UNKNOWN
    fel_confirmed: bool = True

    @property
    def source_url(self) -> str:
        return self.fel_evidence.source_url

    def to_dict(self) -> dict[str, Any]:
        return {
            "movie_title": self.movie_title,
            "fel_confirmed": self.fel_confirmed,
            "release_date": self.release_date,
            "studio": self.studio,
            "audio_formats": self.audio_formats,
            "english_audio": self.english_audio,
            "additional_characteristics": self.additional_characteristics,
            "source_url": self.source_url,
            "source_label": self.source_label,
            "fel_evidence": {
                "source_url": self.fel_evidence.source_url,
                "quote": self.fel_evidence.quote,
                "evidence_type": self.fel_evidence.evidence_type,
                "location": self.fel_evidence.location,
            },
            "collected_at": self.collected_at,
        }
```

- [ ] **Step 4: Implement normalization**

Create `src/fel_dolby_vision_movies/normalize.py` with:

```python
from __future__ import annotations

import re


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_audio(raw_string: str) -> list[str]:
    cleaned = normalize_title(raw_string)
    if not cleaned:
        return []

    lowered = cleaned.lower()
    matches: list[str] = []

    def add(value: str) -> None:
        if value not in matches:
            matches.append(value)

    if "atmos" in lowered and (
        "truehd" in lowered or "true hd" in lowered or "dolby truehd" in lowered
    ):
        add("TrueHD Atmos")
    if "atmos" in lowered and (
        "dd+" in lowered
        or "digital plus" in lowered
        or "e-ac3" in lowered
        or "eac3" in lowered
    ):
        add("DD+ Atmos")
    if "dts:x" in lowered or "dts-x" in lowered:
        add("DTS:X")
    if (
        "dts-hd ma" in lowered
        or "dts-hd master audio" in lowered
        or "dts-ma" in lowered
    ):
        add("DTS-HD MA")
    if not any(value.startswith("TrueHD") for value in matches) and (
        "truehd" in lowered or "true hd" in lowered or "dolby truehd" in lowered
    ):
        add("TrueHD")
    if not any(value.startswith("DD+") for value in matches) and (
        "dd+" in lowered
        or "digital plus" in lowered
        or "e-ac3" in lowered
        or "eac3" in lowered
    ):
        add("DD+")

    return matches or [cleaned]
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_models_normalize.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/fel_dolby_vision_movies/models.py src/fel_dolby_vision_movies/normalize.py tests/test_models_normalize.py
git commit -m "feat: add release models and audio normalization"
```

## Task 3: Source Registry And Confirmed-Only Merge Policy

**Files:**
- Create: `src/fel_dolby_vision_movies/sources.py`
- Create: `tests/test_sources.py`

- [ ] **Step 1: Write source registry tests**

Create `tests/test_sources.py` with:

```python
from pathlib import Path

from fel_dolby_vision_movies.sources import (
    merge_confirmed_sources,
    read_source_urls,
    write_source_urls,
)


def test_read_source_urls_ignores_blanks_and_comments(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n# seed\nhttps://example.test/a\n\nhttps://example.test/a\nhttps://example.test/b\n",
        encoding="utf-8",
    )
    assert read_source_urls(path) == ["https://example.test/a", "https://example.test/b"]


def test_merge_confirmed_sources_adds_only_confirmed(tmp_path: Path):
    path = tmp_path / "forums.txt"
    write_source_urls(path, ["https://example.test/a"])
    changed = merge_confirmed_sources(
        path,
        confirmed_urls=["https://example.test/b", "https://example.test/a"],
    )
    assert changed is True
    assert read_source_urls(path) == ["https://example.test/a", "https://example.test/b"]


def test_merge_confirmed_sources_noops_without_confirmed_urls(tmp_path: Path):
    path = tmp_path / "forums.txt"
    write_source_urls(path, ["https://example.test/a"])
    changed = merge_confirmed_sources(path, confirmed_urls=[])
    assert changed is False
    assert read_source_urls(path) == ["https://example.test/a"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_sources.py -q
```

Expected: fails because `sources.py` does not exist.

- [ ] **Step 3: Implement source registry**

Create `src/fel_dolby_vision_movies/sources.py` with:

```python
from __future__ import annotations

from pathlib import Path


def read_source_urls(path: Path | str) -> list[str]:
    source_path = Path(path)
    if not source_path.exists():
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for raw_line in source_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line in seen:
            continue
        seen.add(line)
        urls.append(line)
    return urls


def write_source_urls(path: Path | str, urls: list[str]) -> None:
    source_path = Path(path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    unique = list(dict.fromkeys(urls))
    text = "\n".join(unique)
    if text:
        text += "\n"
    source_path.write_text(text, encoding="utf-8")


def merge_confirmed_sources(path: Path | str, confirmed_urls: list[str]) -> bool:
    current = read_source_urls(path)
    merged = list(dict.fromkeys([*current, *confirmed_urls]))
    if merged == current:
        return False
    write_source_urls(path, merged)
    return True
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_sources.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fel_dolby_vision_movies/sources.py tests/test_sources.py
git commit -m "feat: add source registry handling"
```

## Task 4: HTTP Fetcher, Cache, Rate Limits, And Secret Handling

**Files:**
- Create: `src/fel_dolby_vision_movies/fetcher.py`
- Create: `tests/test_fetcher.py`

- [ ] **Step 1: Write fetch/cache tests**

Create `tests/test_fetcher.py` with:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_fetcher.py -q
```

Expected: fails because `fetcher.py` does not exist.

- [ ] **Step 3: Implement fetcher**

Create `src/fel_dolby_vision_movies/fetcher.py` with these public APIs:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import time
from urllib.parse import urlparse

import httpx


USER_AGENT = "fel-dolby-vision-movies/0.1 (+https://github.com/Appz4Fun/fel-dolby-vision-movies)"


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str
    from_cache: bool


class DomainRateLimiter:
    def __init__(self, delay_seconds: float = 1.0) -> None:
        self.delay_seconds = delay_seconds
        self._last_seen: dict[str, float] = {}

    def wait(self, url: str) -> None:
        domain = urlparse(url).netloc
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

    def fetch(self, url: str) -> FetchResult:
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
                if attempt < 2:
                    time.sleep(self.retry_sleep_seconds * (attempt + 1))
                    continue
        raise RuntimeError(f"failed to fetch {url}") from last_error

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.html"

    def _read_fresh_cache(self, cache_path: Path) -> str | None:
        if not cache_path.exists():
            return None
        age = time.time() - cache_path.stat().st_mtime
        if age > self.cache_ttl_seconds:
            return None
        return cache_path.read_text(encoding="utf-8")
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_fetcher.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fel_dolby_vision_movies/fetcher.py tests/test_fetcher.py
git commit -m "feat: add cached HTTP fetcher"
```

## Task 5: Brave And Link-Based Source Discovery

**Files:**
- Create: `src/fel_dolby_vision_movies/discovery.py`
- Create: `tests/test_discovery.py`

- [ ] **Step 1: Write discovery tests**

Create `tests/test_discovery.py` with:

```python
import httpx
import respx

from fel_dolby_vision_movies.discovery import brave_search, extract_candidate_links


def test_extract_candidate_links_keeps_physical_media_candidates():
    html = """
    <a href="https://forum.blu-ray.com/showthread.php?t=123">Dolby Vision FEL thread</a>
    <a href="https://example.test/hardware">HDMI splitter FEL support</a>
    """
    assert extract_candidate_links(html, "https://forum.blu-ray.com/") == [
        "https://forum.blu-ray.com/showthread.php?t=123"
    ]


def test_brave_search_without_key_returns_empty_list():
    assert brave_search("Dolby Vision FEL Blu-ray forum", api_key=None) == []


@respx.mock
def test_brave_search_returns_result_urls():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"url": "https://forum.blu-ray.com/showthread.php?t=276448"},
                        {"url": "https://github.com/iammarxg/FEL"},
                    ]
                }
            },
        )
    )
    assert brave_search("Dolby Vision FEL Blu-ray forum", api_key="test") == [
        "https://forum.blu-ray.com/showthread.php?t=276448",
        "https://github.com/iammarxg/FEL",
    ]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_discovery.py -q
```

Expected: fails because `discovery.py` does not exist.

- [ ] **Step 3: Implement discovery**

Create `src/fel_dolby_vision_movies/discovery.py` with public functions:

```python
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup
import httpx


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DISCOVERY_TERMS = ("blu-ray", "uhd", "dolby vision", "profile 7", "fel", "forum")
BLOCKED_HINTS = ("hardware", "hdmi", "splitter", "tv-led", "player")


def extract_candidate_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = anchor.get_text(" ", strip=True).lower()
        href = urljoin(base_url, anchor["href"])
        haystack = f"{label} {href.lower()}"
        if any(blocked in haystack for blocked in BLOCKED_HINTS):
            continue
        if any(term in haystack for term in DISCOVERY_TERMS):
            urls.append(href)
    return list(dict.fromkeys(urls))


def brave_search(query: str, api_key: str | None) -> list[str]:
    if not api_key:
        return []
    response = httpx.get(
        BRAVE_ENDPOINT,
        params={"q": query, "count": 10},
        headers={"X-Subscription-Token": api_key},
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("web", {}).get("results", [])
    return [result["url"] for result in results if result.get("url")]
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_discovery.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fel_dolby_vision_movies/discovery.py tests/test_discovery.py
git commit -m "feat: add source discovery helpers"
```

## Task 6: Strict FEL Parser

**Files:**
- Create: `src/fel_dolby_vision_movies/parser.py`
- Create: `tests/test_parser.py`

- [ ] **Step 1: Write parser tests**

Create `tests/test_parser.py` with:

```python
from fel_dolby_vision_movies.parser import parse_fel_releases


def test_parses_table_row_with_direct_fel_correlation():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th><th>Audio</th></tr>
      <tr><td>The Matrix</td><td>Profile 7 FEL</td><td>English TrueHD Atmos</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["The Matrix"]
    assert releases[0].audio_formats == ["TrueHD Atmos"]
    assert releases[0].english_audio == "Yes"


def test_rejects_generic_fel_chatter_without_title_binding():
    html = """
    <p>I love FEL when discs include it.</p>
    <ul><li>The Matrix</li><li>Alien</li><li>Blade Runner</li></ul>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_parses_direct_sentence_with_title_and_profile_7_fel():
    html = "<p>Alien (1979) is confirmed as Dolby Vision Profile 7 FEL with DTS-HD MA.</p>"
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert releases[0].movie_title == "Alien"
    assert releases[0].release_date == "1979"
    assert releases[0].audio_formats == ["DTS-HD MA"]


def test_rejects_profile_7_without_fel():
    html = "<p>Movie A has Dolby Vision Profile 7 but this post does not identify FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_mel_even_when_fel_appears_elsewhere():
    html = "<p>Movie A is Profile 7 MEL. Another user asked about FEL-capable players.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_parser.py -q
```

Expected: fails because `parser.py` does not exist.

- [ ] **Step 3: Implement strict parser**

Create `src/fel_dolby_vision_movies/parser.py` with:

```python
from __future__ import annotations

from datetime import datetime, timezone
import re

from bs4 import BeautifulSoup

from .models import FelEvidence, FelRelease, UNKNOWN
from .normalize import normalize_audio, normalize_title


TITLE_SENTENCE_RE = re.compile(
    r"(?P<title>[A-Z][A-Za-z0-9:'’&.,!?\- ]{1,80}?)(?:\s+\((?P<year>\d{4})\))?"
    r"\s+(?:is|has|features|includes|confirmed as|confirmed to be).{0,120}?"
    r"(?:profile\s*7.{0,40}?fel|fel.{0,40}?profile\s*7|dolby vision.{0,40}?fel)",
    re.IGNORECASE,
)


def parse_fel_releases(html: str, source_url: str) -> list[FelRelease]:
    soup = BeautifulSoup(html, "html.parser")
    releases: list[FelRelease] = []
    releases.extend(_parse_tables(soup, source_url))
    releases.extend(_parse_sentences(soup.get_text("\n", strip=True), source_url))
    return _dedupe_releases(releases)


def _parse_tables(soup: BeautifulSoup, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        row_text = " ".join(cells)
        if not _has_direct_fel(row_text):
            continue
        title = normalize_title(cells[0])
        if not _looks_like_title(title):
            continue
        releases.append(_build_release(title, row_text, source_url, "table-row"))
    return releases


def _parse_sentences(text: str, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not _has_direct_fel(sentence):
            continue
        match = TITLE_SENTENCE_RE.search(sentence)
        if not match:
            continue
        title = normalize_title(match.group("title"))
        if not _looks_like_title(title):
            continue
        release = _build_release(title, sentence, source_url, "sentence")
        if match.group("year"):
            release.release_date = match.group("year")
        releases.append(release)
    return releases


def _has_direct_fel(text: str) -> bool:
    lowered = text.lower()
    if "mel" in lowered and "fel" not in lowered:
        return False
    return "fel" in lowered and ("profile 7" in lowered or "p7" in lowered or "dolby vision" in lowered)


def _looks_like_title(value: str) -> bool:
    lowered = value.lower()
    if not value or len(value) > 100:
        return False
    if any(word in lowered for word in ("hardware", "player", "splitter", "profile", "dolby vision")):
        return False
    return any(character.isalpha() for character in value)


def _build_release(title: str, evidence_text: str, source_url: str, evidence_type: str) -> FelRelease:
    release = FelRelease(
        movie_title=title,
        fel_evidence=FelEvidence(
            source_url=source_url,
            quote=evidence_text[:500],
            evidence_type=evidence_type,
        ),
        audio_formats=normalize_audio(evidence_text),
        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    if "english" in evidence_text.lower():
        release.english_audio = "Yes"
    return release


def _dedupe_releases(releases: list[FelRelease]) -> list[FelRelease]:
    seen: set[tuple[str, str]] = set()
    unique: list[FelRelease] = []
    for release in releases:
        key = (release.movie_title.lower(), release.source_url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(release)
    return unique
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_parser.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fel_dolby_vision_movies/parser.py tests/test_parser.py
git commit -m "feat: add strict FEL parser"
```

## Task 7: Artifact Generation

**Files:**
- Create: `src/fel_dolby_vision_movies/artifacts.py`
- Create: `tests/test_artifacts.py`

- [ ] **Step 1: Write artifact tests**

Create `tests/test_artifacts.py` with:

```python
import json
from pathlib import Path

from fel_dolby_vision_movies.artifacts import write_artifacts
from fel_dolby_vision_movies.models import FelEvidence, FelRelease


def release(title: str, date: str) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=date,
        studio="Unknown",
        audio_formats=["TrueHD Atmos"],
        english_audio="Yes",
        fel_evidence=FelEvidence(
            source_url=f"https://example.test/{title}",
            quote=f"{title} is Profile 7 FEL",
            evidence_type="fixture",
        ),
    )


def test_write_artifacts_sorts_known_dates_newest_first_unknown_last(tmp_path: Path):
    write_artifacts(
        [release("Unknown Date", "Unknown"), release("Newer", "2026-05-01"), release("Older", "2020")],
        output_dir=tmp_path,
    )
    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in data] == ["Newer", "Older", "Unknown Date"]
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "| Newer | Yes | 2026-05-01 | Unknown | TrueHD Atmos | Yes | Unknown |" in readme
    assert "Newer is Profile 7 FEL" not in readme


def test_links_contains_only_unique_source_urls(tmp_path: Path):
    write_artifacts([release("A", "2020"), release("A", "2020")], output_dir=tmp_path)
    links = (tmp_path / "links.md").read_text(encoding="utf-8")
    assert links.count("https://example.test/A") == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_artifacts.py -q
```

Expected: fails because `artifacts.py` does not exist.

- [ ] **Step 3: Implement artifact generation**

Create `src/fel_dolby_vision_movies/artifacts.py` with public function `write_artifacts(releases, output_dir=Path("."))` that:

```python
from __future__ import annotations

import json
from pathlib import Path

from .models import FelRelease, UNKNOWN


def write_artifacts(releases: list[FelRelease], output_dir: Path | str = ".") -> list[FelRelease]:
    root = Path(output_dir)
    sorted_releases = sorted(releases, key=_sort_key)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "releases.json").write_text(
        json.dumps([release.to_dict() for release in sorted_releases], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(_render_readme(sorted_releases), encoding="utf-8")
    (root / "links.md").write_text(_render_links(sorted_releases), encoding="utf-8")
    return sorted_releases


def _sort_key(release: FelRelease) -> tuple[int, str]:
    if release.release_date == UNKNOWN:
        return (1, "")
    return (0, _invert_date_text(release.release_date))


def _invert_date_text(value: str) -> str:
    return "".join(chr(255 - ord(character)) for character in value)


def _render_readme(releases: list[FelRelease]) -> str:
    lines = [
        "# FEL List",
        "",
        "Confirmed Dolby Vision Profile 7 FEL physical media releases.",
        "",
        "| Movie | FEL | Release Date | Studio | Audio | English Audio | Additional | Source |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for release in releases:
        additional = release.additional_characteristics or UNKNOWN
        if isinstance(additional, dict):
            additional = ", ".join(f"{key}: {value}" for key, value in additional.items()) or UNKNOWN
        lines.append(
            "| "
            + " | ".join(
                [
                    release.movie_title,
                    "Yes",
                    release.release_date,
                    release.studio,
                    ", ".join(release.audio_formats) or UNKNOWN,
                    release.english_audio,
                    str(additional),
                    f"[source]({release.source_url})",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _render_links(releases: list[FelRelease]) -> str:
    urls = list(dict.fromkeys(release.source_url for release in releases))
    lines = ["# Source Links", ""]
    lines.extend(f"- {url}" for url in urls)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run artifact tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_artifacts.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fel_dolby_vision_movies/artifacts.py tests/test_artifacts.py
git commit -m "feat: add generated artifact writer"
```

## Task 8: Static Dashboard Builder

**Files:**
- Create: `src/fel_dolby_vision_movies/dashboard.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write dashboard tests**

Create `tests/test_dashboard.py` with:

```python
from pathlib import Path

from fel_dolby_vision_movies.dashboard import build_dashboard
from fel_dolby_vision_movies.models import FelEvidence, FelRelease


def test_dashboard_writes_index_and_copied_json(tmp_path: Path):
    release = FelRelease(
        movie_title="The Matrix",
        release_date="1999",
        audio_formats=["TrueHD Atmos"],
        english_audio="Yes",
        fel_evidence=FelEvidence(
            source_url="https://example.test/thread",
            quote="The Matrix is Profile 7 FEL",
            evidence_type="fixture",
        ),
    )
    build_dashboard([release], output_dir=tmp_path / "dist")
    html = (tmp_path / "dist/index.html").read_text(encoding="utf-8")
    assert "The Matrix" in html
    assert "TrueHD Atmos" in html
    assert "Filter" in html
    assert "poster-placeholder" in html
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_dashboard.py -q
```

Expected: fails because `dashboard.py` does not exist.

- [ ] **Step 3: Implement dashboard**

Create `src/fel_dolby_vision_movies/dashboard.py` with:

```python
from __future__ import annotations

from html import escape
import json
from pathlib import Path

from .models import FelRelease, UNKNOWN


def build_dashboard(releases: list[FelRelease], output_dir: Path | str = "dist") -> None:
    dist = Path(output_dir)
    dist.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([release.to_dict() for release in releases], indent=2, ensure_ascii=False)
    cards = "\n".join(_render_card(release) for release in releases)
    (dist / "releases.json").write_text(payload + "\n", encoding="utf-8")
    (dist / "index.html").write_text(_render_html(cards, payload), encoding="utf-8")


def _render_card(release: FelRelease) -> str:
    audio_formats = release.audio_formats or [UNKNOWN]
    audio_badges = "".join(f'<span class="badge">{escape(audio)}</span>' for audio in audio_formats)
    search_text = " ".join(
        [
            release.movie_title,
            release.release_date,
            release.studio,
            release.english_audio,
            " ".join(audio_formats),
        ]
    ).lower()
    return f"""<article data-card data-search="{escape(search_text, quote=True)}">
  <div class="poster-placeholder">{escape(release.movie_title)}</div>
  <div class="body">
    <h2>{escape(release.movie_title)}</h2>
    <div class="meta">{escape(release.release_date)} · {escape(release.studio)}</div>
    <div class="badges">
      {audio_badges}
      <span class="badge">English: {escape(release.english_audio)}</span>
      <span class="badge">FEL</span>
    </div>
    <a href="{escape(release.source_url, quote=True)}" rel="noreferrer">Source</a>
  </div>
</article>"""


def _render_html(cards: str, payload: str) -> str:
    escaped_payload = escape(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FEL Dolby Vision Movies</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101214;
      --panel: #191d21;
      --text: #eef2f4;
      --muted: #aab4bd;
      --accent: #4dd3c9;
      --line: #2c343a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0; }}
    header {{ display: grid; gap: 16px; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: 34px; letter-spacing: 0; }}
    label {{ color: var(--muted); display: grid; gap: 8px; max-width: 420px; }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      background: #0c0e10;
      color: var(--text);
      border-radius: 8px;
      padding: 11px 12px;
      font: inherit;
    }}
    #cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }}
    article {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      overflow: hidden;
      min-height: 100%;
    }}
    .poster-placeholder {{
      aspect-ratio: 2 / 3;
      display: grid;
      place-items: center;
      background: #222a30;
      color: var(--muted);
      font-weight: 700;
      text-align: center;
      padding: 16px;
    }}
    .body {{ padding: 14px; display: grid; gap: 10px; }}
    h2 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .badge {{
      border: 1px solid rgba(77, 211, 201, .45);
      background: rgba(77, 211, 201, .12);
      color: var(--text);
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      white-space: nowrap;
    }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>FEL Dolby Vision Movies</h1>
      <label>Filter <input id="filter" type="search" placeholder="Title, studio, audio"></label>
    </header>
    <section id="cards">{cards}</section>
  </main>
  <script type="application/json" id="release-data">{escaped_payload}</script>
  <script>
    const filter = document.getElementById("filter");
    filter.addEventListener("input", () => {{
      const query = filter.value.trim().toLowerCase();
      document.querySelectorAll("[data-card]").forEach(card => {{
        card.hidden = query && !card.dataset.search.includes(query);
      }});
    }});
  </script>
</body>
</html>
"""
```

- [ ] **Step 4: Run dashboard tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_dashboard.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fel_dolby_vision_movies/dashboard.py tests/test_dashboard.py
git commit -m "feat: add static dashboard builder"
```

## Task 9: CLI Orchestration

**Files:**
- Create: `src/fel_dolby_vision_movies/main.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_main.py` with:

```python
from pathlib import Path

from fel_dolby_vision_movies.main import main


def test_search_for_sources_runs_without_brave_key(tmp_path: Path, monkeypatch):
    sources = tmp_path / "forums.txt"
    sources.write_text("https://example.test/thread\n", encoding="utf-8")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert main(["search-for-sources", "--sources", str(sources), "--output-dir", str(tmp_path)]) == 0


def test_scrape_for_titles_handles_empty_sources(tmp_path: Path):
    sources = tmp_path / "forums.txt"
    sources.write_text("", encoding="utf-8")
    assert main(["scrape-for-titles", "--sources", str(sources), "--output-dir", str(tmp_path)]) == 0
    assert (tmp_path / "data/releases.json").exists()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_main.py -q
```

Expected: fails because `main.py` does not exist.

- [ ] **Step 3: Implement CLI**

Create `src/fel_dolby_vision_movies/main.py` with:

```python
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from .artifacts import write_artifacts
from .dashboard import build_dashboard
from .discovery import brave_search, extract_candidate_links
from .fetcher import Fetcher
from .parser import parse_fel_releases
from .sources import merge_confirmed_sources, read_source_urls


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fel-dolby-vision-movies")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("search-for-sources", "scrape-for-titles", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--sources", default="forums.txt")
        command.add_argument("--output-dir", default=".")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    source_path = Path(args.sources)
    output_dir = Path(args.output_dir)
    urls = read_source_urls(source_path)
    fetcher = Fetcher(cookie_header=os.getenv("FORUM_COOKIE_HEADER"))

    if args.command == "search-for-sources":
        discovered = _discover(urls, fetcher)
        confirmed = _scrape(discovered, fetcher)
        merge_confirmed_sources(source_path, [release.source_url for release in confirmed])
        print(f"discovered {len(set(discovered))} candidate sources")
        print(f"confirmed {len(confirmed)} FEL releases from discovered sources")
        return 0

    if args.command == "run":
        discovered = _discover(urls, fetcher)
        urls = list(dict.fromkeys([*urls, *discovered]))

    releases = _scrape(urls, fetcher)
    confirmed_urls = [release.source_url for release in releases]
    merge_confirmed_sources(source_path, confirmed_urls)
    sorted_releases = write_artifacts(releases, output_dir=output_dir)
    build_dashboard(sorted_releases, output_dir=output_dir / "dist")
    print(f"published {len(sorted_releases)} confirmed FEL releases")
    return 0


def _discover(urls: list[str], fetcher: Fetcher) -> list[str]:
    discovered: list[str] = []
    for url in urls:
        try:
            result = fetcher.fetch(url)
        except RuntimeError as exc:
            print(exc)
            continue
        discovered.extend(extract_candidate_links(result.text, url))
    discovered.extend(
        brave_search(
            "Dolby Vision Profile 7 FEL Blu-ray forum",
            api_key=os.getenv("BRAVE_SEARCH_API_KEY"),
        )
    )
    return list(dict.fromkeys(discovered))


def _scrape(urls: list[str], fetcher: Fetcher):
    releases = []
    for url in urls:
        try:
            result = fetcher.fetch(url)
        except RuntimeError as exc:
            print(exc)
            continue
        releases.extend(parse_fel_releases(result.text, url))
    return releases


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_main.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Verify just commands are wired**

Run:

```bash
just search-for-sources
```

Expected: command exits 0 and prints a discovered candidate count, even if the count is 0.

- [ ] **Step 6: Commit**

```bash
git add src/fel_dolby_vision_movies/main.py tests/test_main.py justfile
git commit -m "feat: add scraper CLI orchestration"
```

## Task 10: Deterministic Benchmark

**Files:**
- Create: `tests/fixtures/benchmark_cases.json`
- Create: `src/fel_dolby_vision_movies/benchmark.py`
- Create: `tests/test_benchmark.py`

- [ ] **Step 1: Create benchmark fixture**

Create `tests/fixtures/benchmark_cases.json` with:

```json
[
  {
    "name": "valid_table_fel",
    "html": "<table><tr><td>The Matrix</td><td>Dolby Vision Profile 7 FEL</td><td>English TrueHD Atmos</td></tr></table>",
    "expected_titles": ["The Matrix"]
  },
  {
    "name": "generic_fel_chatter",
    "html": "<p>I love FEL discs.</p><ul><li>The Matrix</li><li>Alien</li></ul>",
    "expected_titles": []
  },
  {
    "name": "profile_7_without_fel",
    "html": "<p>Movie A has Profile 7 metadata.</p>",
    "expected_titles": []
  },
  {
    "name": "direct_sentence",
    "html": "<p>Alien (1979) is confirmed as Dolby Vision Profile 7 FEL with DTS-HD Master Audio.</p>",
    "expected_titles": ["Alien"]
  }
]
```

- [ ] **Step 2: Write benchmark test**

Create `tests/test_benchmark.py` with:

```python
from pathlib import Path

from fel_dolby_vision_movies.benchmark import run_benchmark


def test_benchmark_fixture_passes():
    result = run_benchmark(Path("tests/fixtures/benchmark_cases.json"))
    assert result["false_positives"] == 0
    assert result["false_negatives"] == 0
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_benchmark.py -q
```

Expected: fails because `benchmark.py` does not exist.

- [ ] **Step 4: Implement benchmark**

Create `src/fel_dolby_vision_movies/benchmark.py` with:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .parser import parse_fel_releases


def run_benchmark(path: Path) -> dict[str, float | int]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    false_positives = 0
    false_negatives = 0
    true_positives = 0

    for case in cases:
        expected = set(case["expected_titles"])
        actual = {release.movie_title for release in parse_fel_releases(case["html"], f"fixture:{case['name']}")}
        false_positives += len(actual - expected)
        false_negatives += len(expected - actual)
        true_positives += len(actual & expected)

    precision = true_positives / (true_positives + false_positives) if true_positives + false_positives else 1.0
    recall = true_positives / (true_positives + false_negatives) if true_positives + false_negatives else 1.0
    return {
        "precision": precision,
        "recall": recall,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture")
    args = parser.parse_args()
    result = run_benchmark(Path(args.fixture))
    print(json.dumps(result, indent=2))
    if result["false_positives"] or result["false_negatives"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run benchmark**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt python -m fel_dolby_vision_movies.benchmark tests/fixtures/benchmark_cases.json
```

Expected: exits 0 and prints precision `1.0`, recall `1.0`, zero false positives, and zero false negatives.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/benchmark_cases.json src/fel_dolby_vision_movies/benchmark.py tests/test_benchmark.py
git commit -m "test: add deterministic parser benchmark"
```

## Task 11: Production GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/daily-run.yml`
- Modify: `.github/workflows/secret-smoke.yml`

- [ ] **Step 1: Create production workflow**

Create `.github/workflows/daily-run.yml` with:

```yaml
name: Daily FEL Pipeline

on:
  schedule:
    - cron: "0 8 * * *"
  workflow_dispatch:

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  scrape-and-deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Setup uv
        uses: astral-sh/setup-uv@v6

      - name: Setup just
        uses: taiki-e/install-action@v2
        with:
          tool: just

      - name: Install dependencies
        run: uv pip install --system -r requirements-dev.txt

      - name: Run validation
        run: just ci

      - name: Run scraper
        env:
          BRAVE_SEARCH_API_KEY: ${{ secrets.BRAVE_SEARCH_API_KEY }}
          FORUM_COOKIE_HEADER: ${{ secrets.FORUM_COOKIE_HEADER }}
        run: just run

      - name: Commit generated artifacts
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add README.md links.md data/releases.json forums.txt
          if git diff --cached --quiet; then
            echo "No generated changes"
          else
            git commit -m "chore: update FEL release artifacts [skip ci]"
            git push
          fi

      - name: Configure Pages
        uses: actions/configure-pages@v5

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: dist

      - name: Deploy Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Keep or remove smoke workflow**

Keep `.github/workflows/secret-smoke.yml` for manual verification. Do not make production depend on it.

- [ ] **Step 3: Validate YAML**

Run:

```bash
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/daily-run.yml"); puts "yaml ok"'
```

Expected: prints `yaml ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily-run.yml .github/workflows/secret-smoke.yml
git commit -m "ci: add daily FEL pipeline workflow"
```

## Task 12: Full Local Validation And Live Seed Run

**Files:**
- Modify generated outputs: `data/releases.json`, `README.md`, `links.md`
- Create local-only Pages build output: `dist/index.html`

- [ ] **Step 1: Install and run full validation**

Run:

```bash
uv pip install --system -r requirements-dev.txt
just ci
```

Expected: ruff passes, pytest passes, benchmark exits 0.

- [ ] **Step 2: Run seeded live scrape**

Run:

```bash
just scrape-for-titles
```

Expected: command exits 0, fetches the three seeded sources in `forums.txt`, writes cache snapshots under `.cache/`, and generates `data/releases.json`, `README.md`, `links.md`, and `dist/index.html`.

- [ ] **Step 3: Inspect generated outputs**

Run:

```bash
python -m json.tool data/releases.json >/tmp/releases-json-ok
sed -n '1,80p' README.md
sed -n '1,80p' links.md
test -f dist/index.html
```

Expected: JSON parses, Markdown exists, dashboard exists, release rows do not include release groups, and ambiguous non-FEL titles are absent.

- [ ] **Step 4: Check ignored files**

Run:

```bash
git status --short --ignored
```

Expected: `.env`, `.cache/`, and `.superpowers/` are ignored; generated public artifacts are visible if changed.

- [ ] **Step 5: Commit generated implementation outputs**

```bash
git add README.md links.md data/releases.json forums.txt
git commit -m "chore: publish initial FEL artifacts"
```

## Task 13: Push And Remote Verification

**Files:**
- No source file changes unless validation finds an issue.

- [ ] **Step 1: Push commits**

Run:

```bash
git push
```

Expected: pushes all implementation commits to `origin/main`.

- [ ] **Step 2: Trigger workflow**

Run:

```bash
gh workflow run daily-run.yml --repo Appz4Fun/fel-dolby-vision-movies --ref main
```

Expected: command prints a run URL or exits 0.

- [ ] **Step 3: Watch workflow**

Run:

```bash
gh run list --repo Appz4Fun/fel-dolby-vision-movies --workflow daily-run.yml --limit 1
```

Use the numeric database id printed by `gh run list`:

```bash
gh run watch 123456789 --repo Appz4Fun/fel-dolby-vision-movies --exit-status
```

Expected: workflow completes successfully. If it fails, inspect logs, fix the specific failing task, rerun `just ci`, commit, push, and rerun the workflow.

## Self-Review Checklist

- Spec coverage:
  - repo bootstrap: Tasks 1 and 11
  - source discovery and `forums.txt`: Tasks 3, 5, 9, 11
  - live fetching/cache/cookies: Task 4
  - strict FEL parser: Task 6
  - normalized audio: Task 2
  - generated data/Markdown: Task 7
  - dashboard/Pages: Tasks 8 and 11
  - deterministic benchmark/no live AI: Task 10
  - full validation and push: Tasks 12 and 13
- Placeholder scan: no `TBD`, no incomplete sections, no references to undefined public entrypoints.
- Type consistency: package module is `fel_dolby_vision_movies`; source registry is `forums.txt`; canonical generated data is `data/releases.json`.
