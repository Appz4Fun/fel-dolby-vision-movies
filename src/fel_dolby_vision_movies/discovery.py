from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunsplit

from bs4 import BeautifulSoup
import httpx


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DISCOVERY_TERMS = ("blu-ray", "uhd", "dolby vision", "profile 7", "fel", "remux")
BLOCKED_HINTS = (
    "1337x",
    "download",
    "hardware",
    "hdmi",
    "magnet",
    "mega.nz",
    "nzb",
    "player",
    "rapidgator",
    "splitter",
    "player support",
    "playback",
    "plex",
    "torrent",
    "tv-led",
    "usenet",
    "warez",
    "yts",
)
SOURCE_HOST_HINTS = (
    "blu-ray.com",
    "caps-a-holic.com",
    "criterionforum.org",
    "discourse.coreelec.org",
    "forum",
    "github.com",
    "google.com",
    "list",
    "makemkv.com",
    "reddit.com",
    "wiki",
)
BLOCKED_HOSTS = ("simkl.com", "trakt.tv")
SOURCE_SEARCH_QUERIES = (
    '"Dolby Vision" "Profile 7" FEL "Blu-ray"',
    '"Profile 7 FEL" "UHD Blu-ray" list',
    '"Dolby Vision FEL" "UHD Blu-ray" forum',
    'site:forum.blu-ray.com "Dolby Vision" "Profile 7" FEL',
    'site:reddit.com "Dolby Vision" "Profile 7" FEL',
    'site:github.com "Dolby Vision" FEL "Blu-ray"',
)


@dataclass(frozen=True)
class SourceDiscoveryResult:
    brave_available: bool
    queries: list[str]
    raw_url_count: int
    rejected_url_count: int
    candidate_urls: list[str]
    errors: list[str]


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str = ""
    description: str = ""


def build_source_search_queries() -> list[str]:
    return list(SOURCE_SEARCH_QUERIES)


def extract_candidate_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = anchor.get_text(" ", strip=True).lower()
        href = urljoin(base_url, anchor["href"])
        parsed = urlparse(href)
        url_terms = f"{parsed.path} {parsed.params} {parsed.query} {parsed.fragment}".lower()
        haystack = f"{label} {url_terms}"
        if any(blocked in haystack for blocked in BLOCKED_HINTS):
            continue
        if any(term in haystack for term in DISCOVERY_TERMS):
            urls.append(href)
    return list(dict.fromkeys(urls))


def brave_search(query: str, api_key: str | None) -> list[str]:
    return [result.url for result in brave_search_results(query, api_key)]


def brave_search_results(query: str, api_key: str | None) -> list[SearchResult]:
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
    deduped: dict[str, SearchResult] = {}
    for result in results:
        if not result.get("url"):
            continue
        normalized = _normalize_url(result["url"])
        if not normalized or normalized in deduped:
            continue
        deduped[normalized] = SearchResult(
            url=normalized,
            title=result.get("title", ""),
            description=result.get("description", ""),
        )
    return list(deduped.values())


def filter_candidate_source_urls(urls: Iterable[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = _normalize_url(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if _is_candidate_source_url(normalized):
            candidates.append(normalized)
    return candidates


def discover_source_candidates(
    api_key: str | None,
    *,
    search: Callable[[str, str], list[str] | list[SearchResult]] | None = None,
    queries: Iterable[str] | None = None,
) -> SourceDiscoveryResult:
    query_list = list(queries) if queries is not None else build_source_search_queries()
    if not api_key:
        return SourceDiscoveryResult(
            brave_available=False,
            queries=query_list,
            raw_url_count=0,
            rejected_url_count=0,
            candidate_urls=[],
            errors=[],
        )

    search_func = search or brave_search_results
    raw_results: list[SearchResult] = []
    errors: list[str] = []
    for query in query_list:
        try:
            raw_results.extend(_coerce_search_results(search_func(query, api_key)))
        except httpx.HTTPError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    deduped_results: dict[str, SearchResult] = {}
    for result in raw_results:
        normalized = _normalize_url(result.url)
        if normalized and normalized not in deduped_results:
            deduped_results[normalized] = SearchResult(
                url=normalized,
                title=result.title,
                description=result.description,
            )

    candidate_urls = _filter_candidate_search_results(deduped_results.values())
    return SourceDiscoveryResult(
        brave_available=True,
        queries=query_list,
        raw_url_count=len(deduped_results),
        rejected_url_count=len(deduped_results) - len(candidate_urls),
        candidate_urls=candidate_urls,
        errors=errors,
    )


def _normalize_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.query,
            "",
        )
    )


def _is_candidate_source_url(url: str) -> bool:
    parsed = urlparse(url)
    return _is_candidate_source(parsed, "")


def _filter_candidate_search_results(results: Iterable[SearchResult]) -> list[str]:
    candidates: list[str] = []
    for result in results:
        parsed = urlparse(result.url)
        metadata = f"{result.title} {result.description}"
        if _is_candidate_source(parsed, metadata):
            candidates.append(result.url)
    return candidates


def _is_candidate_source(parsed, metadata: str) -> bool:
    if any(parsed.netloc.lower().endswith(host) for host in BLOCKED_HOSTS):
        return False
    haystack = (
        f"{parsed.netloc} {parsed.path} {parsed.query} {metadata}"
        .replace("-", " ")
        .lower()
    )
    if any(blocked in haystack for blocked in BLOCKED_HINTS):
        return False

    has_source_shape = any(hint in parsed.netloc.lower() for hint in SOURCE_HOST_HINTS)
    has_source_shape = has_source_shape or any(
        hint in parsed.path.lower() for hint in ("/forum", "/thread", "/list", "/wiki")
    )
    has_profile_fel = "profile 7" in haystack and "fel" in haystack
    has_dolby_blu_ray = "dolby vision" in haystack and (
        "blu ray" in haystack or "uhd" in haystack or "remux" in haystack
    )
    return has_source_shape and (has_profile_fel or has_dolby_blu_ray)


def _coerce_search_results(
    values: list[str] | list[SearchResult],
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for value in values:
        if isinstance(value, SearchResult):
            results.append(value)
        else:
            results.append(SearchResult(url=value))
    return results
