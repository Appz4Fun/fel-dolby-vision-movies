from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import httpx


BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DISCOVERY_TERMS = ("blu-ray", "uhd", "dolby vision", "profile 7", "fel", "remux")
BLOCKED_HINTS = ("hardware", "hdmi", "splitter", "tv-led", "player")


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
    urls = [result["url"] for result in results if result.get("url")]
    return list(dict.fromkeys(urls))
